.PHONY: check security validate-artifacts agent-status

check: validate-artifacts
	python3 -m json.tool artifacts/risk.json >/dev/null
	python3 -m json.tool artifacts/quality.json >/dev/null
	python3 -m json.tool artifacts/verdict.json >/dev/null
	python3 -m json.tool artifacts/project_profile.json >/dev/null
	git diff --check

security:
	@echo "security: documentation-only agent workspace; no code security scanner configured"
	@echo "security: required protected-path and artifact checks are enforced by AGENTS.md and make check"

validate-artifacts:
	python3 scripts/validate_artifacts.py

agent-status:
	@echo "== git status =="
	@git status --short
	@echo
	@echo "== verdict =="
	@python3 -m json.tool artifacts/verdict.json
