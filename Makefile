.PHONY: check security validate-artifacts agent-status

check: validate-artifacts security
	python3 -m json.tool artifacts/risk.json >/dev/null
	python3 -m json.tool artifacts/quality.json >/dev/null
	python3 -m json.tool artifacts/verdict.json >/dev/null
	python3 -m json.tool artifacts/project_profile.json >/dev/null
	python3 -m json.tool artifacts/change_set.json >/dev/null
	python3 -m json.tool artifacts/publication.json >/dev/null
	python3 -m pytest tests
	@if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then \
		git diff HEAD --check; \
	else \
		echo "skip git diff HEAD --check: not a git repository"; \
	fi

security:
	python3 scripts/security_scan.py

validate-artifacts:
	python3 scripts/validate_artifacts.py

agent-status:
	@echo "== git status =="
	@git status --short
	@echo
	@echo "== verdict =="
	@python3 -m json.tool artifacts/verdict.json
