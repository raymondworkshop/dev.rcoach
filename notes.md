

#### notes 
- gbrain is a second brain with LLM wiki
    > ingest  
    > query  
    > link  
    
- SKILL.md defines the personality and rules


####  Directory Structure  
- `/raw`: All immutable source material 
    - `/queries`: save Q&A
- `/outputs`:  save the outputs based on wiki (like report, PPT)
- `/wiki/` 
    - `topics/`  
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
