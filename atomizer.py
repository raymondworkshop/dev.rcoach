#import ollama
import os
import re
import string
import requests
import json
from datetime import datetime
from pathlib import Path

# --- CONFIGURATION ---
# 远程服务器的 IP 和端口
OLLAMA_API = "http://100.90.225.26:11434/api/generate"
MODEL_NAME = "rbrain-qwen2.5"
INPUT_DIR = "./rbrain-wiki/raw"
ATOMS_DIR = "./rbrain-wiki/atoms-notes"
LOG_FILE = "atoms_log.json"  # 记录已处理文件的进度

CHUNK_SIZE = 500 
OVERLAP = 50 
#client = ollama.Client(host='http://192.168.1.100:11434')     

THEMES = ["self", "relationship", "work", "habit"]
ENTITY_TAGS = ["#people", "#places", "#objects", "#projects"]
CONCEPT_TAGS = ["#ideas", "#patterns", "#emotions", "#principles", "#skills", "#states"]
GARBAGE = string.punctuation + string.whitespace

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
    payload = {
        "model": MODEL_NAME,
        "prompt": prompt,
        "stream": False,  # 关闭流式输出，直接获取完整回复
        "options": {"temperature": 0.1}
    }
    
    try:
        response = requests.post(OLLAMA_API, json=payload, timeout=180)
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

def extract_original_date(file_path, filename):
    date_match = re.search(r"(\d{4}-\d{2}-\d{2})", filename)
    if date_match: return date_match.group(1)
    
    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
        head = f.read(1000)
        content_date = re.search(r"(\d{4}-\d{2}-\d{2})", head)
        if content_date: return content_date.group(1)
    
    return datetime.fromtimestamp(os.path.getmtime(file_path)).strftime('%Y-%m-%d')

def save_to_wiki(ai_output, source_file, original_date):
    if not os.path.exists(ATOMS_DIR): 
        os.makedirs(ATOMS_DIR)
    
    lines = ai_output.strip().split('\n')
    for line in lines:
        if '|' not in line: continue
        parts = [p.strip() for p in line.split('|') if p.strip()]
        if len(parts) < 4: continue
        
        entity, theme, tags, *rest = parts
        insight = ', '.join(rest)
        safe_name = entity.replace("[[", "").replace("]]", "").replace("/", "-")
        safe_name.strip(GARBAGE)
        file_path = os.path.join(ATOMS_DIR, f"{safe_name}.md")
        
        # Format tags correctly for YAML
        #tag_list = [t.strip() for t in tags.split(',')] 
        current_tag_list = [f"#{t.strip().lstrip('#')}" for t in tags.split(',')]
        current_tags_str = ", ".join(current_tag_list)

        is_new = not os.path.exists(file_path)

        with open(file_path, "a", encoding="utf-8") as f:
            if is_new:
                # 第一次创建时：写入 YAML (使用第一个主题和标签作为初始元数据)
                # 将标签列表转为 JSON 格式以符合 YAML 标准
                yaml_tags = json.dumps(current_tag_list, ensure_ascii=False)
                f.write(f"---\ntheme: {theme}\ntags: {yaml_tags}\nstatus: seed\n---\n")
                f.write(f"# {entity}\n\n")
                f.write("## 📜 历史追踪 (Historical Insights)\n\n")
                print(f"✨ Created new entity: [[{safe_name}]]")
            else:
                print(f"🔗 Merged into entity: [[{safe_name}]]")

            # 4. 写入核心内容（你想要的格式：Insight + Source + 行内 Tags）
            # 注意：\n\n 确保了条目之间的空行
            entry_content = (
                f"- **{original_date}**: {insight}\n"
                f"  *(Source: [[{source_file}]] | Tags: {current_tags_str})*\n\n"
            )
            f.write(entry_content)

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
                mtime = str(os.path.getmtime(full_path)) # 文件指纹
                #current_mtime = get_file_hash(full_path)

                # 检查逻辑：如果路径在记录中，且修改时间没变，则跳过
                if rel_path in processed_log and processed_log[rel_path] == mtime:
                    print(f"⏭️  Skip (Already processed): {rel_path}")
                    continue

                files_to_process.append((full_path, rel_path, file, mtime))

    print(f"🔍 Found {len(files_to_process)}  new or updated files: {len(files_to_process)}.")
    
    for full_path, rel_path, filename, mtime in files_to_process:
        """
        if filename != '2017-04-06-intonation-1.md':
            continue
        """
        orig_date = extract_original_date(full_path, filename)
        with open(full_path, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()

        print(f"\n📂 Processing: {rel_path} (date: {orig_date})")
    
        # 处理超长文件（2000行分块）
        for i in range(0, len(lines), CHUNK_SIZE - OVERLAP):
            chunk = "".join(lines[i : i + CHUNK_SIZE])
            prompt = f"""
            Task: Extract entities/concepts, insights from the text below.
            STRICT RULES:
            - ENTITY GUIDELINES:
               - Use concise, standardized NOUNS (e.g., use 'Perfectionism' instead of 'Feeling perfect' or 'I want to be perfect').
               - Avoid phrases or sentences. Think of it as a Wiki pagename.
               - If the entity is a person, use their full name or a consistent nickname.
            - FORMAT: EENTITY | THEME | TAGS | INSIGHT. 
               - If multiple tags apply, separate with comma.
            - CATEGORIES: 
               - Themes: {THEMES}
               - Entity Tags: {ENTITY_TAGS}
               - Concept Tags: {CONCEPT_TAGS}
               - AI can add its own if none fit.
            - CONSISTENCY: If you encounter a recurring pattern or idea, always use the EXACT SAME name for the ENTITY.
            - LANGUAGE:  Never translate the source language. Match the output language to the input language perfectly

            SOURCE TEXT:
            {chunk}
            """

            result = call_remote_ollama(prompt)
            print(f"\n--- AI 建议: (chunk {i//(CHUNK_SIZE-OVERLAP) + 1}) ---\n{result}")
            
            """
            cmd = input("\n[n]skip the file [exit]exit: ").lower()
            if cmd == 'n':
                break
            elif cmd == 'exit':
                return
            else:
                save_to_wiki(result, rel_path, orig_date)
            """
            save_to_wiki(result, rel_path, orig_date)

        # record process
        processed_log[rel_path] = mtime
        save_log(processed_log)
        print(f"✅ 已记录进度: {rel_path}")


if __name__ == "__main__":
    """
    for file in os.listdir(INPUT_DIR):
        if file.endswith(".md"): process_file(file)
    """
    #filename = "/Users/zhaowenlong/workspace/dev.rbrain/rbrain-wiki/raw/mydiary/diary/2022-03-14-yearly-review.md"
    process_file()