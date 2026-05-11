import asyncio
import os
import re
import json
import spacy
from spacy.matcher import PhraseMatcher
import yaml
import httpx
import hashlib
import signal
import sys
import time
from datetime import datetime
from rapidfuzz import process, fuzz
from tqdm import tqdm
from opencc import OpenCC
from pathlib import Path

from rbrain_config import get_config

# --- 核心配置（見 rbrain_config.py / rbrain.yaml） ---
_cfg = get_config()
OLLAMA_API = _cfg["ollama_generate_url"]
MODEL_NAME = _cfg["generate_model"]
# Smaller num_predict = faster generation; batch sizes bump the cap (see _post_ollama callers).
OLLAMA_OPTIONS = {"temperature": 0.1}
INPUT_DIR = _cfg["raw_dir"]
ATOMS_DIR = _cfg["atoms_dir"]
LOG_FILE = os.environ.get(
    "RBRAIN_ATOMS_LOG", str(Path(__file__).resolve().parent / "atoms_process_log.json")
)

# Performance tuning (env): SPACY_BATCH, ATOMIZER_MIN_PARAGRAPH_CHARS, ATOMIZER_HEADING_MIN_LEVEL,
# ATOMIZER_CONTEXT_CHARS, ATOMIZER_MAX_ENTITIES_PER_PARAGRAPH, ATOMIZER_MAX_NOUN_CHARS,
# ATOMIZER_MAX_BLOCK_CHARS, ATOMIZER_SLICE_CHARS, OLLAMA_NUM_PREDICT*, etc.


