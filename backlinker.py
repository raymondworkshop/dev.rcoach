import os
import re
import string

class Backlinker:
    def __init__(self, wiki_dir):
        self.wiki_dir = wiki_dir
        self.garbage = string.punctuation + string.whitespace
        # 核心：动态获取实体索引
        self.entities = self._get_current_entities()

    def _get_current_entities(self):
        """从文件夹名中提取现有词库，并按长度降序排列"""
        if not os.path.exists(self.wiki_dir):
            return []
        
        # 获取文件名（不含 .md 后缀）
        names = [f[:-3] for f in os.listdir(self.wiki_dir) if f.endswith('.md')]
        
        # 关键逻辑：长词优先。
        # 理由：防止 "失败恐惧" 被拆解成 "[[失败]]恐惧" 或 "失败[[恐惧]]"
        names.sort(key=len, reverse=True)
        return names

    def apply_backlinks(self):
        """对原子笔记文件夹执行全量扫描与互联"""
        if not self.entities:
            print("⚠️ 词库为空，请先运行 Atomizer 生成笔记。")
            return

        print(f"🕵️ 启动 Backlinker... 目标实体数: {len(self.entities)}")
        
        updated_count = 0
        for root, _, files in os.walk(self.wiki_dir):
            for file in files:
                if not file.endswith(".md"): continue
                
                file_path = os.path.join(root, file)
                current_filename = file[:-3] # 当前笔记名
                
                with open(file_path, "r", encoding="utf-8") as f:
                    content = f.read()

                original_content = content
                
                # 遍历词库执行正则替换
                for entity in self.entities:
                    # 规则 1：跳过单字词（极易误伤）
                    if len(entity) <= 1: continue
                    # 规则 2：跳过自身链接（防止笔记里到处是自己的双链）
                    if entity == current_filename: continue
                    
                    # 规则 3：正则负向断言 (Negative Lookbehind/Lookahead)
                    # (?<!\[\[) : 前面不能是 [[
                    # (?!\]\])   : 后面不能是 ]]
                    # 作用：确保不会把已经打好链接的词再次包装成 [[[[双链]]]]
                    pattern = rf"(?<!\[\[)({re.escape(entity)})(?!\]\])"
                    
                    # 执行替换
                    content = re.sub(pattern, rf"[[\1]]", content)

                if content != original_content:
                    with open(file_path, "w", encoding="utf-8") as f:
                        f.write(content)
                    print(f"✅ Linked: {file}")
                    updated_count += 1

        print(f"🏁 任务完成！共计更新了 {updated_count} 个文件的隐形链接。")

# --- 集成运行示例 ---
if __name__ == "__main__":
    # 指向你的原子笔记目录
    ATOMS_PATH = "./rbrain-wiki/atoms" 
    
    linker = Backlinker(ATOMS_PATH)
    linker.apply_backlinks()