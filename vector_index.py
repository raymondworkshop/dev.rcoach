import os
import json
import requests
import re
import hashlib
import datetime
import numpy as np
from tqdm import tqdm
from opencc import OpenCC
from thefuzz import fuzz

# --- 核心配置 ---
OLLAMA_API = "http://100.90.225.26:11434/api/embeddings"
EMBED_MODEL = "nomic-embed-text" 

BASE_DIR = os.path.abspath("./rbrain-wiki")
ATOMS_DIR = os.path.join(BASE_DIR, "atoms")
SAVE_PATH = os.path.join(BASE_DIR, "vector_index.json")

class WikiHybridIndexer:
    def __init__(self):
        self.cc = OpenCC('s2t')
        os.makedirs(os.path.dirname(SAVE_PATH), exist_ok=True)
        self.data_store = self._load_index()

    def _load_index(self):
        """載入現有的索引數據"""
        if os.path.exists(SAVE_PATH):
            try:
                with open(SAVE_PATH, 'r', encoding='utf-8') as f:
                    # 轉換為字典以利增量比對: {atom_name: data}
                    return {item['name']: item for item in json.load(f)}
            except: return {}
        return {}

    def get_file_hash(self, text):
        """計算文件內容的 Hash，用於增量檢查"""
        return hashlib.md5(text.encode('utf-8')).hexdigest()

    def clean_content(self, text):
        """解析內容：返回 (純淨文本, 原始路徑)"""
        # 1. 提取相對路徑 Source
        source_match = re.search(r"Source: \[\[\.\./raw/(.*?)\]\]", text)
        source_rel_path = source_match.group(1) if source_match else "unknown"

        # 2. 清洗 Markdown 雜訊
        text = re.sub(r'---.*?---', '', text, flags=re.DOTALL) # 移除 YAML
        text = re.sub(r'\*\(Source:.*?\)\*', '', text)        # 移除 Source 標記行
        text = re.sub(r'[#`\[\]]', '', text)                  # 移除符號
        
        clean_text = self.cc.convert(text).strip()
        return clean_text, source_rel_path

    def get_embedding(self, text):
        """調用 Ollama API 獲取向量"""
        try:
            res = requests.post(OLLAMA_API, json={
                "model": EMBED_MODEL, 
                "prompt": text
            }, timeout=240)
            res.raise_for_status()
            return res.json().get("embedding")
        except Exception as e:
            print(f"\n❌ Embedding 失敗: {e}")
            return None

    def run_indexing(self):
        """掃描 atoms 目錄，執行增量索引"""
        if not os.path.exists(ATOMS_DIR):
            print(f"❌ 錯誤: 找不到目錄 {ATOMS_DIR}")
            return
        
        all_files = [f for f in os.listdir(ATOMS_DIR) if f.endswith('.md')]
        updated_count = 0

        print(f"🚀 正在檢查 {len(all_files)} 篇原子筆記的變動...")

        for filename in tqdm(all_files):
            atom_name = filename[:-3]
            file_path = os.path.join(ATOMS_DIR, filename)
            
            with open(file_path, 'r', encoding='utf-8') as f:
                raw_content = f.read()

            current_hash = self.get_file_hash(raw_content)
            
            # 增量比對：如果內容沒變，跳過向量運算
            if atom_name in self.data_store:
                if self.data_store[atom_name].get("hash") == current_hash:
                    continue

            clean_text, source_path = self.clean_content(raw_content)
            vector = self.get_embedding(clean_text)
            
            if vector:
                self.data_store[atom_name] = {
                    "name": atom_name,
                    "source": f"raw/{source_path}",
                    "text": clean_text,
                    "vector": vector,
                    "hash": current_hash,
                    "updated_at": datetime.datetime.now().isoformat()
                }
                updated_count += 1

        # 垃圾回收：移除已經不在目錄中的索引項
        final_list = [v for k, v in self.data_store.items() if os.path.exists(os.path.join(ATOMS_DIR, k+".md"))]
        
        with open(SAVE_PATH, 'w', encoding='utf-8') as f:
            json.dump(final_list, f, ensure_ascii=False, indent=2)
        
        print(f"✅ 索引同步完成。更新數量: {updated_count}")

    def search(self, query, top_k=5):
        """混合檢索：KW 70% / Vec 70% (動態調整)"""
        query_t = self.cc.convert(query.strip())
        query_vec = self.get_embedding(query_t)
        
        # --- 權重策略優化 ---
        # 針對你的需求：2 個字（如 "善良"）觸發強關鍵字匹配
        if len(query_t) <= 2:
            kw_w, sem_w = 0.8, 0.2
            mode = "精確鎖定 (Strict)"
        else:
            kw_w, sem_w = 0.3, 0.7
            mode = "語義關聯 (Explore)"

        results = []
        for name, data in self.data_store.items():
            # 1. 關鍵字得分 (Fuzzy Match)
            # 標題完全一致給予額外加權
            t_score = fuzz.ratio(query_t.lower(), name.lower()) / 100.0
            c_score = fuzz.partial_ratio(query_t, data['text']) / 100.0
            kw_score = max(t_score * 1.3, c_score)
            kw_score = min(kw_score, 1.0)

            # 2. 語義得分 (餘弦相似度)
            sem_score = 0
            if query_vec and data['vector']:
                v1, v2 = np.array(query_vec), np.array(data['vector'])
                denom = (np.linalg.norm(v1) * np.linalg.norm(v2))
                if denom > 0:
                    sem_score = np.dot(v1, v2) / denom
                sem_score = (sem_score + 1) / 2 # 歸一化

            # 3. 混合評分
            total_score = (kw_score * kw_w) + (sem_score * sem_w)

            # 極端情況：標題完全一致強制置頂
            if query_t.lower() == name.lower():
                total_score = 2.0

            results.append({
                "name": name,
                "source": data['source'],
                "score": total_score,
                "kw": kw_score,
                "sem": sem_score
            })

        # 排序並過濾
        results = sorted(results, key=lambda x: x['score'], reverse=True)[:top_k]
        
        print(f"\n🔎 搜尋: '{query_t}' | 策略: {mode}")
        print("-" * 80)
        for i, r in enumerate(results):
            tag = "🎯" if r['score'] >= 1.0 else "🔍"
            print(f"[{i+1}] {tag} [[{r['name']}]]")
            print(f"    📂 原文: {r['source']}")
            print(f"    📊 權重分配: 關鍵字 {r['kw']:.2f}({int(kw_w*100)}%) | 語義 {r['sem']:.2f}({int(sem_w*100)}%)")
            print("-" * 80)

if __name__ == "__main__":
    import sys
    indexer = WikiHybridIndexer()
    
    # 命令行參數處理
    if len(sys.argv) > 1 and sys.argv[1] == "--build":
        indexer.run_indexing()
    else:
        # 如果索引不存在則先跑 build
        if not indexer.data_store:
            indexer.run_indexing()
        
        while True:
            q = input("\n請輸入搜索詞 (q 退出): ").strip()
            if q.lower() == 'q': break
            if q: indexer.search(q)