class WikiAtomizer:
    def __init__(self):
        self.wiki_dir = ATOMS_DIR
        if not os.path.exists(self.wiki_dir): os.makedirs(self.wiki_dir)
        
        self.cc = OpenCC('s2t')
        self.themes = ["self", "work", "relationship", "action"]
        self.emotion_triggers = {
            "zh": [
                "生氣", "憤怒", "失望", "沮喪", "悲傷", "難過", "害怕", "焦慮", "緊張", "不安",
                "安心", "高興", "開心", "興奮", "感動", "疲憊", "無力", "挫折", "壓力"
            ],
            "en": [
                "angry", "frustrated", "disappointed", "sad", "afraid", "scared", "anxious",
                "nervous", "uneasy", "happy", "excited", "moved", "tired", "exhausted",
                "stressed", "pressured"
            ],
        }
        self.decision_keywords = {
            "zh": ["決定", "選擇", "抉擇", "判斷", "是否", "要不要", "決策", "計畫", "方向"],
            "en": ["decide", "decision", "choose", "choice", "determine", "plan", "direction"]
        }
        self.problem_keywords = {
            "zh": ["問題", "挑戰", "困難", "卡住", "卡關", "麻煩", "瓶頸", "風險", "痛點"],
            "en": ["problem", "issue", "challenge", "difficulty", "stuck", "risk", "bottleneck"]
        }
        self.learning_keywords = {
            "zh": ["學習", "吸收", "反思", "教訓", "成長", "發現", "體會", "經驗"],
            "en": ["learn", "learning", "lesson", "reflect", "reflection", "realize", "insight", "experience"]
        }
        self.perspective_keywords = {
            "self": ["我", "自己", "我的", "自我", "內在", "個人", "心情", "感受"],
            "other": ["你", "他", "她", "他們", "朋友", "同事", "家人", "對方", "主管", "客戶"],
            "society": ["社會", "世界", "城市", "制度", "政策", "文化", "公眾", "社區", "團隊"]
        }
        # --- 優雅中斷信號處理 ---
        self.stop_requested = False
        signal.signal(signal.SIGINT, self._handle_interrupt)
        
        # 定義停用實體列表，過濾高頻但無意義的噪音 Atoms
        self.stop_entities = {
            "時候", "事情", "東西", "部分", "問題", "其中", "一些", "現在", "今天", "大家", "自己",
            "一個", "一種", "一樣", "意思", "方法", "過程", "情況", "結果", "方面", "理由", "點子",
            "甚至", "所以", "但是", "而且", "不過", "因此", "就是", "還是", "什麼", "為什麼", "如何",
            "如果", "雖然", "可是", "然後", "那麼", "這會", "屬於", "其列", "等於", "其煩", "比起", 
            "乃是", "不見", "人困惑", "something", "things", "everything", "anything", "people",
            "person", "someone", "anyone", "everyone", "nothing", "nobody", "anybody", "somebody"
        }

        print("⏳ 正在加載 NLP 模型 (zh/en)...")
        try:
            # 更激進地禁用組件：attribute_ruler 和 lemmatizer 在不需要還原詞幹的場景下可關閉
            self.nlp_zh = spacy.load("zh_core_web_sm", disable=["parser", "attribute_ruler", "lemmatizer"])
            self.nlp_en = spacy.load("en_core_web_sm", disable=["parser", "attribute_ruler", "lemmatizer"])
            print("✅ NLP 模型加載成功")
        except Exception as e:
            print(f"❌ 模型加載失敗: {e}\n提示: 請確保已執行 python -m spacy download zh_core_web_sm")
            exit(1)

        # 初始化 PhraseMatcher
        self.matcher_zh = PhraseMatcher(self.nlp_zh.vocab, attr="TEXT")
        self.matcher_en = PhraseMatcher(self.nlp_en.vocab, attr="TEXT")
        self._init_phrase_matchers()

        # 保留 regex 作為備用（或針對非 Token 化的匹配）
        self._patterns = self._init_keyword_patterns()

        self.entity_cache = [f[:-3] for f in os.listdir(self.wiki_dir) if f.endswith('.md')]
        self.entity_cache_set = set(self.entity_cache)
        self._normalization_map = {}
        self.processed_log = self._load_log()
        self.http = httpx.Client(timeout=int(os.environ.get("OLLAMA_TIMEOUT", "480")))
        self._current_loop = None
        self._current_tasks = []
        self._current_client = None
        self._network_concurrency = int(os.environ.get("ATOMIZER_NETWORK_CONCURRENCY", "4"))
        self._spacy_batch = int(os.environ.get("SPACY_BATCH", "64"))
        # Increase batch thresholds to reduce API calls
        self._max_batch_size = int(os.environ.get("ATOMIZER_MAX_BATCH_SIZE", "16"))
        # Skip very short slabs (碎碎念); default ~「十幾個字」才當一段處理
        self._min_paragraph_chars = int(os.environ.get("ATOMIZER_MIN_PARAGRAPH_CHARS", "12"))
        self._ctx_chars = int(os.environ.get("ATOMIZER_CONTEXT_CHARS", "900"))
        self._max_entities_para = int(os.environ.get("ATOMIZER_MAX_ENTITIES_PER_PARAGRAPH", "24"))
        self._max_noun_run = int(os.environ.get("ATOMIZER_MAX_NOUN_CHARS", "48"))
        # Long notes: if one \\n\\n block exceeds this, subdivide (see paragraphs6_from_content).
        self._max_block_chars = int(os.environ.get("ATOMIZER_MAX_BLOCK_CHARS", "6000"))
        # Only used when a *single line* (or newline-free slab) exceeds _max_block_chars.
        self._slice_chars = int(os.environ.get("ATOMIZER_SLICE_CHARS", "2200"))
        # Split long notes at ATX headings: e.g. 4 = #### / ##### / ###### only. 0 = disable.
        self._heading_min_level = int(os.environ.get("ATOMIZER_HEADING_MIN_LEVEL", "4"))

    def _init_phrase_matchers(self):
        """將關鍵字列表預先轉換為模式並註冊到 Matcher 中"""
        groups = {
            "EMOTION": self.emotion_triggers,
            "DECISION": self.decision_keywords,
            "PROBLEM": self.problem_keywords,
            "LEARNING": self.learning_keywords
        }
        
        for label, lang_map in groups.items():
            # ZH patterns
            zh_patterns = list(self.nlp_zh.pipe(lang_map["zh"]))
            self.matcher_zh.add(label, zh_patterns)
            
            # EN patterns
            en_patterns = list(self.nlp_en.pipe(lang_map["en"]))
            self.matcher_en.add(label, en_patterns)

    def _init_keyword_patterns(self):
        p = {}
        p["emotion"] = re.compile(r"\b(" + "|".join(re.escape(k) for k in self.emotion_triggers["en"]) + r")\b", re.I)
        p["decision"] = re.compile(r"\b(" + "|".join(re.escape(k) for k in self.decision_keywords["en"]) + r")\b", re.I)
        p["problem"] = re.compile(r"\b(" + "|".join(re.escape(k) for k in self.problem_keywords["en"]) + r")\b", re.I)
        p["learning"] = re.compile(r"\b(" + "|".join(re.escape(k) for k in self.learning_keywords["en"]) + r")\b", re.I)
        return p

    def _handle_interrupt(self, signum, frame):
        print("\n\n🛑 [INTERRUPT] 接收到中斷請求，正在保存當前進度並安全退出...")
        self.stop_requested = True
        if self._current_loop is not None:
            try:
                self._current_loop.call_soon_threadsafe(self._cancel_current_tasks)
                self._current_loop.call_soon_threadsafe(self._close_current_client)
            except Exception:
                pass

    def _load_log(self):
        if os.path.exists(LOG_FILE):
            try:
                with open(LOG_FILE, 'r', encoding='utf-8') as f: return json.load(f)
            except: return {}
        return {}

    def _save_log(self):
        """原子化保存：先寫入臨時文件再替換，防止寫入時中斷導致損壞"""
        temp_log = LOG_FILE + ".tmp"
        with open(temp_log, 'w', encoding='utf-8') as f:
            json.dump(self.processed_log, f, indent=4, ensure_ascii=False)
        os.replace(temp_log, LOG_FILE)

    def _cancel_current_tasks(self):
        for task in self._current_tasks:
            if not task.done():
                task.cancel()

    def _close_current_client(self):
        client = self._current_client
        if client is not None:
            self._current_client = None
            asyncio.create_task(client.aclose())

    def _run_async(self, coro):
        loop = asyncio.new_event_loop()
        try:
            self._current_loop = loop
            return loop.run_until_complete(coro)
        finally:
            self._current_loop = None
            loop.close()

    def get_file_hash(self, filepath):
        hasher = hashlib.md5()
        with open(filepath, 'rb') as f:
            hasher.update(f.read())
        return hasher.hexdigest()

    def get_normalized_name(self, name):
        """實體名歸一化 + 繁簡轉換"""
        raw_name = name.strip()
        # 1. 檢查本次運行的 Session 緩存
        if raw_name in self._normalization_map:
            return self._normalization_map[raw_name]

        # 2. 檢查現有實體庫是否存在精確匹配
        if raw_name in self.entity_cache_set:
            self._normalization_map[raw_name] = raw_name
            return raw_name

        # 3. 檢查繁簡轉換後的精確匹配
        t_name = self.cc.convert(raw_name)
        if t_name in self.entity_cache_set:
            self._normalization_map[raw_name] = t_name
            return t_name

        if not self.entity_cache:
            return t_name

        # 4. 模糊匹配 (使用 RapidFuzz 提升速度)
        # RapidFuzz 返回 (match, score, index)，thefuzz 返回 (match, score)
        extracted = process.extractOne(t_name, self.entity_cache, scorer=fuzz.token_set_ratio)
        result = extracted[0] if (extracted and extracted[1] >= 90) else t_name
        self._normalization_map[raw_name] = result
        return result

    def _entities_from_doc(self, doc, return_matches=False):
        """結合模型 NER 與規則匹配提取實體"""
        is_zh = doc.lang_ == "zh"
        found = set()
        keyword_matches = []

        # 0. PhraseMatcher 提取預定義關鍵字 (極速)
        matcher = self.matcher_zh if is_zh else self.matcher_en
        matches = matcher(doc)
        for match_id, start, end in matches:
            label = doc.vocab.strings[match_id]
            text = doc[start:end].text
            found.add((text, f"KEYWORD_{label}"))
            keyword_matches.append((text, label))
        
        # 1. 模型 NER 提取
        for ent in doc.ents:
            if ent.label_ not in ["TIME",  "QUANTITY", "ORDINAL", "CARDINAL"]:
                t = ent.text.strip()
                if len(t) > 1 and t not in self.stop_entities:
                    found.add((t, ent.label_))
        # 2. 強制關鍵字提取 (例如：一定要抓取的決策、問題、情緒，學習關鍵字)
        text_content = doc.text
        for kw in self.decision_keywords["zh" if is_zh else "en"]:
            if kw in text_content:
                found.add((kw, "KEYWORD_DECISION"))

        # 3. 複合名詞 (Concept) 提取邏輯優化
        max_run = self._max_noun_run
        current_noun = ""
        for token in doc:
            if token.pos_ in ["NOUN", "PROPN"]:
                piece = token.text
                if current_noun:
                    # 英文概念保留空格，中文不加空格
                    sep = " " if not is_zh else ""
                    if len(current_noun) + len(sep) + len(piece) > max_run:
                        if len(current_noun) > 1 and current_noun not in self.stop_entities:
                            found.add((current_noun, "CONCEPT"))
                        current_noun = piece
                    else:
                        current_noun += sep + piece
                else:
                    current_noun = piece
            else:
                if len(current_noun) > 1 and current_noun not in self.stop_entities:
                    found.add((current_noun, "CONCEPT"))
                current_noun = ""
        if len(current_noun) > 1 and current_noun not in self.stop_entities:
            found.add((current_noun, "CONCEPT"))
        
        return list(found)

    def _find_keywords(self, text, keywords, pattern_en=None):
        is_zh = any('\u4e00' <= c <= '\u9fff' for c in text)
        if is_zh:
            return [kw for kw in keywords if kw in text]
        elif pattern_en:
            return pattern_en.findall(text) 
        return []
        
    def detect_emotion_triggers(self, text):
        is_zh = any('\u4e00' <= c <= '\u9fff' for c in text)
        keywords = self.emotion_triggers["zh"] if is_zh else self.emotion_triggers["en"]
        return self._find_keywords(text, keywords, self._patterns.get("emotion"))

    def detect_classification(self, text):
        is_zh = any('\u4e00' <= c <= '\u9fff' for c in text)
            
        classification = []
        if self._find_keywords(text, self.decision_keywords["zh" if is_zh else "en"], self._patterns.get("decision")):
            classification.append("decision")
        if self._find_keywords(text, self.problem_keywords["zh" if is_zh else "en"], self._patterns.get("problem")):
            classification.append("problem")
        if self._find_keywords(text, self.learning_keywords["zh" if is_zh else "en"], self._patterns.get("learning")):
            classification.append("learning")
        return classification

    def detect_perspective(self, text):
        counts = {}
        for perspective, keywords in self.perspective_keywords.items():
            counts[perspective] = len(self._find_keywords(text, keywords))
        best = max(counts, key=counts.get)
        return best if counts[best] > 0 else None

    def detect_attributes(self, text):
        return {
            "emotion_triggers": self.detect_emotion_triggers(text),
            "classification": self.detect_classification(text),
            "perspective": self.detect_perspective(text),
        }

    def _merge_local_attributes(self, audit_data, text):
        if audit_data is None:
            audit_data = {}
        attrs = self.detect_attributes(text)
        if not audit_data.get("perspective") and attrs.get("perspective"):
            audit_data["perspective"] = attrs["perspective"]
        if not audit_data.get("classification"):
            audit_data["classification"] = attrs.get("classification", [])
        if not audit_data.get("emotion_triggers"):
            audit_data["emotion_triggers"] = attrs.get("emotion_triggers", [])
        # 如果沒有logic，嘗試從insight生成簡單的logic
        if not audit_data.get("logic") and audit_data.get("insight"):
            insight = audit_data["insight"]
            # 簡單的logic生成：基於insight添加root cause提示
            audit_data["logic"] = f"Based on the insight: {insight} Root cause analysis not fully determined by AI."
        # 如果沒有related，嘗試從文本中提取實體作為相關
        if not audit_data.get("related"):
            entities = self.extract_entities(text)
            # 選擇最多3個不同於當前實體的實體作為相關
            current_entity = audit_data.get("entity") or ""
            related_candidates = [e[0] for e in entities if e[0] != current_entity][:3]
            if related_candidates:
                audit_data["related"] = related_candidates
        return audit_data

    def extract_entities(self, text):
        is_zh = any('\u4e00' <= c <= '\u9fff' for c in text)
        doc = self.nlp_zh(text) if is_zh else self.nlp_en(text)
        return self._entities_from_doc(doc)

    def extract_entities_batch(self, texts):
        """One nlp.pipe batch per language — much faster than per-paragraph parse on long notes."""
        if not texts:
            return []
        zh_mask = [any("\u4e00" <= c <= "\u9fff" for c in t) for t in texts]
        out = [[] for _ in texts]
        zh_items = [(i, texts[i]) for i, z in enumerate(zh_mask) if z]
        en_items = [(i, texts[i]) for i, z in enumerate(zh_mask) if not z]
        bs = self._spacy_batch
        for indices, nlp in ((zh_items, self.nlp_zh), (en_items, self.nlp_en)):
            if not indices:
                continue
            idxs = [i for i, _ in indices]
            for i, doc in zip(idxs, nlp.pipe([t for _, t in indices], batch_size=bs)):
                out[i] = self._entities_from_doc(doc)
        return out

    def _chunks_from_oversized_block(self, block, min_len, max_block, max_line_slice):
        """Subdivide a block that already exceeds max_block.

        Order: inner ``\\n\\n`` sub-paragraphs → else each physical line (一行一次) → else char
        windows only for a single line that is still too long.
        """
        out = []
        inner = [p.strip() for p in block.split("\n\n") if len(p.strip()) >= min_len]
        if len(inner) > 1:
            for p in inner:
                if len(p) <= max_block:
                    out.append(p)
                else:
                    out.extend(self._chunks_from_oversized_block(p, min_len, max_block, max_line_slice))
            return out

        if "\n" in block:
            for line in block.split("\n"):
                line = line.strip()
                if len(line) < min_len:
                    continue
                if len(line) <= max_block:
                    out.append(line)
                else:
                    for i in range(0, len(line), max_line_slice):
                        piece = line[i : i + max_line_slice].strip()
                        if len(piece) >= min_len:
                            out.append(piece)
            return out

        for i in range(0, len(block), max_line_slice):
            piece = block[i : i + max_line_slice].strip()
            if len(piece) >= min_len:
                out.append(piece)
        return out

    def _split_atx_headings(self, content):
        """Split on Markdown ATX headings at line start: ``#`` * k + whitespace, k in [min_level..6].

        Returns ``None`` if there is no such heading (single chunk only) — caller uses ``\\n\\n`` flow.
        """
        lvl = self._heading_min_level
        if lvl <= 0 or lvl > 6:
            return None
        pattern = re.compile(rf"(?m)^(?=#{{{lvl},6}}\s)")
        parts = [p.strip() for p in pattern.split(content) if p.strip()]
        if len(parts) <= 1:
            return None
        return parts

    def _finalize_chunks(self, blocks, min_len, max_block):
        out = []
        for block in blocks:
            block = block.strip()
            if len(block) <= max_block:
                out.append(block)
            else:
                out.extend(
                    self._chunks_from_oversized_block(
                        block, min_len, max_block, self._slice_chars
                    )
                )
        return out

    def paragraphs_from_content(self, content):
        """Split a note into units for spaCy + LLM.

        - If ``ATOMIZER_HEADING_MIN_LEVEL`` > 0 and the file contains ATX headings at that depth
          (e.g. 4 → ``####`` … ``######``), **each heading section** (title + body until next such
          heading) is one primary unit, then ``MAX_BLOCK`` / line / slice rules apply inside it.
        - Else: Markdown paragraphs (``\\n\\n``), then oversized → inner ``\\n\\n`` → per line →
          char slice for one ultra-long line.
        """
        min_len = self._min_paragraph_chars
        max_block = self._max_block_chars

        heading_parts = self._split_atx_headings(content)
        if heading_parts is not None:
            chunks = self._finalize_chunks(heading_parts, min_len, max_block)
            if chunks:
                return chunks

        blocks = [p.strip() for p in content.split("\n\n") if p.strip()]
        if not blocks:
            return []
        return self._finalize_chunks(blocks, min_len, max_block)

    def _post_ollama(self, prompt, num_predict=None):
        """Synchronous Ollama API call with retry and error handling."""
        opts = {**OLLAMA_OPTIONS, "num_predict": num_predict if num_predict is not None else int(os.environ.get("OLLAMA_NUM_PREDICT", "2048"))}
        payload = {
            "model": MODEL_NAME,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": opts,
        }
        
        retry_count = int(os.environ.get("OLLAMA_RETRY_COUNT", "2"))
        for attempt in range(retry_count + 1):
            try:
                res = self.http.post(OLLAMA_API, json=payload, timeout=int(os.environ.get("OLLAMA_TIMEOUT", "600")))
                res.raise_for_status()
                resp_json = res.json()
                
                # Parse Ollama response format: { "response": "{ ... }" }
                response_text = resp_json.get("response", "")
                if isinstance(response_text, str):
                    try:
                        data = json.loads(response_text)
                        return data
                    except json.JSONDecodeError as je:
                        print(f"⚠️  Failed to parse JSON from Ollama response: {je}")
                        if attempt < retry_count:
                            print(f"   Retrying ({attempt + 1}/{retry_count + 1})...")
                            time.sleep(1)
                            continue
                        return None
                else:
                    return response_text
                    
            except httpx.TimeoutException:
                print(f"⚠️  Ollama API timeout (attempt {attempt + 1}/{retry_count + 1})")
                if attempt < retry_count:
                    time.sleep(2)
                    continue
                return None
            except httpx.RequestError as e:
                print(f"⚠️  Connection error to Ollama API: {e}")
                if attempt < retry_count:
                    time.sleep(2)
                    continue
                return None
            except Exception as e:
                print(f"❌ Unexpected error calling Ollama API: {e}")
                return None
        
        return None

    def _normalize_tags(self, data):
        if "tags" in data and isinstance(data["tags"], list):
            data["tags"] = [t.lower().replace(" ", "-").strip("#") for t in data["tags"]]
        if "classification" in data and isinstance(data["classification"], list):
            data["classification"] = [str(t).lower().replace(" ", "-") for t in data["classification"]]
        if "emotion_triggers" in data and isinstance(data["emotion_triggers"], list):
            data["emotion_triggers"] = [str(t).lower().strip() for t in data["emotion_triggers"]]
        if "perspective" in data and isinstance(data["perspective"], str):
            data["perspective"] = data["perspective"].lower().strip()
        return data

    async def _post_ollama_async(self, client, prompt, num_predict=None):
        opts = {**OLLAMA_OPTIONS, "num_predict": num_predict if num_predict is not None else int(os.environ.get("OLLAMA_NUM_PREDICT", "2048"))}
        payload = {
            "model": MODEL_NAME,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": opts,
        }
        max_retries = int(os.environ.get("OLLAMA_RETRIES", "2"))
        timeout = int(os.environ.get("OLLAMA_TIMEOUT", "600"))
        for attempt in range(max_retries + 1):
            try:
                response = await client.post(OLLAMA_API, json=payload, timeout=timeout)
                response.raise_for_status()
                resp_json = response.json()
                response_text = resp_json.get("response", "")
                if isinstance(response_text, str):
                    try:
                        return json.loads(response_text)
                    except json.JSONDecodeError as je:
                        print(f"⚠️  Failed to parse JSON from Ollama async response: {je}")
                        return None
                return response_text
            except httpx.TimeoutException:
                if attempt < max_retries:
                    print(f"⚠️  Async Ollama API timeout (attempt {attempt + 1}/{max_retries + 1}), retrying...")
                    await asyncio.sleep(2 ** attempt)  # Exponential backoff
                else:
                    print(f"⚠️  Async Ollama API timeout after {max_retries + 1} attempts")
                    return None
            except httpx.RequestError as e:
                print(f"⚠️  Async connection error to Ollama API: {e}")
                return None
            except Exception as e:
                print(f"❌ Unexpected async error calling Ollama API: {e}")
                return None

    async def call_qwen_audit_async(self, client, entity, context):
        is_zh = any('\u4e00' <= c <= '\u9fff' for c in context)
        lang_style = "Match the exact Chinese style (Traditional) of the context." if is_zh else "Use English."
        prompt = f"""
        Task: Audit the entity [[{entity}]] from the provided text.
        Style Guide: {lang_style}
        [Audit Framewor]
        - insight: Concise summary of what happened, Fact-based.
        - logic: Root Cause Analysis. For long essays, find systemic patterns. For fragments/diaries, find the mental trigger.
        - related: Essential causal or logical links to other entities.
        - tags: 3-4 English tags.
        - perspective: one of self, other, society.
        - classification: one or more of decision, problem, learning, observation.
        - emotion_triggers: list of mood or feeling words found in the text.

        Text: {context[: self._ctx_chars]}
        Output JSON:
        {{ "theme": "one of {self.themes}", "tags": [], "insight": "", "logic": "", "related": [], "perspective": "", "classification": [], "emotion_triggers": [] }}
        """
        data = await self._post_ollama_async(client, prompt, num_predict=int(os.environ.get("OLLAMA_NUM_PREDICT_SINGLE", "1024")))
        if data is None:
            print(f"⚠️  Entity '{entity}' async audit returned None (Ollama API failed)")
            return None
        return self._normalize_tags(data)

    async def _batch_audit_attempt_async(self, client, entity_label_pairs, context):
        n = len(entity_label_pairs)
        is_zh = any('\u4e00' <= c <= '\u9fff' for c in context)
        lang_style = "Match the exact Chinese style (Traditional) of the context." if is_zh else "Use English."
        ent_lines = "\n".join(f"- [[{e[0]}]]" for e in entity_label_pairs)
        prompt = f"""
        Task: For EACH entity listed, produce one audit object using ONLY the provided text.
        Style Guide: {lang_style}
        Entities (one audit per line, same order):
        {ent_lines}
        [Audit Framework per entity]
        - insight: Concise, fact-based.
        - logic: Root cause / mental trigger.
        - related: Links to other entities.
        - tags: 3-4 English kebab-case tags.
        - perspective: one of self, other, society.
        - classification: one or more of decision, problem, learning, observation.
        - emotion_triggers: list of mood or feeling words found in the text.

        Text: {context[: self._ctx_chars]}
        Output JSON:
        {{ "audits": [
           {{ "entity": "exact entity string as listed", "theme": "one of {self.themes}", "tags": [], "insight": "", "logic": "", "related": [], "perspective": "", "classification": [], "emotion_triggers": [] }}
        ] }}
        The "audits" array MUST have exactly {n} objects, in the same order as the entity list.
        """
        num_predict = min(400 + 220 * n, int(os.environ.get("OLLAMA_NUM_PREDICT_BATCH_MAX", "3072")))
        data = await self._post_ollama_async(client, prompt, num_predict=num_predict)
        if not data:
            return None
        audits = data.get("audits")
        if not isinstance(audits, list) or len(audits) != n:
            return None
        by_entity = {}
        for i, a in enumerate(audits):
            if not isinstance(a, dict):
                return None
            key = a.get("entity")
            if not key and i < len(entity_label_pairs):
                key = entity_label_pairs[i][0]
            if key:
                by_entity[key.strip()] = self._normalize_tags(dict(a))
        return by_entity if len(by_entity) == n else None

    async def call_qwen_audit_batch_async(self, client, entity_label_pairs, context):
        if not entity_label_pairs:
            return {}
        if len(entity_label_pairs) == 1:
            ent, _ = entity_label_pairs[0]
            one = await self.call_qwen_audit_async(client, ent, context)
            return {ent: one} if one else {}
        if len(entity_label_pairs) <= self._max_batch_size:
            merged = await self._batch_audit_attempt_async(client, entity_label_pairs, context)
            if merged is not None:
                return merged
        batch_size = self._max_batch_size
        merged = {}
        for i in range(0, len(entity_label_pairs), batch_size):
            batch = entity_label_pairs[i:i+batch_size]
            result = await self._batch_audit_attempt_async(client, batch, context)
            if result is not None:
                merged.update(result)
            else:
                for ent, _ in batch:
                    audit = await self.call_qwen_audit_async(client, ent, context)
                    if audit:
                        merged[ent] = audit
        return merged if merged else {}

    async def _save_to_wiki_async(self, entity_name, label, audit, rel_source_path, date, write_lock):
        async with write_lock:
            await asyncio.to_thread(self.save_to_wiki, entity_name, label, audit, rel_source_path, date,)

    async def _process_paragraph_async(self, client, paragraph, unique_atoms, rel_path, date, semaphore, write_lock):
        async with semaphore:
            batch_map = await self.call_qwen_audit_batch_async(client, unique_atoms, paragraph)
            for ent_name, label in unique_atoms:
                if self.stop_requested:
                    break
                row = self._audit_lookup(batch_map, ent_name)
                if row is None:
                    row = await self.call_qwen_audit_async(client, ent_name, paragraph)
                if row:
                    row["entity"] = ent_name  # 添加實體名稱以供後續處理
                row = self._merge_local_attributes(row, paragraph)
                await self._save_to_wiki_async(ent_name, label, row, rel_path, date, write_lock)

    async def _process_paragraphs_async(self, paragraphs, entity_lists, rel_path, date):
        semaphore = asyncio.Semaphore(self._network_concurrency)
        write_lock = asyncio.Lock()
        async with httpx.AsyncClient(timeout=int(os.environ.get("OLLAMA_TIMEOUT", "600"))) as client:
            self._current_client = client
            tasks = []
            for paragraph, atoms in zip(paragraphs, entity_lists):
                if self.stop_requested:
                    break
                seen_names = set()
                unique_atoms = []
                for ent_name, label in atoms:
                    if ent_name in seen_names:
                        continue
                    seen_names.add(ent_name)
                    unique_atoms.append((ent_name, label))
                if self._max_entities_para > 0 and len(unique_atoms) > self._max_entities_para:
                    unique_atoms = unique_atoms[: self._max_entities_para]
                tasks.append(self._process_paragraph_async(client, paragraph, unique_atoms, rel_path, date, semaphore, write_lock))
            if tasks:
                self._current_tasks = tasks
                await asyncio.gather(*tasks, return_exceptions=True)
                self._current_tasks = []
            self._current_client = None

    def call_qwen_audit(self, entity, context):
        is_zh = any('\u4e00' <= c <= '\u9fff' for c in context)
        lang_style = "Match the exact Chinese style (Traditional) of the context." if is_zh else "Use English."

        prompt = f"""
        Task: Audit the entity [[{entity}]] from the provided text.
        Style Guide: {lang_style}
        [Audit Framework]
        - insight: Concise summary of what happened, Fact-based.
        - logic: Root Cause Analysis. For long essays, find systemic patterns. For fragments/diaries, find the mental trigger.
        - related: Essential causal or logical links to other entities.
        - tags: 3-4 English tags.
        - perspective: one of self, other, society.
        - classification: one or more of decision, problem, learning, observation.
        - emotion_triggers: list of mood or feeling words found in the text.

        Text: {context[: self._ctx_chars]}
        Output JSON:
        {{ "theme": "one of {self.themes}", "tags": [], "insight": "", "logic": "", "related": [], "perspective": "", "classification": [], "emotion_triggers": [] }}
        """
        try:
            data = self._post_ollama(prompt, num_predict=int(os.environ.get("OLLAMA_NUM_PREDICT_SINGLE", "1024")))
            if data is None:
                print(f"⚠️  Entity '{entity}' audit returned None (Ollama API failed)")
                return None
            return self._normalize_tags(data)
        except Exception as e:
            print(f"❌ Error auditing entity '{entity}': {e}")
            return None

    def _audit_lookup(self, by_entity, ent_name):
        if ent_name in by_entity:
            return by_entity[ent_name]
        s = ent_name.strip()
        if s in by_entity:
            return by_entity[s]
        for k, v in by_entity.items():
            if k.strip() == s:
                return v
        return None

    def _batch_audit_attempt(self, entity_label_pairs, context):
        """One Ollama call for many entities. Returns dict or None if JSON/model failed."""
        n = len(entity_label_pairs)
        is_zh = any('\u4e00' <= c <= '\u9fff' for c in context)
        lang_style = "Match the exact Chinese style (Traditional) of the context." if is_zh else "Use English."
        ent_lines = "\n".join(f"- [[{e[0]}]]" for e in entity_label_pairs)

        prompt = f"""
        Task: For EACH entity listed, produce one audit object using ONLY the provided text.
        Style Guide: {lang_style}
        Entities (one audit per line, same order):
        {ent_lines}
        [Audit Framework per entity]
        - insight: Concise, fact-based.
        - logic: Root cause / mental trigger.
        - related: Links to other entities.
        - tags: 3-4 English kebab-case tags.
        - perspective: one of self, other, society.
        - classification: one or more of decision, problem, learning, observation.
        - emotion_triggers: list of mood or feeling words found in the text.

        Text: {context[: self._ctx_chars]}
        Output JSON:
        {{ "audits": [
           {{ "entity": "exact entity string as listed", "theme": "one of {self.themes}", "tags": [], "insight": "", "logic": "", "related": [], "perspective": "", "classification": [], "emotion_triggers": [] }}
        ] }}
        The "audits" array MUST have exactly {n} objects, in the same order as the entity list.
        """
        num_predict = min(400 + 220 * n, int(os.environ.get("OLLAMA_NUM_PREDICT_BATCH_MAX", "3072")))
        try:
            data = self._post_ollama(prompt, num_predict=num_predict)
            audits = data.get("audits")
            if not isinstance(audits, list) or len(audits) != n:
                return None
            by_entity = {}
            for i, a in enumerate(audits):
                if not isinstance(a, dict):
                    return None
                key = a.get("entity")
                if not key and i < len(entity_label_pairs):
                    key = entity_label_pairs[i][0]
                if key:
                    by_entity[key.strip()] = self._normalize_tags(dict(a))
            if len(by_entity) != n:
                return None
            return by_entity
        except Exception:
            return None

    def call_qwen_audit_batch(self, entity_label_pairs, context):
        """Batched Ollama call with adaptive batch sizing to reduce API calls."""
        if not entity_label_pairs:
            return {}
        if len(entity_label_pairs) == 1:
            ent, _ = entity_label_pairs[0]
            one = self.call_qwen_audit(ent, context)
            return {ent: one} if one else {}

        # Try batch attempt for up to max_batch_size entities
        if len(entity_label_pairs) <= self._max_batch_size:
            merged = self._batch_audit_attempt(entity_label_pairs, context)
            if merged is not None:
                return merged
        else:
            # For larger batches, split and process in parallel batches
            batch_size = self._max_batch_size
            merged = {}
            for i in range(0, len(entity_label_pairs), batch_size):
                batch = entity_label_pairs[i:i+batch_size]
                result = self._batch_audit_attempt(batch, context)
                if result is not None:
                    merged.update(result)
                else:
                    # Fallback for failed batch
                    for ent, _ in batch:
                        audit = self.call_qwen_audit(ent, context)
                        if audit:
                            merged[ent] = audit
            return merged if merged else {}

        # Fallback: binary split on single-batch failure
        mid = len(entity_label_pairs) // 2
        left = self.call_qwen_audit_batch(entity_label_pairs[:mid], context)
        right = self.call_qwen_audit_batch(entity_label_pairs[mid:], context)
        return {**left, **right}

    def save_to_wiki(self, entity_name, label, audit, rel_source_path, date):
        name = self.get_normalized_name(entity_name)
        
        # Step 1: 清理首尾的非字母數字字符 (移除符號、下劃線、方括號等，僅保留起始的中英文字)
        cleaned_name = re.sub(r'^[^a-zA-Z0-9\u4e00-\u9fff]+|[^a-zA-Z0-9\u4e00-\u9fff]+$', '', name).strip()

        # Step 2: 將 CamelCase 轉換為帶空格的單詞 (例如 HighPerformance -> High Performance)
        cleaned_name = re.sub(r'(?<!^)(?=[A-Z])', ' ', cleaned_name).strip()
        
        # Step 3: 將剩餘的非字母數字字符 (如 &、下劃線) 替換為空格，避免文件名中出現雜亂符號
        cleaned_name = re.sub(r'[^a-zA-Z0-9\s\u4e00-\u9fff]', ' ', cleaned_name)
        
        # Step 4: 正規化空格並轉換為小寫
        cleaned_name = re.sub(r'\s+', ' ', cleaned_name).strip().lower()
        
        # Step 5: 將空格替換為連字符，生成最終安全的文件名
        safe_name = cleaned_name.replace(' ', '-')
        
        # 如果清理後文件名為空，提供一個默認值
        if not safe_name:
            safe_name = "untitled-atom"
        file_path = os.path.join(self.wiki_dir, f"{safe_name}.md")
        is_new = not os.path.exists(file_path)
        
        audit_data = audit or {}
        # 標籤轉為 Obsidian 標籤格式
        insight = audit_data.get('insight') 
        if not insight:
            print(f"⚠️ Skipping {entity_name} due to empty insight")
            return
        tags = list(audit_data.get('tags', []))
        classification = audit_data.get('classification', [])
        if isinstance(classification, str):
            classification = [classification]
        perspective = audit_data.get('perspective')
        emotion_triggers = audit_data.get('emotion_triggers', [])
        if isinstance(emotion_triggers, str):
            emotion_triggers = [emotion_triggers]

        if perspective:
            tags.append(perspective)
        tags.extend(classification)
        if emotion_triggers:
            tags.append("emotion")
            tags.extend([f"emotion-{str(t).lower().replace(' ', '-') }" for t in emotion_triggers if t])

        is_missing_ai_tags = len(tags) == 0
        if is_missing_ai_tags:
            path_lower = rel_source_path.lower()
            if "diary" in path_lower: tags.append("diary")
            elif "business" in path_lower: tags.append("business")
            elif "tech" in path_lower: tags.append("tech")
            # 2. 尝试从 Theme 填充 (业务逻辑)
            elif audit_data.get('theme'):
                tags.append(audit_data.get('theme'))
            # 3. 尝试从 Label 填充 (物理属性)
            elif label:
                tags.append(label.lower())
            
            # 始终加上这个，方便在 Obsidian 里一键找到所有“降级生成”的页面
            tags.append("missing-tags")

        # 归一化标签格式：去掉 # 号，转小写，空格换横杠
        clean_tags = [str(t).lower().replace(" ", "-").strip("#") for t in tags]
        clean_tags = list(dict.fromkeys(clean_tags))
        # 用于正文展示的标签行
        tag_line = " ".join([f"#{t}" for t in clean_tags])
        
        source_link = f"../raw/{rel_source_path}"
        related = [self.cc.convert(str(r)) for r in audit_data.get('related', [])]
        theme = audit_data.get('theme', 'self')
        logic = audit_data.get('logic')

        print(f"{entity_name} | {theme} | {clean_tags} | {insight} | {logic} | perspective={perspective} classification={classification} | {related}")

        with open(file_path, "a", encoding="utf-8") as f:
            if is_new:
                meta = {
                    "theme": theme,
                    "tags": clean_tags,
                    "label": label,
                    "perspective": perspective,
                    "classification": classification,
                    "emotion_triggers": emotion_triggers,
                    "last_audit": date
                }
                f.write(f"---\n{yaml.dump(meta, allow_unicode=True)}---\n# {name}\n\n## 📜 Trace\n\n")
                if name not in self.entity_cache_set:
                    self.entity_cache.append(name)
                    self.entity_cache_set.add(name)
            
            entry = f"- **{date}**: {insight} {tag_line}\n"
            if logic: 
                entry += f"  > **Logic**: {logic}\n"
            else:
                entry += f"  > **Logic**: Root cause analysis not provided by AI model.\n"
            rel_links = ", ".join([f"[[{r}]]" for r in related])
            entry += f"  *(Source: [[{source_link}]] | Related: {rel_links if rel_links else 'None'})*\n\n"
            f.write(entry)

    def run(self):
        start_time = time.time()
        all_files = []
        for root, dirs, files in os.walk(INPUT_DIR):
            # Coach Q&A lives under raw/queries — do not atomize it.
            dirs[:] = [d for d in dirs if d != "queries"]
            for f in files:
                if f.endswith(".md"):
                    all_files.append(os.path.join(root, f))
        
        print(f"🚀 開始原子化審計 | 待處理: {len(all_files)} 文件 | 批大小: {self._max_batch_size}")

        save_counter = 0  # 计数器：每5个文件保存一次日志

        for full_path in tqdm(all_files, desc="Processing Files"):
            if self.stop_requested: break
            
            rel_path = os.path.relpath(full_path, INPUT_DIR)
            file_hash = self.get_file_hash(full_path)
            if self.processed_log.get(rel_path) == file_hash: 
                print(f"⏭️  跳过未改动文件: {rel_path}")
                continue
            if 'tv-shows' in rel_path or 'games-of-thrones' in rel_path or '2020-08-24-notes-on-your-assets' in rel_path: 
                print(f"⏭️  跳过未改动文件: {rel_path}")
                continue

            date_match = re.search(r"(\d{4}-\d{2}-\d{2})", os.path.basename(full_path))
            date = date_match.group(1) if date_match else datetime.now().strftime("%Y-%m-%d")

            print(f"🚀 processing: {rel_path}")
            with open(full_path, 'r', encoding='utf-8') as f:
                content = f.read()

            # 跳过小文件（字符少于5），避免不必要的分词处理
            if len(content.strip()) < 5:
                continue

            paragraphs = self.paragraphs_from_content(content)
            if not paragraphs:
                continue
            entity_lists = self.extract_entities_batch(paragraphs)
            self._run_async(self._process_paragraphs_async(paragraphs, entity_lists, rel_path, date))

            if not self.stop_requested:
                self.processed_log[rel_path] = file_hash
                save_counter += 1
                if save_counter % 10 == 0:
                    self._save_log()
                    print(f"✅ 进度已记录: {rel_path} (每10个文件保存)")
                else:
                    print(f"✅ 处理完成: {rel_path}")

        # 确保最后保存一次日志
        if not self.stop_requested:
            self._save_log()

        elapsed = time.time() - start_time
        status = "已中止" if self.stop_requested else "已完成"
        print(f"\n✅ 任務{status}。進度已妥善保存。總耗時: {elapsed:.1f}秒")

if __name__ == "__main__":
    WikiAtomizer().run()