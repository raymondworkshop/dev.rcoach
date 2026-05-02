import os
import re
import json
import hashlib
from tqdm import tqdm

# --- 配置區 (與 Atomizer 絕對一致) ---
BASE_DIR = os.path.abspath("./rbrain-wiki")
INPUT_DIR = os.path.join(BASE_DIR, "raw")
ATOMS_DIR = os.path.join(BASE_DIR, "atoms")
LOG_PATH = os.path.join(BASE_DIR, "backlink_process_log.json")

class WikiBacklinker:
    def __init__(self):
        self.backlinks_map = {} # { "raw_path": set(atoms) }
        self.log = self._load_log()

    def _load_log(self):
        if os.path.exists(LOG_PATH):
            try:
                with open(LOG_PATH, 'r', encoding='utf-8') as f: return json.load(f)
            except: return {}
        return {}

    def get_hash(self, path):
        hasher = hashlib.md5()
        with open(path, 'rb') as f: hasher.update(f.read())
        return hasher.hexdigest()

    def run(self):
        # 1. 全量掃描 Atoms (不管 Atomizer 跑了沒，這裡都重新掃一遍目錄)
        print("🔍 掃描 Atoms 目錄並提取反向引用...")
        if not os.path.exists(ATOMS_DIR): return
        
        atom_files = [f for f in os.listdir(ATOMS_DIR) if f.endswith('.md')]
        for f_name in tqdm(atom_files, desc="Indexing"):
            atom_name = f_name[:-3]
            with open(os.path.join(ATOMS_DIR, f_name), 'r', encoding='utf-8') as f:
                content = f.read()
                # 關鍵：尋找所有指向原始文件的 Source 標籤
                sources = re.findall(r"Source: \[\[\.\./raw/(.*?)\]\]", content)
                for src in sources:
                    if src not in self.backlinks_map: self.backlinks_map[src] = set()
                    self.backlinks_map[src].add(atom_name)

        # 2. 同步至 Raw 文件
        print("📝 正在將原子連結織回原始筆記...")
        new_log = {}
        
        # 遍歷所有被引用過的 Raw 文件
        for rel_path, atoms in self.backlinks_map.items():
            full_path = os.path.abspath(os.path.join(INPUT_DIR, rel_path))
            if not os.path.exists(full_path): continue

            # 計算當前 Atoms 集合的特徵
            sorted_atoms = sorted(list(atoms))
            atoms_sig = hashlib.md5("".join(sorted_atoms).encode()).hexdigest()
            raw_hash = self.get_hash(full_path)

            # 增量判斷：如果 Raw 內容和 Atoms 集合都沒變，跳過
            if self.log.get(rel_path, {}).get('sig') == atoms_sig and \
               self.log.get(rel_path, {}).get('hash') == raw_hash:
                new_log[rel_path] = self.log[rel_path]
                continue

            # 執行寫入
            print(f" ✨ 更新: {rel_path}")
            with open(full_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()

            # 移除舊的 Generated Atoms 區塊
            new_content = []
            skip = False
            for line in lines:
                if "### 🧠 Generated Atoms" in line:
                    skip = True
                    # 順便移除區塊上方的分隔線
                    if new_content and "---" in new_content[-1]:
                        new_content.pop()
                    continue
                if skip and line.strip() == "": continue # 跳過區塊內的空行
                if skip and not line.strip().startswith("[[") and not line.strip().startswith(", "):
                    skip = False # 區塊結束
                if not skip:
                    new_content.append(line)

            # 重新封裝內容並追加新區塊
            final_text = "".join(new_content).rstrip()
            final_text += f"\n\n---\n\n### 🧠 Generated Atoms\n"
            final_text += ", ".join([f"[[{a}]]" for a in sorted_atoms]) + "\n"

            with open(full_path, 'w', encoding='utf-8') as f:
                f.write(final_text)

            # 更新日誌
            new_log[rel_path] = {'sig': atoms_sig, 'hash': self.get_hash(full_path)}

        # 3. 儲存日誌
        with open(LOG_PATH, 'w', encoding='utf-8') as f:
            json.dump(new_log, f, indent=4)
        print("✅ Backlinker 同步完成。")

if __name__ == "__main__":
    WikiBacklinker().run()