import os
import requests
import chromadb
import datetime
import re

MODEL_NAME = "rbrain-qwen2.5"

class ProfessionalCoach:
    def __init__(self, atoms_dir, queries_dir, db_path):
        # 确保路径为绝对路径，防止 Mac mini 下相对路径失效
        self.atoms_dir = os.path.abspath(atoms_dir)
        self.queries_dir = os.path.abspath(queries_dir)
        
        # 1. 连接数据库
        self.client = chromadb.PersistentClient(path=os.path.abspath(db_path))
        self.collection = self.client.get_collection(name="atoms_rbrain")
        
        self.ollama_url = "http://localhost:11434/api"
        self.model = MODEL_NAME 

    def _get_embedding(self, text):
        """调用 Ollama 获取向量"""
        res = requests.post(f"{self.ollama_url}/embeddings", json={
            "model": "nomic-embed-text",
            "prompt": text
        })
        return res.json()["embedding"]

    def retrieve_hybrid(self, query_text, n=6):
        """
        混合检索核心逻辑：关键词路 + 向量路
        """
        query_vec = self._get_embedding(query_text)
        
        # --- 路径 A: 向量检索 (Semantic) ---
        vector_res = self.collection.query(
            query_embeddings=[query_vec],
            n_results=n
        )

        # --- 路径 B: 关键词检索 (Lexical/Metadata) ---
        # 提取问题中的核心词 (长度 > 1)
        kws = [w for w in re.split(r'\W+', query_text) if len(w) > 1]
        
        kw_docs = []
        kw_sources = []
        if kws:
            # 使用 $contains 算子在 metadata 的 keywords 字段中查找
            # 只要命中其中一个关键词即可
            kw_res = self.collection.get(
                where={"keywords": {"$contains": kws[0]}}, 
                limit=3
            )
            kw_docs = kw_res.get("documents", [])
            kw_sources = [m["source"] for m in kw_res.get("metadatas", [])]

        # --- 合并去重 ---
        combined_docs = kw_docs + vector_res['documents'][0]
        combined_sources = kw_sources + [m['source'] for m in vector_res['metadatas'][0]]
        
        # 使用 dict.fromkeys 去重并保持顺序
        final_sources = list(dict.fromkeys(combined_sources))[:n]
        # 根据去重后的 source 重新整理 context (简化处理)
        return "\n---\n".join(combined_docs[:n]), final_sources

    def save_qa(self, question, answer, sources):
        """存档到 queries 目录，适配 Backlinker 格式"""
        if not os.path.exists(self.queries_dir):
            os.makedirs(self.queries_dir)
            
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M")
        # 过滤非法字符做文件名
        safe_q = "".join(x for x in question[:15] if x.isalnum() or x in " _-")
        filename = f"Q_{timestamp}_{safe_q}.md"
        
        # 构造带有双链链接的内容
        links = [f"[[{s[:-3]}]]" for s in sources]
        content = f"""---
type: coach_qa
date: {datetime.datetime.now().isoformat()}
tags: #query #reverse-coach
---

# User Query
{question}

# Coach Response
{answer}

# Related Atoms
{", ".join(links)}

---
Source: [[my-ai-coach]]
"""
        with open(os.path.join(self.queries_dir, filename), "w", encoding="utf-8") as f:
            f.write(content)
        return filename

    def ask(self, user_input):
        # 1. 混合检索背景
        context, sources = self.retrieve_hybrid(user_input)
        
        # 2. 构造 System Prompt
        system_prompt = f"""你是一个基于用户 5107 篇个人笔记的反向教练。
        背景知识：
        {context}
        
        任务：
        1. 审计用户的想法，指出与过去笔记原则不符的地方。
        2. 引用相关的笔记名称（如 [[文件名]]）。
        3. 逻辑严密，语气专业。"""

        # 3. 生成回答
        res = requests.post(f"{self.ollama_url}/generate", json={
            "model": self.model,
            "system": system_prompt,
            "prompt": user_input,
            "stream": False
        })
        
        answer = res.json().get("response", "AI 响应错误")
        
        # 4. 自动存档
        fname = self.save_qa(user_input, answer, sources)
        return answer, fname

if __name__ == "__main__":
    # 配置你的本地路径
    BASE_DIR = "./rbrain-wiki"
    ATOMS_DIR = os.path.join(BASE_DIR, "atoms")
    QUERIES_DIR = os.path.join(BASE_DIR, "raw/queries")
    DB_DIR = os.path.join(BASE_DIR, "vdata/chroma_db")


    coach = ProfessionalCoach(ATOMS_DIR, QUERIES_DIR, DB_DIR)
    
    print("="*20)
    print("🤖 反向教练 (Hybrid Search Mode) 已就绪")
    print("👉 输入你的思考开始对话")
    print("👉 输入 'q', 'exit' 或 'quit' 结束并保存")
    print("="*20)

    while True:
        try:
            user_q = input("\n👤 你的思考: ").strip()
            
            # 1. 退出检查逻辑
            if user_q.lower() in ['q', 'exit', 'quit', '退出']:
                print("\n👋 正在关闭教练系统... 今天的深度思考已存档。")
                break
            
            # 2. 空输入检查
            if not user_q:
                continue
            
            # 3. 执行核心逻辑
            print("🧠 检索并思考中...")
            ans, saved = coach.ask(user_q)
            
            print(f"\n🧩 反向教练:\n{ans}")
            print(f"\n💾 对话已存档: {saved}")
            
        except KeyboardInterrupt:
            # 处理 Ctrl+C 强制退出
            print("\n\n⚠️ 检测到强制中断，正在安全退出...")
            break

    print("🏁 运行结束。保持思考，再见！")