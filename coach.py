import os
import requests
import datetime
import re
import numpy as np
import json
import yaml
import signal
import sys
import httpx
from opencc import OpenCC
from thefuzz import fuzz

from rbrain_config import get_config

class ProfessionalCoach:
    def __init__(self, base_dir=None):
        self.cc = OpenCC('s2t')
        cfg = get_config()
        self.base_dir = os.path.abspath(base_dir or cfg["wiki_root"])
        self._embeddings_url = cfg["ollama_embeddings_url"]
        self._generate_url = cfg["ollama_generate_url"]

        # 標準路徑定義
        self.atoms_dir = os.path.join(self.base_dir, "atoms")
        self.queries_dir = os.path.join(self.base_dir, "raw", "queries")
        self.index_path = os.path.join(self.base_dir, "vector_index.json")

        os.makedirs(self.queries_dir, exist_ok=True)

        # 中斷機制（防止問答中途卡死）
        self.stop_requested = False
        signal.signal(signal.SIGINT, self._handle_interrupt)

        # 載入索引
        self.data_store = self._load_index()
        self.model = cfg["generate_model"]
        self.embed_model = cfg["embed_model"]
        self.http = httpx.Client(timeout=int(os.environ.get("OLLAMA_TIMEOUT", "600")))

    def _handle_interrupt(self, signum, frame):
        print("\n\n� [INTERRUPT] 正在保存當前狀態並安全退出...")
        self.stop_requested = True

    def _load_index(self):
        if os.path.exists(self.index_path):
            with open(self.index_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        print(f"⚠️ 警告: 找不到索引文件 {self.index_path}")
        return []

    def _get_embedding(self, text):
        try:
            # 處理搜尋詞中的標籤符號
            clean_text = text.strip('#').replace('-', ' ')
            res = self.http.post(self._embeddings_url, json={
                "model": self.embed_model,
                "prompt": clean_text
            })
            res.raise_for_status()
            return res.json().get("embedding")
        except Exception as e:
            print(f"❌ Embedding API 錯誤: {e}")
            return None

    def _raw_excerpt(self, source_field, max_chars=1400):
        """Load a snippet from raw diary (vector_index 'source' is like 'raw/foo.md')."""
        if not source_field or source_field == "unknown":
            return ""
        rel = source_field.replace("\\", "/").lstrip("/")
        if rel.startswith("raw/"):
            path = os.path.join(self.base_dir, rel)
        else:
            path = os.path.join(self.base_dir, "raw", rel)
        try:
            if not os.path.isfile(path):
                return ""
            with open(path, encoding="utf-8") as f:
                body = f.read()
            return body.strip()[:max_chars]
        except Exception:
            return ""

    def retrieve_hybrid(self, query_text, n=6):
        query_t = self.cc.convert(query_text.strip())
        is_tag_search = query_t.startswith('#')
        search_term = query_t.strip('#').replace('-', ' ')
        
        query_vec = self._get_embedding(search_term)
        
        # 權重分配：標籤或短詞優先精確匹配
        if len(search_term) <= 3 or is_tag_search:
            kw_w, sem_w = 0.7, 0.3
        else:
            kw_w, sem_w = 0.3, 0.7

        scored_results = []
        for item in self.data_store:
            # 1. 關鍵字得分 (增加標籤匹配權重)
            t_score = fuzz.ratio(search_term.lower(), item['name'].lower()) / 100.0
            
            # 兼容新版 vector_index 中的 tags 列表
            tag_list = item.get('tags', [])
            tag_str = " ".join(tag_list).lower()
            tag_match = fuzz.partial_ratio(search_term.lower(), tag_str) / 100.0
            
            c_score = fuzz.partial_ratio(search_term, item['text']) / 100.0
            
            # 綜合關鍵字分數
            kw_score = max(t_score * 1.5, tag_match * 1.3, c_score)
            kw_score = min(kw_score, 1.0)

            # 2. 語義得分
            sem_score = 0
            if query_vec and item.get('vector'):
                v1, v2 = np.array(query_vec), np.array(item['vector'])
                norm = np.linalg.norm(v1) * np.linalg.norm(v2)
                if norm > 0:
                    sem_score = (np.dot(v1, v2) / norm + 1) / 2

            total_score = (kw_score * kw_w) + (sem_score * sem_w)
            
            # 核心匹配補償
            if search_term.lower() == item['name'].lower() or search_term.lower() in tag_list:
                total_score += 0.5

            # Boost for new attributes: perspective, classification, emotion_triggers
            perspective = item.get('perspective', '')
            classification = item.get('classification', [])
            emotion_triggers = item.get('emotion_triggers', [])
            if isinstance(classification, str):
                classification = [classification]
            if isinstance(emotion_triggers, str):
                emotion_triggers = [emotion_triggers]

            # Boost if query matches perspective or classification
            if search_term.lower() in [perspective, 'self', 'other', 'society']:
                total_score += 0.3
            if any(cls in search_term.lower() for cls in classification):
                total_score += 0.2
            if any(emo in search_term.lower() for emo in emotion_triggers):
                total_score += 0.2

            scored_results.append({"item": item, "score": total_score})

        top_results = sorted(scored_results, key=lambda x: x['score'], reverse=True)[:n]
        
        context_parts = []
        sources = []
        for r in top_results:
            item = r['item']
            tags_display = " ".join([f"#{t}" for t in item.get('tags', [])])
            raw_src = item.get("source", "")
            excerpt = self._raw_excerpt(raw_src)
            excerpt_block = (
                f"\n[Raw excerpt: {raw_src}]\n> {excerpt[:1200].replace(chr(10), ' ')}\n"
                if excerpt
                else ""
            )
            context_parts.append(
                f"### [[{item['name']}]]\nTags: {tags_display}\n"
                f"Content: {item['text'][:800]}{excerpt_block}"
            )
            sources.append(item['name'])
            
        return "\n---\n".join(context_parts), sources

    def save_qa(self, question, answer, sources):
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M")
        # 改進：文件名安全過濾
        safe_q = re.sub(r'[\\/:*?"<>|]', '_', question).strip()[:20]
        filename = f"Q_{timestamp}_{safe_q}.md"
        
        links = [f"[[{s}]]" for s in sources]
        
        # 構造符合 Obsidian 規範的 YAML
        meta = {
            "type": "coach-qa",
            "date": datetime.datetime.now().isoformat(),
            "tags": ["query", "reverse-mentorship", "diary-audit"],
            "related_atoms": sources
        }
        
        content = f"""---
{yaml.dump(meta, allow_unicode=True)}---

# 👤 用戶提問
{question}

# 🤖 教練反饋
{answer}

# 🔗 參考實體
{", ".join(links)}

---
*Generated by RBrain AI Coach*
"""
        file_path = os.path.join(self.queries_dir, filename)
        temp_path = file_path + ".tmp"
        with open(temp_path, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(temp_path, file_path)
        return filename

    def ask(self, user_input):
        context, sources = self.retrieve_hybrid(user_input)
        
        # 升級：更具針對性的教練提示詞
        system_prompt = f"""你是一位深耕社會動力學與高階商業心智的專業教練。
你的任務是根據用戶過去 5,000 篇日記所展現出的行為模式、決策邏輯和核心價值觀，對用戶當前的想法進行『深度審計』。

[背景知識 (由日記原子化數據提供；[Raw excerpt] 為原文摘錄，優先作為事實依據)]:
{context}

[執行準則]:
1. **模式識別**：找出用戶當前想法與歷史行為（如：過往對投資、人際或情緒的處理）是一致還是矛盾。
2. **精確引用**：必須在反饋中自然地提及相關實體名，例如：『這讓我想起你在 [[某個事件]] 中的處理方式...』。若使用 [Raw excerpt] 中的說法，請對應標註來源檔路徑或日期。
3. **批判思維**：不要盲目附和。如果用戶表現出過度重複的錯誤或迴避行為，請直接指出。
4. **語言風格**：使用繁體中文，語氣需兼具商業導師的冷靜與老友的誠懇。"""

        try:
            res = self.http.post(self._generate_url, json={
                "model": self.model,
                "system": system_prompt,
                "prompt": user_input,
                "stream": False,
                "options": {
                    "temperature": 0.3,
                    "top_p": 0.9,
                    "num_ctx": 8192 # 擴大上下文以容納更多原子筆記
                }
            })
            res.raise_for_status()
            answer = res.json().get("response", "❌ AI 響應異常")
        except Exception as e:
            answer = f"❌ 通信錯誤: {e}"
        
        fname = self.save_qa(user_input, answer, sources)
        return answer, fname

def main_repl():
    coach = ProfessionalCoach()

    print("\n" + "=" * 50)
    print("🧠 RBrain Professional Coach v2.0")
    print(f"📍 存檔目錄: {coach.queries_dir}")
    print("=" * 50)

    while True:
        if coach.stop_requested:
            print("\n👋 操作已中斷。")
            break
        try:
            user_q = input("\n👤 你的思考: ").strip()
            if user_q.lower() in ["q", "exit", "quit"]:
                break
            if not user_q:
                continue

            print("🔍 正在檢索深層記憶並調研行為模式...")
            ans, saved = coach.ask(user_q)

            print(f"\n💡 教練反饋：\n\n{ans}\n")
            print(f"📝 記錄已存檔至: {saved}")

        except KeyboardInterrupt:
            coach._handle_interrupt(None, None)
            break


if __name__ == "__main__":
    main_repl()