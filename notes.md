
#### notes 
- CLAUDE.md defines the personality and rules


####  Directory Structure  
- `/raw`: All unprocessed inputs (voice transcripts, messy diary entries, meeting notes)
    - `/queries`: save Q&A
- `/outputs`:  save the outputs based on wiki (like report, PPT)
- `/wiki/self/`: 
    - `/wiki/self/patterns.md`: Analysis of recurring behaviors/thoughts
    - `/wiki/self/values.md`: Core values and "rules for life."
    - `/wiki/self/biography.md`: Personal Timeline
    - `/wiki/self/health.md`: Physical and mental well-being logs
- `/wiki/relations/`:
    - `/wiki/relations/contacts/`: Individual markdown files for key people (interaction logs, preferences)
    - `/wiki/relations/network.md`: High-level view of social circles and relationship health
- `/wiki/business/`:
    - `/wiki/business/projects/`: Specific business or work task folders
    - `/wiki/business/insights.md`: Industry insights and long-term career goals
- `/wiki/queries/`: save Q&A
- `/wiki/index.md`: Automated MOCs (Maps of Content)

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
