import os
import requests
import chromadb
from tqdm import tqdm
import time

OLLAMA_API = "http://100.90.225.26:11434/api/embeddings"

class VectorIndexer:
    def __init__(self, atoms_dir, db_path="./chroma_db"):
        # 1. 路径标准化
        self.atoms_dir = os.path.abspath(atoms_dir)
        self.db_path = os.path.abspath(db_path)
        
        # 2. 初始化持久化客户端
        # PersistentClient 会自动处理目录创建
        self.client = chromadb.PersistentClient(path=self.db_path)
        
        # 3. 获取或创建集合 (使用 cosine 相似度算法，适合文本)
        self.collection = self.client.get_or_create_collection(
            name="atoms_rbrain",
            metadata={"hnsw:space": "cosine"} 
        )
        
        self.ollama_url = OLLAMA_API
        self.model = "nomic-embed-text"

    def get_embedding_with_retry(self, text, retries=3):
        """带重试机制的 Embedding 获取，防止 Ollama 偶尔超时"""
        for i in range(retries):
            try:
                response = requests.post(
                    self.ollama_url,
                    json={"model": self.model, "prompt": text},
                    timeout=30
                )
                response.raise_for_status()
                return response.json()["embedding"]
            except Exception as e:
                if i == retries - 1:
                    raise e
                time.sleep(1)

    def sync_index(self):
        """同步索引：只处理新增或修改的文件"""
        all_files = [f for f in os.listdir(self.atoms_dir) if f.endswith('.md')]
        
        # 排除副本文件 (2.md 等)
        valid_files = [f for f in all_files if not os.path.exists(os.path.join(self.atoms_dir, f[:-3] + " 2.md"))]
        
        print(f"🧠 目标目录: {self.atoms_dir}")
        print(f"📊 待处理文件: {len(valid_files)}")

        for file_name in tqdm(valid_files, desc="Indexing Atoms"):
            file_path = os.path.join(self.atoms_dir, file_name)
            
            try:
                # 获取文件的最后修改时间，作为元数据的一部分
                mtime = os.path.getmtime(file_path)
                
                with open(file_path, "r", encoding="utf-8") as f:
                    content = f.read()

                # 分离 YAML Header，只索引正文
                body_parts = content.split("---")
                # 如果有 YAML，取最后一部分；如果没有，取全文
                body = body_parts[-1].strip()
                
                if not body:
                    continue

                # 核心优化：使用 upsert 而非 add
                # 如果 id (文件名) 已存在，它会更新该向量而不会报错
                vector = self.get_embedding_with_retry(body)
                
                self.collection.upsert(
                    embeddings=[vector],
                    documents=[body],
                    metadatas=[{
                        "source": file_name, 
                        "last_modified": mtime,
                        "char_count": len(body)
                    }],
                    ids=[file_name]
                )
            except Exception as e:
                print(f"\n❌ 处理 {file_name} 时出错: {e}")

    def semantic_search(self, query_text, top_k=5):
        """反向教练模式的核心检索接口"""
        query_vector = self.get_embedding_with_retry(query_text)
        
        results = self.collection.query(
            query_embeddings=[query_vector],
            n_results=top_k,
            include=["documents", "metadatas", "distances"]
        )
        return results

# --- 执行区 ---
if __name__ == "__main__":
    # 配置你的路径
    # 既然是 Mac mini，建议把 db 放在笔记库同级的 data 文件夹下
    BASE_PATH = "./rbrain-wiki"
    ATOMS_DIR = os.path.join(BASE_PATH, "atoms")
    DB_DIR = os.path.join(BASE_PATH, "vdata/chroma_db")

    indexer = VectorIndexer(atoms_dir=ATOMS_DIR, db_path=DB_DIR)
    
    # 启动同步
    start_time = time.time()
    indexer.sync_index()
    
    print(f"\n✅ 索引同步完成！耗时: {time.time() - start_time:.2f}s")
    
    # 测试一下你的 5107 分身
    print("\n🤖 反向教练语义测试：")
    test_prompt = "分析一下我目前对风险投资的理解逻辑"
    hits = indexer.semantic_search(test_prompt, top_k=3)
    
    for i in range(len(hits['ids'][0])):
        print(f"[{i+1}] 来源: {hits['metadatas'][0][i]['source']} (距离: {hits['distances'][0][i]:.4f})")