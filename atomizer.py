#import ollama
import os
import re
import requests
import json
from datetime import datetime
from pathlib import Path

# --- CONFIGURATION ---
INPUT_DIR = "./rbrain-wiki/raw"
WIKI_DIR = "./rbrain-wiki/atoms-notes"
MODEL = "rbrain-qwen2.5"
CHUNK_SIZE = 500 
OVERLAP = 50 
LOG_FILE = "atoms_log.json"  # 记录已处理文件的进度
#client = ollama.Client(host='http://192.168.1.100:11434')     

THEMES = ["self", "relationship", "work", "habit"]
ENTITY_TAGS = ["#people", "#places", "#objects", "#projects"]
CONCEPT_TAGS = ["#ideas", "#patterns", "#emotions", "#principles", "#skills", "#states"]


def load_log():
    """加载已处理文件的记录"""
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def save_log(log_data):
    """保存进度记录"""
    with open(LOG_FILE, 'w', encoding='utf-8') as f:
        json.dump(log_data, f, indent=4, ensure_ascii=False)

def get_file_hash(file_path):
    """获取文件修改时间作为唯一标识，如果文件内容变了，则需要重新处理"""
    return str(os.path.getmtime(file_path))

def call_remote_ollama(prompt):
    # 远程服务器的 IP 和端口
    url = "http://100.90.225.26:11434/api/generate"
    
    payload = {
        "model": "rbrain-qwen2.5",
        "prompt": prompt,
        "stream": False  # 关闭流式输出，直接获取完整回复
    }
    
    try:
        response = requests.post(url, json=payload)
        response.raise_for_status() # 检查状态码
        return response.json()['response']
    except Exception as e:
        return f"Request Error: {e}"

def extract_original_date(file_path, filename):
    date_match = re.search(r"(\d{4}-\d{2}-\d{2})", filename)
    if date_match: return date_match.group(1)
    
    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
        head = f.read(1000)
        content_date = re.search(r"(\d{4}-\d{2}-\d{2})", head)
        if content_date: return content_date.group(1)
    
    return datetime.fromtimestamp(os.path.getmtime(file_path)).strftime('%Y-%m-%d')

def save_to_wiki(ai_output, source_file, original_date):
    if not os.path.exists(WIKI_DIR): os.makedirs(WIKI_DIR)
    
    lines = ai_output.strip().split('\n')
    for line in lines:
        if '|' not in line: continue
        parts = [p.strip() for p in line.split('|') if p.strip()]
        if len(parts) < 4: continue
        
        entity, theme, tags, insight = parts
        safe_name = entity.replace("[[", "").replace("]]", "").replace("/", "-")
        file_path = os.path.join(WIKI_DIR, f"{safe_name}.md")
        
        # Format tags correctly for YAML
        tag_list = [t.strip() for t in tags.split(',')]
        
        is_new = not os.path.exists(file_path)
        with open(file_path, "a", encoding="utf-8") as f:
            if is_new:
                f.write(f"---\ntheme: {theme}\ntags: {tag_list}\noriginal_date: {original_date}\nsource: \"[[{source_file}]]\"\nstatus: seed\n---\n")
                f.write(f"# {entity}\n")
            f.write(f"\n- **{original_date}**: {insight} (Ref: [[{source_file}]])\n")

def process_file():
    """使用 os.walk 遍历所有子目录"""
    # 记录已处理文件，防止重复（可选）
    processed_log = load_log()
    files_to_process = []

    for root, dirs, files in os.walk(INPUT_DIR):
        for file in files:
            if file.endswith(".md"):
                full_path = os.path.join(root, file)
                # 计算相对路径，方便在 Obsidian 中点击链接
                rel_path = os.path.relpath(full_path, INPUT_DIR)
                current_mtime = get_file_hash(full_path)

                # 检查逻辑：如果路径在记录中，且修改时间没变，则跳过
                if rel_path in processed_log and processed_log[rel_path] == current_mtime:
                    print(f"⏭️  Skip (Already processed): {rel_path}")
                    continue

                files_to_process.append((full_path, rel_path, file))

    print(f"🔍 Found {len(files_to_process)}  new or updated files.")
    
    for full_path, rel_path, filename in files_to_process:
        """
        if filename == '2022-08-08-the-log-of-your-life.md' or  filename == '2023-09-04-the-log-of-your-life.md' or  filename == '2024-01-02-the-log-of-your-life.md':
            return
        """
        orig_date = extract_original_date(full_path, filename)
        
        with open(full_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        print(f"\n📂 Processing: {rel_path} | date: {orig_date}")

        # 标记是否成功保存了至少一个片段
        saved_successfully = False
    
        # 处理超长文件（2000行分块）
        for i in range(0, len(lines), CHUNK_SIZE - OVERLAP):
            chunk = "".join(lines[i : i + CHUNK_SIZE])
            prompt = f"""
            Task: Extract entities/concepts from the text below.
            - Themes: {THEMES}
            - Entity Tags: {ENTITY_TAGS}
            - Concept Tags: {CONCEPT_TAGS}
            AI can add its own if none fit.
            
            STRICT RULES:
            1. LANGUAGE: Use the SAME LANGUAGE as the source text. DO NOT translate.
            2. If multiple tags apply, separate with comma.
            
            OUTPUT: ENTITY | THEME | TAGS | INSIGHT
            TEXT: {chunk}
            """
            
        result = call_remote_ollama(prompt)
        print(f"\n--- Proposed Atoms: (chunk {i//(CHUNK_SIZE-OVERLAP) + 1}) ---\n{result}")
            
        save_to_wiki(result, rel_path, orig_date)
        saved_successfully = True
 
        """
        cmd = input("\n[n]skip the file [exit]exit: ").lower()
        if cmd == 'n':
            break
        elif cmd == 'exit':
            return
        else:
            save_to_wiki(result, rel_path, orig_date)
        """


if __name__ == "__main__":
    """
    for file in os.listdir(INPUT_DIR):
        if file.endswith(".md"): process_file(file)
    """
    #filename = "/Users/zhaowenlong/workspace/dev.rbrain/rbrain-wiki/raw/mydiary/diary/2022-03-14-yearly-review.md"
    process_file()