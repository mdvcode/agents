CODEX_CLI ?= $(shell if [ -x /Applications/ChatGPT.app/Contents/Resources/codex ]; then printf /Applications/ChatGPT.app/Contents/Resources/codex; elif [ -x "$$HOME/Applications/ChatGPT.app/Contents/Resources/codex" ]; then printf "$$HOME/Applications/ChatGPT.app/Contents/Resources/codex"; else command -v codex 2>/dev/null || printf codex; fi)

.PHONY: check security validate-artifacts runtime-preflight codex-preflight codex-smoke step1-verify step2-verify queue-worker worker-service-start worker-service-restart worker-service-status worker-service-health worker-service-stop control-plane metrics approve-run resume-run reject-run list-exceptions publish-dry-run publish agent-status

RUN_ID ?=
STEP1_MANIFEST ?=
QUEUE_DB ?= .agent-queue/tasks.db
ACTOR ?=
REASON ?=

check: validate-artifacts security
	PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest tests
	@if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then \
		git diff HEAD --check; \
	else \
		echo "skip git diff HEAD --check: not a git repository"; \
	fi

security:
	python3 scripts/security_scan.py

validate-artifacts:
	@if [ -n "$(RUN_ID)" ]; then \
		python3 scripts/validate_artifacts.py --run-dir ".agent-runs/$(RUN_ID)"; \
	else \
		python3 scripts/validate_artifacts.py --contracts-only; \
	fi

runtime-preflight:
	AGENT_CODEX_CLI_COMMAND="$(CODEX_CLI)" python3 scripts/check_runtime.py --repo .

codex-preflight: runtime-preflight

codex-smoke:
	AGENT_REAL_CODEX_SMOKE=1 AGENT_CODEX_CLI_COMMAND="$(CODEX_CLI)" \
	PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest tests/test_real_codex_smoke.py -q

step1-verify:
	@test -n "$(RUN_ID)" || (echo "RUN_ID is required for the evidence artifact owner" >&2; exit 2)
	@test -n "$(STEP1_MANIFEST)" || (echo "STEP1_MANIFEST is required" >&2; exit 2)
	python3 scripts/verify_step1_series.py --runs-dir .agent-runs --manifest "$(STEP1_MANIFEST)" \
		--output ".agent-runs/$(RUN_ID)/artifacts/step1_evidence.json"

step2-verify:
	@test -n "$(RUN_ID)" || (echo "RUN_ID is required for the evidence artifact owner" >&2; exit 2)
	python3 scripts/verify_step2.py --runs-dir .agent-runs --db "$(QUEUE_DB)" \
		--output ".agent-runs/$(RUN_ID)/artifacts/step2_evidence.json"

queue-worker:
	python3 scripts/worker_pool.py --db "$(QUEUE_DB)" --workers 3

worker-service-start:
	python3 scripts/worker_service.py start --db "$(QUEUE_DB)" --workers 3

worker-service-restart:
	python3 scripts/worker_service.py restart --db "$(QUEUE_DB)" --workers 3

worker-service-status:
	python3 scripts/worker_service.py status

worker-service-health:
	python3 scripts/worker_service.py health

worker-service-stop:
	python3 scripts/worker_service.py stop

control-plane:
	python3 scripts/control_plane_api.py --db "$(QUEUE_DB)"

metrics:
	python3 scripts/operational_metrics.py --db "$(QUEUE_DB)"

approve-run:
	@test -n "$(RUN_ID)" || (echo "RUN_ID is required" >&2; exit 2)
	@test -n "$(ACTOR)" || (echo "ACTOR is required" >&2; exit 2)
	python3 scripts/approval_lifecycle.py approve "$(RUN_ID)" --actor "$(ACTOR)" --reason "$(REASON)"

resume-run:
	@test -n "$(RUN_ID)" || (echo "RUN_ID is required" >&2; exit 2)
	python3 scripts/approval_lifecycle.py resume "$(RUN_ID)" --db "$(QUEUE_DB)"

reject-run:
	@test -n "$(RUN_ID)" || (echo "RUN_ID is required" >&2; exit 2)
	@test -n "$(ACTOR)" || (echo "ACTOR is required" >&2; exit 2)
	@test -n "$(REASON)" || (echo "REASON is required" >&2; exit 2)
	python3 scripts/approval_lifecycle.py reject "$(RUN_ID)" --actor "$(ACTOR)" --reason "$(REASON)"

list-exceptions:
	python3 scripts/list_runs.py --db "$(QUEUE_DB)" --requires-human

publish-dry-run:
	@test -n "$(RUN_ID)" || (echo "RUN_ID is required" >&2; exit 2)
	python3 scripts/publish_pr.py --run-id "$(RUN_ID)" --dry-run

publish:
	@test -n "$(RUN_ID)" || (echo "RUN_ID is required" >&2; exit 2)
	python3 scripts/publish_pr.py --run-id "$(RUN_ID)"

agent-status:
	@test -n "$(RUN_ID)" || (echo "RUN_ID is required" >&2; exit 2)
	@python3 -m json.tool ".agent-runs/$(RUN_ID)/workflow.json"
