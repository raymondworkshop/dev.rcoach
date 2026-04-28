

#### notes 
- rbrain is a second brain with LLM wiki 
    - Wiki is long-term memory from Agent  
        > ingest  
        > query  
        > link  

- LLM is the programmer to maintain wiki based on raw notes    
    - index the themes and main points based on gemini-flash-lite 
        - [[self]] | Awareness, stoic integration, vulnerability, self-acceptance
        - [[work]] | Strategic execution, minimalism, and professional development
        - [[relationships]] | Emotional maturity, authentic connection, and boundary management
        - [[actions]] | Sustainable habits, operational frameworks, and system efficiency
        - [[growth-trajectory]] | Synthesis of long-term life patterns, personal evolution, and transformation insights

    - atomization 词条化  -
        - 针对你的目标（自我、关系、工作），建议将词条分为以下几类：
            + [[人/人物名]]: 记录你们的互动模式、对方的喜好、协作的雷区。 
            + [[心/心理机制]]: 如 [[回避型人格]] 或 [[深夜焦虑]]。每当你日记里出现类似情绪，就链接到这里。 
            + [[法/方法论]]: 如 [[快速决策法]] 或 [[Qwen调优技巧]]。 
            + [[事/项目]]: 具体的工作任务，记录起承转合  
        - llm 执行 'wiki 重构任务”  

        > atomizer.py  

    - cross-linking 发现 ‘隐形链接“  
        - 运行一个“关联审计”脚本， 看到系统性问题  
        - prompt: "读取词条 [[情绪-焦虑]] 和 [[工作-周报汇报]]。分析这两者在我的历史记录中是否存在时间上的相关性？我是因为汇报而焦虑，还是因为焦虑导致汇报表现不佳？"   
        - TODO

    - weekly synthesis  
        - 让 LLM 定期“修剪”和“整合” 词条    
        - 合并与升级  
        - 结果： 你的 Wiki 目录会从几百个琐碎的词条，演变成几十个深度的方法论
        - TODO

    - socratic questioning  
        - 开启 “反向教练” 模式， 与笔记对话   
        - prompt： “基于 [[自我成长]] 主题下的所有词条，扮演一个严厉的教练。找出我思维中的 3 个漏洞。比如：我是否总是在遇到某种特定挫折时选择逃避？”  
        - 目的： 强迫你直面那些被你分散记录在不同日期里的失败模式 
        - TODO  

    - actionable insights  行动建议  
        - 案例：人际关系： 当你要和 [[人物-张三]] 开会前，让 Qwen 快速扫描该词条：“总结与张三沟通的 3 个禁忌点。” 
               工作： 当你要开始新项目时：“根据 [[过往项目-教训库]]，列出我最容易在启动阶段犯的 2 个错误。”
        - TODO  

    - involve when new inputs based on these  
        - entiry & pattern extraction, and emotion recognition in each file  
            + EXTRACTION_RULES = """
             在提取内容时，请务必在每条笔记末尾添加以下格式的 Tags：

             1. 格式要求：使用 #TagName 并在其后紧跟简短描述。
             2. 强制标签池：
             - 如果涉及具体的人或物，必带 #Entity
             - 如果是行为规律总结，必带 #Pattern
             - 如果发现今天的言行与之前记录的价值观（Wiki 内容）不符，必带 #Contradiction
             - 如果这是一个需要后续执行的动作，必带 #Todo

            示例输出：
             - 洞察：发现自己在老板批评时会下意识寻找外部借口。
             - 标签：#Pattern #Contradiction #Review
            """
        
        - maintain wiki based on the above results   
            - give the reference 

        - 交叉引用 (Bookkeeping): 在每一篇原始笔记的末尾，让 LLM 自动添加一行：
            - Related to: [[Wiki/Mental_Models#Overthinking]]  

        - log.md

#### run  
- 用 gemini-late 继续挖
    + 也慢慢构造本地llm-wiki based on pi agent

- pi agent  
    - model: qwen2.5:7b-instruct-q5_K_M on local mini
        - api: http://100.90.225.26:11434 
        > ollama launch pi  
    - local run
        > export OLLAMA_HOST="http://100.90.225.26:11434"  
        > ollama run rbrain-gemma3  

    
- SKILL.md defines the personality and rules


####  Directory Structure  
- `/raw`: All immutable source material 
    - `/queries`: save Q&A
- `/outputs`:  save the outputs based on wiki (like report, PPT)
- `/wiki/` 
    - `topics/` 
    - `atoms/`
    - index.md  
    - log.md  

#### run

* using local ollama/ to compiles raw into a structured wiki
    - drop any .md files into ~/rbrain-wiki/raw/
         
    - query "question" --save 
         
*  hermes using Gemini Flash  
    - questions
 
    - ingest new content
    
    - maintain
      

* review
    - open ~/rbrain-wiki as an Obsidian vault.
      The graph view shows your connected wiki  

#### reference
* [obsidian-llm-wiki](https://github.com/kytmanov/obsidian-llm-wiki-local)
