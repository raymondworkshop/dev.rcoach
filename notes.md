

#### notes 
- rbrain is a second brain with LLM wiki
    > ingest  
    > query  
    > link  

- pi agent  
    - Mini local model: gemma3:4b-it-q8_0 
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
