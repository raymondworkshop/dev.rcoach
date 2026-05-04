import os
import re
import string
import requests
import json
from datetime import datetime
from thefuzz import process, fuzz

# --- CONFIGURATION ---
OLLAMA_API = "http://100.90.225.26:11434/api/generate"
MODEL_NAME = "rbrain-qwen2.5"
INPUT_DIR = "./rbrain-wiki/raw"
ATOMS_DIR = "./rbrain-wiki/atoms"
LOG_FILE = "atoms_log.json"

SIMILARITY_THRESHOLD = 90  # 语义合并阈值
CHUNK_SIZE = 500 
OVERLAP = 50 
GARBAGE = string.punctuation + string.whitespace

THEMES = ["self", "relationship", "work", "habit"]
ENTITY_TAGS = ["#people", "#places", "#objects", "#projects"]
CONCEPT_TAGS = ["#ideas", "#patterns", "#emotions", "#principles", "#skills", "#states"]

class Atomizer:
    def __init__(self):
        self.wiki_dir = ATOMS_DIR
        self.log_file = LOG_FILE
        if not os.path.exists(self.wiki_dir):
            os.makedirs(self.wiki_dir)
        
        # 1. 开发者建议：初始化时一次性扫描目录，建立内存索引缓存
        self.existing_entities = self._load_existing_entities()
        # 2. 加载进度日志
        self.processed_log = self._load_log()
        
        print(f"📦 系统就绪 | 已加载实体: {len(self.existing_entities)} | 已处理文件: {len(self.processed_log)}")

    def _load_existing_entities(self):
        """扫描磁盘，建立词条清单"""
        return [f[:-3] for f in os.listdir(self.wiki_dir) if f.endswith('.md')]

    def _load_log(self):
        if os.path.exists(self.log_file):
            with open(self.log_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {}

    def _save_log(self):
        with open(self.log_file, 'w', encoding='utf-8') as f:
            json.dump(self.processed_log, f, indent=4, ensure_ascii=False)

    def get_normalized_entity(self, new_entity):
        """核心升级：使用内存缓存进行语义对齐合并"""
        if not self.existing_entities:
            self.existing_entities.append(new_entity)
            return new_entity

        # 快速匹配
        if new_entity in self.existing_entities:
            return new_entity

        # 模糊匹配：识别类似 "焦虑感" 与 "焦虑"
        best_match, score = process.extractOne(
            new_entity, 
            self.existing_entities, 
            scorer=fuzz.token_sort_ratio
        )
        
        if score >= SIMILARITY_THRESHOLD:
            return best_match
        
        # 如果是全新词条，加入缓存
        self.existing_entities.append(new_entity)
        return new_entity

    def call_ollama(self, prompt):
        payload = {
            "model": MODEL_NAME,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.1}
        }
        try:
            response = requests.post(OLLAMA_API, json=payload, timeout=240)
            response.raise_for_status()
            return response.json()['response']
        except Exception as e:
            return f"Error: {e}"

    def extract_date(self, file_path, filename):
        date_match = re.search(r"(\d{4}-\d{2}-\d{2})", filename)
        if date_match: return date_match.group(1)
        
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            head = f.read(1000)
            content_date = re.search(r"(\d{4}-\d{2}-\d{2})", head)
            if content_date: return content_date.group(1)
        
        return datetime.fromtimestamp(os.path.getmtime(file_path)).strftime('%Y-%m-%d')

    def save_to_atoms(self, ai_output, source_rel_path, original_date):
        """将 AI 提取的洞察合并到对应的原子笔记中"""
        lines = ai_output.strip().split('\n')
        for line in lines:
            if '|' not in line: continue
            if not re.search(r"[a-zA-Z\u4e00-\u9fa5]", line): continue
            parts = [p.strip() for p in line.split('|') if p.strip()]
            if len(parts) < 4: continue
            
            entity_raw, theme, tags_raw, *rest = parts
            insight = ', '.join(rest)

            # 语义对齐
            entity = self.get_normalized_entity(entity_raw)
            
            # 安全文件名处理
            _safe_name = entity.replace("[[", "").replace("]]", "").replace("/", "-")
            safe_name = _safe_name.strip(GARBAGE)
            file_path = os.path.join(self.wiki_dir, f"{safe_name}.md")
            
            # 标签处理
            current_tags = [f"#{t.strip().lstrip('#')}" for t in tags_raw.split(',')]
            tags_str = ", ".join(current_tags)

            print(f"{entity} | {theme} | {tags_str} | {insight}")
            is_new = not os.path.exists(file_path)

            with open(file_path, "a", encoding="utf-8") as f:
                if is_new:
                    yaml_header = {
                        "theme": theme,
                        "tags": current_tags,
                        "status": "seed",
                        "initialized": datetime.now().strftime('%Y-%m-%d')
                    }
                    f.write(f"---\n{json.dumps(yaml_header, ensure_ascii=False, indent=2)}\n---\n")
                    f.write(f"# {entity}\n\n## 📜 历史追踪 (Historical Insights)\n\n")
                    print(f"✨ [New Entry] [[{safe_name}]]")
                
                # 追加内容格式
                entry = (
                    f"- **{original_date}**: {insight}\n"
                    f"  *(Source: [[{source_rel_path}]] | Tags: {tags_str})*\n\n"
                )
                f.write(entry)
                
                if not is_new:
                    status = f"归一化 -> [[{entity}]]" if entity != entity_raw else "物理追加"
                    print(f"🔗 [Merge] {entity_raw} ({status})")

    def run(self):
        """遍历并处理所有文件"""
        files_to_process = []
        for root, _, files in os.walk(INPUT_DIR):
            for file in files:
                if file.endswith(".md"):
                    full_path = os.path.join(root, file)
                    rel_path = os.path.relpath(full_path, INPUT_DIR)
                    mtime = str(os.path.getmtime(full_path))

                    if rel_path in self.processed_log and self.processed_log[rel_path] == mtime:
                        continue
                    files_to_process.append((full_path, rel_path, file, mtime))

        print(f"🔍 待处理/更新的文件数: {len(files_to_process)}")

        for full_path, rel_path, filename, mtime in files_to_process:
            orig_date = self.extract_date(full_path, filename)
            with open(full_path, 'r', encoding='utf-8', errors='ignore') as f:
                content_lines = f.readlines()

            print(f"\n📂 正在处理: {rel_path} ({orig_date})")

            for i in range(0, len(content_lines), CHUNK_SIZE - OVERLAP):
                chunk = "".join(content_lines[i : i + CHUNK_SIZE])
                prompt = f"""
                Task: Extract entities/concepts from the text below.
                STRICT RULES:
                - ENTITY GUIDELINES:
                   - Use consistent, atomic NOUNS (e.g., 'Perfectionism' instead of 'Feeling perfect').
                   - Avoid phrases. Think of it as a Wiki pagename.
                   - If a person, use full name or consistent nickname.
                - FORMAT: ENTITY | THEME | TAGS | INSIGHT
                - CATEGORIES: 
                   - Themes: {THEMES}
                   - Entity Tags: {ENTITY_TAGS}
                   - Concept Tags: {CONCEPT_TAGS}
                - CONSISTENCY: Use EXACT SAME name for recurring patterns.
                - LANGUAGE: Never translate. Match input language.

                SOURCE TEXT:
                {chunk}
                """
                ai_result = self.call_ollama(prompt)
                self.save_to_atoms(ai_result, rel_path, orig_date)

            # 更新进度
            self.processed_log[rel_path] = mtime
            self._save_log()
            print(f"✅ 进度已记录: {rel_path}")

if __name__ == "__main__":
    atomizer = Atomizer()
    atomizer.run()