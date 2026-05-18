# Quality Runner Agent

Run repository quality checks and write the result to `artifacts/quality.json`.

## Responsibilities
Run:
- `ruff check .`
- `ruff format --check .`
- `mypy .`
- `pytest --tb=short --maxfail=1`
- coverage evaluation if configured

## Required JSON shape
```json
{
  "ruff": "pass|fail",
  "format": "pass|fail",
  "mypy": "pass|fail",
  "pytest": "pass|fail",
  "coverage": {
    "status": "pass|fail|unknown",
    "percent": null,
    "threshold": 80
  },
  "failures": [],
  "files_checked": []
}
```

## Rules
- Preserve `PYTHONPATH=contactapi:contactapi/apps` for repository commands.
- Exclude local virtualenvs, media, artifacts, and migrations from linting noise where configured.
- Record blockers rather than guessing if tooling is unavailable.
