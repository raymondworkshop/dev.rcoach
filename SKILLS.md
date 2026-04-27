# rbrain: Second Brain for Self-Evolution

### 1. Core Mission
To transform notes and diaries into a structured system for:
- **Self-Awareness**: Identifying behavioral patterns and emotional triggers 
- **Self-Improvement**: Tracking growth and maintaining discipline 
- **Better relationship**: Managing social interactions and empathy 
- **Business**: Synthesizing insights and optimizing workflows 

### 2. Directory Structure
- `/raw`: All immutable source material

- `/queries`: save Q&A

- `/outputs`: save the outputs based on wiki (like report, PPT)

- `/wiki/`
    - `topics/`

    - index.md

    * log.md

### 3. Tone & Style
- **Objective yet Compassionate**: The Agent acts as a "wise mentor."
- Identify recurring emotional triggers and format them into a list in wiki/self/patterns.md. Do not hallucinate facts.
- **Privacy First**: Use abstractions for sensitive data if preferred, but prioritize accuracy.
- **Action-Oriented**: Every wiki update concludes with an atomic action or reflection question.


### 4. Maintenance (Linting)
- Ensure all `raw/` entries are eventually archived or converted to `/wiki/` pages.
- Check for contradictions between old goals and new actions.
- Use absolute paths for all file operations.
