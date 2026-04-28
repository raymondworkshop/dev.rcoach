#import ollama
import os
import re
import requests
from datetime import datetime

# --- CONFIGURATION ---
INPUT_DIR = "./rbrain-wiki/raw"
WIKI_DIR = "./rbrain-wiki/atoms-notes"
MODEL = "rbrain-qwen2.5"
CHUNK_SIZE = 500 
OVERLAP = 50 
#client = ollama.Client(host='http://192.168.1.100:11434')     

THEMES = ["self", "relationship", "work", "habit"]

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

def process_file(filename):
    file_path = os.path.join(INPUT_DIR, filename)
    orig_date = extract_original_date(file_path, filename)
    
    with open(file_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    print(f"\n📂 Source: {filename} | Date: {orig_date}")
    
    for i in range(0, len(lines), CHUNK_SIZE - OVERLAP):
        chunk = "".join(lines[i : i + CHUNK_SIZE])
        prompt = f"""
        Extract entities/concepts from the text below.
        Categories: {THEMES}
        Mandatory Tag Groups: 
        - Entities: #people, #places, #objects, #projects
        - Concepts: #ideas, #patterns, #emotions, #principles, #skills, #states
        AI can add its own if none fit.
        
        OUTPUT: ENTITY | THEME | TAGS | INSIGHT
        TEXT: {chunk}
        """
        raw_output = call_remote_ollama(prompt)
        print(f"\nProposed Atoms:\n{raw_output}")
        #if input("\nSave? (y/n): ").lower() == 'y':
        save_to_wiki(raw_output, filename, orig_date)

if __name__ == "__main__":
    """
    for file in os.listdir(INPUT_DIR):
        if file.endswith(".md"): process_file(file)
    """
    filename = "/Users/zhaowenlong/workspace/dev.rbrain/rbrain-wiki/raw/mydiary/diary/2022-03-14-yearly-review.md"
    process_file(filename)