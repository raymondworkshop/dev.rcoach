import os
import re
import json
import spacy
import yaml
import requests
import hashlib
import signal
import sys
from datetime import datetime
from thefuzz import process, fuzz
from tqdm import tqdm
from opencc import OpenCC

# --- 核心配置 ---
OLLAMA_API = "http://100.90.225.26:11434/api/generate"
MODEL_NAME = "rbrain-qwen2.5" 
INPUT_DIR = os.path.abspath("./rbrain-wiki/raw")
ATOMS_DIR = os.path.abspath("./rbrain-wiki/atoms")
LOG_FILE = "atoms_process_log.json"

class WikiAtomizer:
    def __init__(self):
        self.wiki_dir = ATOMS_DIR
        if not os.path.exists(self.wiki_dir): os.makedirs(self.wiki_dir)
        
        self.cc = OpenCC('s2t')
        self.themes = ["self", "work", "relationships", "actions"]
        
        # --- 中斷機制初始化 ---
        self.stop_requested = False
        signal.signal(signal.SIGINT, self._handle_interrupt)
        
        print("⏳ 正在加載 NLP 模型...")
        try:
            self.nlp_zh = spacy.load("zh_core_web_sm")
            self.nlp_en = spacy.load("en_core_web_sm")
            print("✅ NLP 模型加載成功")
        except Exception as e:
            print(f"❌ 模型加載失敗: {e}")
            exit(1)

        self.entity_cache = [f[:-3] for f in os.listdir(self.wiki_dir) if f.endswith('.md')]
        self.processed_log = self._load_log()

    def _handle_interrupt(self, signum, frame):
        """處理 Ctrl+C 信號"""
        print("\n\n🛑 接收到中斷指令！正在等待當前段落處理完成並安全保存進度...")
        self.stop_requested = True

    def _load_log(self):
        if os.path.exists(LOG_FILE):
            try:
                with open(LOG_FILE, 'r', encoding='utf-8') as f: 
                    return json.load(f)
            except json.JSONDecodeError:
                return {}
        return {}

    def _save_log(self):
        """原子化保存日誌，防止寫入中斷損壞文件"""
        temp_file = LOG_FILE + ".tmp"
        with open(temp_file, 'w', encoding='utf-8') as f:
            json.dump(self.processed_log, f, indent=4, ensure_ascii=False)
        os.replace(temp_file, LOG_FILE)

    def get_file_hash(self, filepath):
        hasher = hashlib.md5()
        with open(filepath, 'rb') as f:
            hasher.update(f.read())
        return hasher.hexdigest()

    def get_normalized_name(self, name):
        t_name = self.cc.convert(name.strip())
        if not self.entity_cache: return t_name
        if t_name in self.entity_cache: return t_name
        # 提高匹配閾值以保證精確度
        match, score = process.extractOne(t_name, self.entity_cache, scorer=fuzz.token_set_ratio)
        return match if score >= 95 else t_name

    def extract_entities(self, text):
        is_zh = any('\u4e00' <= c <= '\u9fff' for c in text)
        doc = self.nlp_zh(text) if is_zh else self.nlp_en(text)
        found = set()
        for ent in doc.ents:
            if ent.label_ not in ["DATE", "TIME", "MONEY", "QUANTITY"]:
                found.add((ent.text.strip(), ent.label_))
        
        current_noun = ""
        for token in doc:
            if token.pos_ in ["NOUN", "PROPN"]:
                current_noun += token.text
            else:
                if len(current_noun) > 1: 
                    found.add((current_noun, "CONCEPT"))
                current_noun = ""
        return list(found)

    def call_qwen_audit(self, entity, context):
        is_zh = any('\u4e00' <= c <= '\u9fff' for c in context)
        lang_style = "Match the exact Chinese style (Traditional) of the context." if is_zh else "Use English."
        prompt = f"""
        Task: Audit the entity [[{entity}]] from the provided text.
        Style Guide: {lang_style}
        [Audit Framework]
        - insight: Concise summary of what happened, Fact-based.
        - logic: Root Cause Analysis. For long essays, find systemic patterns. For fragments/diaries, find the mental trigger.
        - related: Essential causal or logical links to other entities.
        - tags: 2-3 English tags.
        
        Text: {context[:1200]}
        Output JSON:
        {{ "theme": "one of {self.themes}", "tags": [], "insight": "", "logic": "", "related": [] }}
        """
        payload = {"model": MODEL_NAME, "prompt": prompt, "stream": False, "format": "json"}
        try:
            # 設置長超時以應對本地 LLM 響應波動
            res = requests.post(OLLAMA_API, json=payload, timeout=240)
            return json.loads(res.json()['response'])
        except: return None

    def save_to_wiki(self, entity_name, label, audit, rel_source_path, date):
        name = self.get_normalized_name(entity_name)
        safe_name = re.sub(r'[\\/:*?"<>|]', '_', name)
        file_path = os.path.join(self.wiki_dir, f"{safe_name}.md")
        is_new = not os.path.exists(file_path)
        
        audit_data = audit or {}
        # 獲取繁體化的關聯實體名
        related = [self.cc.convert(str(r)) for r in audit_data.get('related', [])]
        # 基於相對目錄的 Source Link
        source_link = f"../raw/{rel_source_path}"
        theme = audit_data.get('theme', 'self')
        tags = audit_data.get('tags', [])
        insight = audit_data.get('insight')
        logic = audit_data.get('logic')

        print(f"{entity_name} | {theme} | {tags} | {insight} |  {logic} | {related}")

        with open(file_path, "a", encoding="utf-8") as f:
            if is_new:
                meta = {
                    "theme": theme, 
                    "tags": tags, 
                    "label": label, 
                    "last_audit": date
                }
                f.write(f"---\n{yaml.dump(meta, allow_unicode=True)}---\n# {name}\n\n## 📜 Trace (演進軌跡)\n\n")
                if name not in self.entity_cache: self.entity_cache.append(name)
            
            entry = f"- **{date}**: {insight}\n"
            if logic: entry += f"  > **Logic**: {logic}\n"
            rel_links = ", ".join([f"[[{r}]]" for r in related])
            entry += f"  *(Source: [[{source_link}]] | Related: {rel_links})*\n\n"
            f.write(entry)

    def run(self):
        # 1. 預掃描所有 MD 文件
        all_files = []
        for root, _, files in os.walk(INPUT_DIR):
            for filename in files:
                if filename.endswith(".md"):
                    all_files.append(os.path.join(root, filename))
        
        print(f"🚀 開始審計任務 | 總文件數: {len(all_files)}")

        for full_path in tqdm(all_files, desc="Total Files"):
            # 中斷檢查點 A
            if self.stop_requested: break
            
            rel_path = os.path.relpath(full_path, INPUT_DIR)
            file_hash = self.get_file_hash(full_path)

            if self.processed_log.get(rel_path) == file_hash:
                continue

            # 日期提取
            date_match = re.search(r"(\d{4}-\d{2}-\d{2})", os.path.basename(full_path))
            date = date_match.group(1) if date_match else datetime.now().strftime("%Y-%m-%d")

            print(f"\n📂 processing: {rel_path}")

            with open(full_path, 'r', encoding='utf-8') as f:
                content = f.read()

            # 段落解析
            paragraphs = [p.strip() for p in content.split('\n\n') if len(p.strip()) > 5]
            
            for p in paragraphs:
                # 中斷檢查點 B (在昂貴的調用前)
                if self.stop_requested: break
                
                atoms = self.extract_entities(p)
                for ent_name, label in atoms:
                    if self.stop_requested: break
                    audit_res = self.call_qwen_audit(ent_name, p)
                    self.save_to_wiki(ent_name, label, audit_res, rel_path, date)

            # 完成一個文件的更新後立即保存日誌
            if not self.stop_requested:
                self.processed_log[rel_path] = file_hash
                self._save_log()
            else:
                print(f"\n⚠️ {rel_path} 處理被中斷，進度未保存。")
                break

        if self.stop_requested:
            print("\n👋 任務已安全中止。進度已同步。下次啟動將自動跳過已完成部分。")
        else:
            print("\n✅ 任務圓滿完成。")

if __name__ == "__main__":
    WikiAtomizer().run()