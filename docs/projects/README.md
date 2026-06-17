# Projects

This directory stores private memory for multiple target projects.

`/Users/user/agents` is the private control plane. Target project repositories receive only reviewed code, tests, and safe public documentation according to each project's publication rules in `.agent-policy.yaml`.

## Layout
Each project should use:

```text
docs/projects/<project>/
  README.md
  privacy.md
  issues/
  memory/
    MEMORY.md
    SCRATCHPAD.md
    topics/
  wiki/
    index.md
    concepts/
    entities/
    decisions/
    contradictions.md
  graph/
    README.md
    files.md
    workflows.md
    risks.md
```

## Project Intake
1. Create `docs/projects/<project>/` from `docs/projects/_template/`.
2. Fill `privacy.md` before working on issues.
3. Keep issue journals under `docs/projects/<project>/issues/`.
4. Promote reusable project knowledge to project `memory/` or `wiki/`.
5. Keep global agent-system knowledge in top-level `docs/wiki/` and `docs/memory/`.

## Privacy
- Project memory is private by default.
- Do not copy private issue history into target project repositories.
- PR descriptions should use sanitized summaries only.
- If a project has stricter rules, record them in `privacy.md`.
