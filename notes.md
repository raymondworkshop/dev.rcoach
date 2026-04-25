
#### notes 

* Using olw calling local ollama/gemma4:2b to compiles raw into a structured wiki
    - Drop any .md files into ~/my-wiki/raw/

    -  One command: ingest + compile + lint + optional auto-approve
        - > olw run --vault my-wiki or export OLW_VAULT=my-wiki

        - Extracts concept names, Creates wiki/sources/Note.md (source summary page)
            + >  olw ingest --all
        - For each concept, Writes a wiki article with [[wikilinks]] to related concepts
            + > olw compile
        - Updates wiki/index.md (navigation layer)
            + > olw review        # interactive draft review
         
    - olw query "question" --save 
         
*  hermes
    - questions
        + > hermes /llm-wiki query "questions"
 
    - ingest new content
        + > hermes /llm-wiki ingest ./path/to/your/note.md
 
    - maintain
        + > hermes /llm-wiki lint or hermes /llm-wiki compile
         

* review
    - Open ~/my-wiki as an Obsidian vault. The graph view shows your connected wiki

#### reference
* [obsidian-llm-wiki](https://github.com/kytmanov/obsidian-llm-wiki-local)
