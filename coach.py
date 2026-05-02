import os
import requests
import datetime
import re
import numpy as np
import json
from opencc import OpenCC
from thefuzz import fuzz

# --- 核心配置 ---
MODEL_NAME = "rbrain-qwen2.5"
OLLAMA_BASE_URL = "http://localhost:11434/api" 
EMBED_MODEL = "nomic-embed-text"

class ProfessionalCoach:
    def __init__(self, base_dir):
        self.cc = OpenCC('s2t')
        self.base_dir = os.path.abspath(base_dir)
        
        # 標準路徑定義
        self.atoms_dir = os.path.join(self.base_dir, "atoms")
        self.queries_dir = os.path.join(self.base_dir, "raw/queries")
        self.index_path = os.path.join(self.base_dir, "vector_index.json")
        
        # 確保存檔目錄存在
        os.makedirs(self.queries_dir, exist_ok=True)
        
        # 載入索引
        self.data_store = self._load_index()
        self.model = MODEL_NAME

    def _load_index(self):
        if os.path.exists(self.index_path):
            with open(self.index_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        print(f"⚠️ 警告: 找不到索引文件 {self.index_path}，請先執行索引器。")
        return []

    def _get_embedding(self, text):
        try:
            res = requests.post(f"{OLLAMA_BASE_URL}/embeddings", json={
                "model": EMBED_MODEL,
                "prompt": text
            }, timeout=30)
            return res.json()["embedding"]
        except Exception as e:
            print(f"❌ Embedding API 出錯: {e}")
            return None

    def retrieve_hybrid(self, query_text, n=6):
        """
        混合檢索：同步 vector_index.py 的權重邏輯
        """
        query_t = self.cc.convert(query_text.strip())
        query_vec = self._get_embedding(query_t)
        
        # 動態權重策略：2 個字為分水嶺
        if len(query_t) <= 2:
            kw_w, sem_w = 0.8, 0.2  # 精確模式 (人名、術語)
        else:
            kw_w, sem_w = 0.3, 0.7  # 語義模式 (情緒、複雜事件)

        scored_results = []
        for item in self.data_store:
            # 1. 關鍵字得分
            t_score = fuzz.ratio(query_t.lower(), item['name'].lower()) / 100.0
            c_score = fuzz.partial_ratio(query_t, item['text']) / 100.0
            kw_score = max(t_score * 1.5, c_score)
            kw_score = min(kw_score, 1.0)

            # 2. 語義得分
            sem_score = 0
            if query_vec and item.get('vector'):
                v1, v2 = np.array(query_vec), np.array(item['vector'])
                norm = np.linalg.norm(v1) * np.linalg.norm(v2)
                if norm > 0:
                    sem_score = (np.dot(v1, v2) / norm + 1) / 2

            total_score = (kw_score * kw_w) + (sem_score * sem_w)
            
            if query_t.lower() == item['name'].lower():
                total_score = 2.0

            scored_results.append({"item": item, "score": total_score})

        top_results = sorted(scored_results, key=lambda x: x['score'], reverse=True)[:n]
        
        # 構造上下文
        context_parts = []
        sources = []
        for r in top_results:
            item = r['item']
            context_parts.append(f"[[{item['name']}]]:\n{item['text'][:500]}")
            sources.append(item['name'])
            
        return "\n---\n".join(context_parts), sources

    def save_qa(self, question, answer, sources):
        """
        存檔至 raw/queries，適配 Obsidian 雙鏈
        """
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M")
        # 安全文件名處理：僅保留中英文字符與數字
        clean_q = re.sub(r'[^\u4e00-\u9fa5a-zA-Z0-9]', '', question)[:15]
        filename = f"Q_{timestamp}_{clean_q}.md"
        
        links = [f"[[{s}]]" for s in sources]
        content = f"""---
type: coach_qa
date: {datetime.datetime.now().isoformat()}
tags: #query #reverse-coach #diary-analysis
---

# 👤 User Query
{question}

# 🤖 Coach Response
{answer}

# 🔗 Related Atoms
{", ".join(links)}

---
Source: [[my-ai-coach]]
"""
        file_path = os.path.join(self.queries_dir, filename)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
        return filename

    def ask(self, user_input):
        context, sources = self.retrieve_hybrid(user_input)
        
        system_prompt = f"""你是一個基於用戶 5000+ 篇個人筆記的反向教練。
你的角色是透過用戶過去的經歷、行為模式（由背景知識提供），來審核用戶現在的想法。

背景知識 (Context):
{context}

任務要求：
1. 審計用戶的想法，指出與過去一貫原則或歷史行為不符、矛盾或過度重覆的地方。
2. 必須精確引用相關筆記（格式：[[文件名]]）。
3. 語氣專業、具批判性且誠懇，像是一個長期觀察用戶的智者。"""

        try:
            res = requests.post(f"{OLLAMA_BASE_URL}/generate", json={
                "model": self.model,
                "system": system_prompt,
                "prompt": user_input,
                "stream": False,
                "options": {"temperature": 0.2}
            }, timeout=240)
            answer = res.json().get("response", "AI 響應錯誤")
        except Exception as e:
            answer = f"與 Ollama 通信超時或錯誤: {e}"
        
        # 自動存檔
        fname = self.save_qa(user_input, answer, sources)
        return answer, fname

if __name__ == "__main__":
    # 指向你的 wiki 根目錄
    wiki_base = "./rbrain-wiki"
    coach = ProfessionalCoach(wiki_base)
    
    print("="*40)
    print("🤖 RBrain Coach ")
    print(f"📂 存檔路徑: {coach.queries_dir}")
    print("="*40)

    while True:
        try:
            user_q = input("\n👤 你的思考: ").strip()
            if user_q.lower() in ['q', 'exit', 'quit']: break
            if not user_q: continue
            
            print("🧠 正在檢索歷史並構思反饋...")
            ans, saved = coach.ask(user_q)
            
            print(f"\n🧩 反向教練:\n{ans}")
            print(f"\n💾 對話已存檔: {saved}")
            
        except KeyboardInterrupt:
            break