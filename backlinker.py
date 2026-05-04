import os
import re
import json
import yaml
import hashlib
import signal
import sys
from pathlib import Path
from tqdm import tqdm

from rbrain_config import get_config

# --- 核心配置 (需與 WikiAtomizer 保持一致) ---
_cfg = get_config()
ATOMS_DIR = _cfg["atoms_dir"]
LOG_FILE = os.environ.get(
    "RBRAIN_BACKLINKS_LOG", str(Path(__file__).resolve().parent / "backlinks_process_log.json")
)

class BackLinker:
    def __init__(self):
        self.wiki_dir = ATOMS_DIR
        if not os.path.exists(self.wiki_dir):
            print(f"❌ 錯誤: 目錄 {self.wiki_dir} 不存在")
            exit(1)
        
        # 中斷機制
        self.stop_requested = False
        signal.signal(signal.SIGINT, self._handle_interrupt)
        
        self.processed_log = self._load_log()
        # 內存中的索引：{entity_name: set_of_referrers}
        self.index = {}
        self.affected_targets = set()
        self.changed_files = []
        self.deleted_files = []

    def _handle_interrupt(self, signum, frame):
        print("\n\n🛑 [INTERRUPT] 正在保存當前索引進度並退出...")
        self.stop_requested = True

    def _load_log(self):
        if os.path.exists(LOG_FILE):
            try:
                with open(LOG_FILE, 'r', encoding='utf-8') as f:
                    raw = json.load(f)
                parsed = {}
                for key, value in raw.items():
                    if isinstance(value, str):
                        parsed[key] = {"hash": value, "links": []}
                    elif isinstance(value, dict):
                        parsed[key] = {"hash": value.get("hash", ""), "links": value.get("links", [])}
                    else:
                        parsed[key] = {"hash": "", "links": []}
                return parsed
            except:
                return {}
        return {}

    def _save_log(self):
        temp_log = LOG_FILE + ".tmp"
        with open(temp_log, 'w', encoding='utf-8') as f:
            json.dump(self.processed_log, f, indent=4, ensure_ascii=False)
        os.replace(temp_log, LOG_FILE)

    def get_file_hash(self, filepath):
        hasher = hashlib.md5()
        with open(filepath, 'rb') as f:
            hasher.update(f.read())
        return hasher.hexdigest()

    def parse_metadata(self, content):
        """解析 YAML Frontmatter 中的標籤"""
        try:
            if content.startswith('---'):
                parts = content.split('---', 2)
                if len(parts) >= 3:
                    return yaml.safe_load(parts[1])
        except: pass
        return {}

    def extract_links(self, content):
        """提取 Obsidian 格式的雙鏈 [[Entity]]，同時支持別名 [[target|alias]]。"""
        links = re.findall(r'\[\[([^\]]+?)\]\]', content)
        results = []
        for link in links:
            if '/' in link or '..' in link:
                continue
            target = link.split('|', 1)[0].strip()
            if target:
                results.append(target)
        return results

    def build_index(self):
        """掃描所有 atoms 文件，建立反向引用關係，只重寫受影響的 Backlinks。"""
        files = [f for f in os.listdir(self.wiki_dir) if f.endswith('.md')]
        print(f"🔍 正在索引 {len(files)} 個實體...")

        current_files = set(files)
        previous_files = set(self.processed_log.keys())
        deleted_filenames = previous_files - current_files

        current_info = {}
        for filename in tqdm(files, desc="Indexing"):
            if self.stop_requested:
                break

            file_path = os.path.join(self.wiki_dir, filename)
            current_entity = filename[:-3]
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()

            links = self.extract_links(content)
            file_hash = self.get_file_hash(file_path)
            current_info[filename] = {"hash": file_hash, "links": links}

            old_entry = self.processed_log.get(filename, {"hash": None, "links": []})
            if old_entry.get("hash") != file_hash:
                self.changed_files.append(filename)
                old_links = set(old_entry.get("links", []))
                new_links = set(links)
                self.affected_targets |= new_links | old_links

            for target in links:
                if '/' in target or '..' in target:
                    continue
                self.index.setdefault(target, set()).add(current_entity)

        for filename in deleted_filenames:
            old_links = set(self.processed_log.get(filename, {}).get("links", []))
            self.deleted_files.append(filename)
            self.affected_targets |= old_links

        self.processed_log = current_info
        self.deleted_files = list(deleted_filenames)

    def update_backlinks_section(self):
        """將反向鏈接寫入受影響文件的 ## 🔗 Backlinks 章節"""
        if not self.affected_targets:
            print("📝 沒有需要更新的 Backlinks。")
            return

        print("📝 正在寫入反向鏈接...")
        for entity in tqdm(sorted(self.affected_targets), desc="Updating Files"):
            if self.stop_requested:
                break

            safe_name = re.sub(r'[\\/:*?"<>|#]', '_', entity)
            file_path = os.path.join(self.wiki_dir, f"{safe_name}.md")
            if not os.path.exists(file_path):
                continue

            referrers = self.index.get(entity, set())
            with open(file_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()

            new_lines = []
            for line in lines:
                if line.startswith('## 🔗 Backlinks'):
                    break
                new_lines.append(line)

            if new_lines and not new_lines[-1].endswith('\n'):
                new_lines[-1] = new_lines[-1] + '\n'
            if not new_lines or new_lines[-1].strip() != '':
                new_lines.append('\n')

            backlink_content = '## 🔗 Backlinks\n'
            for ref in sorted(referrers):
                backlink_content += f'- [[{ref}]]\n'
            backlink_content += '\n'

            with open(file_path, 'w', encoding='utf-8') as f:
                f.writelines(new_lines)
                f.write(backlink_content)

    def run(self):
        self.build_index()
        if not self.stop_requested:
            self.update_backlinks_section()
            self._save_log()

            print("\n✅ 反向鏈接同步完成。")
            print(f"  - 已更新文件: {len(self.changed_files)}")
            if self.deleted_files:
                print(f"  - 已刪除文件: {len(self.deleted_files)} ({', '.join(self.deleted_files)})")
        else:
            print("\n👋 操作已中斷。")

if __name__ == "__main__":
    BackLinker().run()