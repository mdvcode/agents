# Context Compiler

Context Engine deterministically collects and compiles the minimal repository, Obsidian,
documentation, policy, profile, skill, contract, and artifact context needed for the next role.
Agents receive only its bounded Context Package and never read knowledge roots directly.

Output JSON with:
- `context_files`
- `context_package_path`
- `context_log_path`
- `project_profile`
- `known_constraints`
- `open_questions`
- `next_action`
