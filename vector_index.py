import os
import json
import requests
import re
import hashlib
import datetime
import signal
import sys
import numpy as np
import yaml
from tqdm import tqdm
from opencc import OpenCC
from thefuzz import fuzz

from rbrain_config import get_config

# --- 核心配置 ---
_cfg = get_config()
OLLAMA_API = _cfg["ollama_embeddings_url"]
EMBED_MODEL = _cfg["embed_model"]
BASE_DIR = _cfg["wiki_root"]
ATOMS_DIR = _cfg["atoms_dir"]
SAVE_PATH = _cfg["vector_index_path"]

class WikiHybridIndexer:
    def __init__(self):
        self.cc = OpenCC('s2t')
        os.makedirs(os.path.dirname(SAVE_PATH), exist_ok=True)
        
        # 中斷控制
        self.stop_requested = False
        signal.signal(signal.SIGINT, self._handle_interrupt)
        
        self.data_store = self._load_index()

    def _handle_interrupt(self, signum, frame):
        print("\n\n🛑 [INTERRUPT] 正在保存當前向量數據並安全退出...")
        self.stop_requested = True

    def _load_index(self):
        if os.path.exists(SAVE_PATH):
            try:
                with open(SAVE_PATH, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    return data
                if isinstance(data, list):
                    return {item['name']: item for item in data if isinstance(item, dict) and item.get('name')}
            except:
                pass
        return {}

    def _save_index(self):
        """原子化保存 JSON 索引"""
        os.makedirs(os.path.dirname(SAVE_PATH), exist_ok=True)
        final_list = [v for k, v in self.data_store.items() if os.path.exists(os.path.join(ATOMS_DIR, k + ".md"))]
        temp_path = SAVE_PATH + ".tmp"
        with open(temp_path, 'w', encoding='utf-8') as f:
            json.dump(final_list, f, ensure_ascii=False, indent=2)
        os.replace(temp_path, SAVE_PATH)

    def get_file_hash(self, filepath):
        hasher = hashlib.md5()
        with open(filepath, 'rb') as f:
            hasher.update(f.read())
        return hasher.hexdigest()

    def clean_content(self, raw_text):
        """
        深度清洗優化：
        1. 提取 YAML 中的 tags 並轉為語義化文本
        2. 提取 Source 路徑（與 atomizer.py 生成格式一致）
        3. 移除 Markdown 語法但保留標籤內容
        """
        # 1. 解析 YAML Frontmatter
        tags_text = ""
        meta = {}
        if raw_text.startswith('---'):
            try:
                parts = raw_text.split('---', 2)
                if len(parts) >= 3:
                    meta = yaml.safe_load(parts[1]) or {}
                    # 將 kebab-case 標籤還原為空格，增強向量語義理解
                    # 例如: risk-management -> risk management
                    tags_list = meta.get('tags', [])
                    tags_text = " ".join([t.replace('-', ' ') for t in tags_list])
                    # Add new attributes to semantic content
                    perspective = meta.get('perspective', '')
                    classification = meta.get('classification', [])
                    emotion_triggers = meta.get('emotion_triggers', [])
                    if isinstance(classification, str):
                        classification = [classification]
                    if isinstance(emotion_triggers, str):
                        emotion_triggers = [emotion_triggers]
                    attr_text = f" {perspective} {' '.join(classification)} {' '.join(emotion_triggers)}".strip()
                    tags_text += attr_text
            except: pass

        # 2. 提取相對路徑 Source（匹配 atomizer.py 的 *(Source: [[../raw/...]] | Related: ...)* 格式）
        source_match = re.search(r"\*\(Source: \[\[\.\./raw/(.*?)\]\] \| Related:.*?\)\*", raw_text)
        source_rel_path = source_match.group(1) if source_match else "unknown"

        # 3. 清洗正文
        # 移除 YAML, Source 行
        text = re.sub(r'---.*?---', '', raw_text, flags=re.DOTALL)
        text = re.sub(r'\*\(Source:.*?\)\*', '', text)
        # 移除符號，但保留連字符（因為標籤可能還在正文中）
        text = re.sub(r'[#`\[\]]', '', text) 
        
        # 4. 融合標籤語義到正文中，讓 Embedding 能搜到標籤相關內容
        full_semantic_content = f"{tags_text} {text}".strip()
        clean_text = self.cc.convert(full_semantic_content)
        
        return clean_text, source_rel_path, meta.get('tags', []), meta

    def get_embedding(self, text):
        if not text: return None
        try:
            res = requests.post(OLLAMA_API, json={
                "model": EMBED_MODEL, 
                "prompt": text
            }, timeout=int(os.environ.get("OLLAMA_TIMEOUT", "480")))
            res.raise_for_status()
            return res.json().get("embedding")
        except Exception as e:
            print(f"\n❌ Embedding API 錯誤: {e}")
            return None

    def run_indexing(self):
        if not os.path.exists(ATOMS_DIR):
            print(f"❌ 錯誤: 找不到目錄 {ATOMS_DIR}")
            return
        
        all_files = [f for f in os.listdir(ATOMS_DIR) if f.endswith('.md')]
        updated_count = 0

        print(f"🚀 啟動增量索引 | 總文件數: {len(all_files)}")

        for filename in tqdm(all_files, desc="Embedding"):
            if self.stop_requested:
                break
            
            atom_name = filename[:-3]
            file_path = os.path.join(ATOMS_DIR, filename)
            
            with open(file_path, 'r', encoding='utf-8') as f:
                raw_content = f.read()

            current_hash = self.get_file_hash(file_path)
            
            # 增量比對
            if atom_name in self.data_store and self.data_store[atom_name].get("hash") == current_hash:
                continue

            clean_text, source_path, tags, meta = self.clean_content(raw_content)
            vector = self.get_embedding(clean_text)
            
            if vector:
                self.data_store[atom_name] = {
                    "name": atom_name,
                    "source": f"raw/{source_path}",
                    "tags": tags, # 保存原始標籤供搜尋過濾
                    "text": clean_text,
                    "vector": vector,
                    "hash": current_hash,
                    "updated_at": datetime.datetime.now().isoformat(),
                    "perspective": meta.get('perspective', ''),
                    "classification": meta.get('classification', []),
                    "emotion_triggers": meta.get('emotion_triggers', [])
                }
                updated_count += 1
                
                # 每 10 個文件自動保存一次，防止大規模處理時中斷丟失過多
                if updated_count % 10 == 0:
                    self._save_index()

        if self.stop_requested:
            print("\n⚠️ 中斷請求已觸發，正在保存已生成的向量索引...")
        self._save_index()
        print(f"\n✅ 索引同步完成。更新數量: {updated_count}")

    def search(self, query, top_k=5):
        query_t = self.cc.convert(query.strip())
        # 搜尋預處理：如果搜尋詞帶 #，強化關鍵字權重
        is_tag_search = query_t.startswith('#')
        search_term = query_t.strip('#').replace('-', ' ')
        
        query_vec = self.get_embedding(search_term)
        
        # 權重策略：短詞或標籤搜尋傾向關鍵字，長句傾向語義
        if len(search_term) <= 3 or is_tag_search:
            kw_w, sem_w = 0.7, 0.3
        else:
            kw_w, sem_w = 0.3, 0.7

        results = []
        for name, data in self.data_store.items():
            # 1. 關鍵字得分 (Fuzzy Match)
            # 增加標籤匹配權重
            tag_str = " ".join(data.get('tags', []))
            t_score = fuzz.ratio(search_term.lower(), name.lower()) / 100.0
            tag_score = fuzz.partial_ratio(search_term.lower(), tag_str.lower()) / 100.0
            c_score = fuzz.partial_ratio(search_term, data['text']) / 100.0
            
            kw_score = max(t_score * 1.3, tag_score * 1.2, c_score)
            kw_score = min(kw_score, 1.0)

            # 2. 語義得分
            sem_score = 0
            if query_vec and data.get('vector'):
                v1, v2 = np.array(query_vec), np.array(data['vector'])
                denom = (np.linalg.norm(v1) * np.linalg.norm(v2))
                if denom > 0:
                    sem_score = (np.dot(v1, v2) / denom + 1) / 2

            total_score = (kw_score * kw_w) + (sem_score * sem_w)
            
            # 完美匹配補償
            if search_term.lower() == name.lower() or search_term.lower() in data.get('tags', []):
                total_score += 0.5

            # Boost for new attributes
            perspective = data.get('perspective', '')
            classification = data.get('classification', [])
            emotion_triggers = data.get('emotion_triggers', [])
            if isinstance(classification, str):
                classification = [classification]
            if isinstance(emotion_triggers, str):
                emotion_triggers = [emotion_triggers]

            if search_term.lower() in [perspective, 'self', 'other', 'society']:
                total_score += 0.3
            if any(cls in search_term.lower() for cls in classification):
                total_score += 0.2
            if any(emo in search_term.lower() for emo in emotion_triggers):
                total_score += 0.2

            results.append({
                "name": name,
                "source": data['source'],
                "tags": data.get('tags', []),
                "score": total_score,
                "kw": kw_score,
                "sem": sem_score,
                "perspective": perspective,
                "classification": classification,
                "emotion_triggers": emotion_triggers
            })

        results = sorted(results, key=lambda x: x['score'], reverse=True)[:top_k]
        
        print(f"\n🔎 搜尋: '{query_t}' (模式: {'Tag' if is_tag_search else 'Hybrid'})")
        print("-" * 80)
        for i, r in enumerate(results):
            tag_display = " ".join([f"#{t}" for t in r['tags']])
            print(f"[{i+1}] [[{r['name']}]] (Score: {r['score']:.2f})")
            print(f"    🏷️ 標籤: {tag_display}")
            print(f"    📂 來源: {r['source']}")
            print(f"    �️ 視角: {r.get('perspective', 'N/A')}")
            print(f"    📋 分類: {', '.join(r.get('classification', [])) or 'N/A'}")
            print(f"    😊 情緒觸發: {', '.join(r.get('emotion_triggers', [])) or 'N/A'}")
            print(f"    �📊 權重: KW {r['kw']:.2f} | SEM {r['sem']:.2f}")
            print("-" * 80)

if __name__ == "__main__":
    import sys
    indexer = WikiHybridIndexer()
    
    if len(sys.argv) > 1 and sys.argv[1] == "--build":
        indexer.run_indexing()
    else:
        if not indexer.data_store:
            indexer.run_indexing()
        
        while True:
            try:
                q = input("\n請輸入搜索詞 (q 退出): ").strip()
                if q.lower() == 'q': break
                if q: indexer.search(q)
            except EOFError: break