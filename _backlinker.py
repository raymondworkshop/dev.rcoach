import os
import re
import time

class Backlinker:
    def __init__(self, atoms_dir, min_length=2, whitelist=None):
        self.atoms_dir = os.path.abspath(atoms_dir)
        self.min_length = min_length
        self.whitelist = set(whitelist) if whitelist else set()
        self.entities = self._get_current_entities()
        self.pattern_cache = self._precompile_patterns()

    def _get_current_entities(self):
        if not os.path.exists(self.atoms_dir): return []
        
        all_files = os.listdir(self.atoms_dir)
        names = []
        for f in all_files:
            if f.endswith('.md'):
                name = f[:-3]
                if re.search(r'\s\d+$|\(\d+\)$', name): continue
                names.append(name)
        
        names.sort(key=len, reverse=True)
        return names

    def _precompile_patterns(self):
        cache = {}
        for entity in self.entities:
            escaped = re.escape(entity)
            if entity.isascii():
                p = rf"(?<!\[\[)(?<![a-zA-Z0-9])({escaped})(?![a-zA-Z0-9])(?!\]\])"
            else:
                p = rf"(?<!\[\[)({escaped})(?!\]\])"
            cache[entity] = re.compile(p, re.IGNORECASE)
        return cache

    def apply_backlinks(self):
        if not self.entities: return

        start_time = time.time()
        print(f"🖥️  Mac mini 已就绪 | 目标规模: {len(self.entities)} 个原子笔记")
        
        updated_count = 0
        total_scanned = 0
        entity_size = len(self.entities)

        for root, _, files in os.walk(self.atoms_dir):
            md_files = [f for f in files if f.endswith(".md") and not re.search(r'\s\d+\.md$', f)]
            
            for file in md_files:
                file_path = os.path.join(root, file)
                current_name = file[:-3]
                total_scanned += 1
                
                # 进度提示：每 500 个文件打印一次，避免日志刷屏
                if total_scanned % 500 == 0:
                    elapsed = time.time() - start_time
                    print(f"⏳ 已扫描 {total_scanned}/{entity_size}... (用时: {elapsed:.1f}s)")

                with open(file_path, "r", encoding="utf-8") as f:
                    content = f.read()

                # 逻辑隔离逻辑
                parts = re.split(r'(^---$.*?^---$)', content, flags=re.DOTALL | re.MULTILINE)
                new_parts = []
                for part in parts:
                    if part.startswith("---"):
                        new_parts.append(part)
                        continue
                    
                    lines = part.splitlines(keepends=True)
                    new_lines = []
                    for line in lines:
                        if "Source:" in line:
                            new_lines.append(line)
                            continue
                        
                        for entity, pattern in self.pattern_cache.items():
                            if entity.lower() == current_name.lower():
                                continue
                            line = pattern.sub(rf"[[\1]]", line)
                        new_lines.append(line)
                    new_parts.append("".join(new_lines))
                
                final_content = "".join(new_parts)

                if final_content != content:
                    with open(file_path, "w", encoding="utf-8") as f:
                        f.write(final_content)
                    updated_count += 1

        end_time = time.time()
        print(f"\n🏁 任务圆满完成！")
        print(f"📊 总扫描: {total_scanned} | 实际更新: {updated_count}")
        print(f"⏱️  总耗时: {end_time - start_time:.2 short} 秒")

if __name__ == "__main__":
    # 配置
    ATOMS_DIR = "./rbrain-wiki/atoms" 
    #WHITELIST = ["禅", "道", "C", "Go", "爱"]
    WHITELIST = []

    linker = Backlinker(atoms_dir=ATOMS_DIR, whitelist=WHITELIST)
    linker.apply_backlinks()