CODEX_CLI ?= $(shell if [ -x /Applications/ChatGPT.app/Contents/Resources/codex ]; then printf /Applications/ChatGPT.app/Contents/Resources/codex; elif [ -x "$$HOME/Applications/ChatGPT.app/Contents/Resources/codex" ]; then printf "$$HOME/Applications/ChatGPT.app/Contents/Resources/codex"; else command -v codex 2>/dev/null || printf codex; fi)
PYTHON ?= $(shell if [ -x .venv/bin/python ]; then printf .venv/bin/python; else command -v python3; fi)

.PHONY: check security validate-artifacts runtime-preflight codex-preflight codex-smoke runtime-chaos runtime-soak runtime-soak-verify step1-verify step2-verify eval-score eval-run eval-compare eval-leaderboard eval-regression adaptive-eval-plans adaptive-eval-ab adaptive-eval-leaderboard adaptive-eval-gate queue-worker worker-service-start worker-service-restart worker-service-status worker-service-health worker-service-stop control-plane dashboard metrics approve-run resume-run reject-run list-exceptions publish-dry-run publish agent-status

RUN_ID ?=
STEP1_MANIFEST ?=
QUEUE_DB ?= .agent-queue/tasks.db
ACTOR ?=
REASON ?=
EVAL_RUN_DIR ?=
EVAL_DATASET ?= evals/datasets/harness_completed_run_v1.json
EVAL_RUBRIC ?= evals/rubrics/harness_run_v1.json
EVAL_SUBJECT ?= candidate
EVAL_BASELINE ?=
EVAL_CANDIDATE ?=
EVAL_REPORTS ?=
EVAL_OUTPUT ?=
EVAL_EXPERIMENT ?= evals/experiments/production_e2_v1.json
EVAL_CANDIDATE_REPORT ?=
EVAL_GATE_OUTPUT ?=
ADAPTIVE_MANIFEST ?=
ADAPTIVE_REPORT ?= .agent-runs/adaptive-evaluation-report.json
SOAK_MANIFEST ?=
SOAK_REPORT ?=

check: validate-artifacts security
	PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 $(PYTHON) -m pytest tests
	@if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then \
		git diff HEAD --check; \
	else \
		echo "skip git diff HEAD --check: not a git repository"; \
	fi

security:
	$(PYTHON) scripts/security_scan.py

validate-artifacts:
	@if [ -n "$(RUN_ID)" ]; then \
		$(PYTHON) scripts/validate_artifacts.py --run-dir ".agent-runs/$(RUN_ID)"; \
	else \
		$(PYTHON) scripts/validate_artifacts.py --contracts-only; \
	fi

runtime-preflight:
	$(PYTHON) scripts/check_runtime.py --repo .

codex-preflight: runtime-preflight

codex-smoke:
	AGENT_REAL_CODEX_SMOKE=1 \
	PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 $(PYTHON) -m pytest tests/test_real_codex_smoke.py -q

runtime-chaos:
	PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 $(PYTHON) -m pytest -q tests/recovery tests/test_worker_pool.py tests/test_worker_service.py tests/test_task_queue.py tests/test_publish_pr.py tests/test_approval_lifecycle.py

runtime-soak:
	@test -n "$(SOAK_MANIFEST)" || (echo "SOAK_MANIFEST is required" >&2; exit 2)
	@test -n "$(SOAK_REPORT)" || (echo "SOAK_REPORT is required" >&2; exit 2)
	$(PYTHON) scripts/run_runtime_soak.py "$(SOAK_MANIFEST)" --db "$(QUEUE_DB)" --output "$(SOAK_REPORT)"

runtime-soak-verify:
	@test -n "$(SOAK_REPORT)" || (echo "SOAK_REPORT is required" >&2; exit 2)
	$(PYTHON) scripts/verify_runtime_soak.py "$(SOAK_REPORT)"

step1-verify:
	@test -n "$(RUN_ID)" || (echo "RUN_ID is required for the evidence artifact owner" >&2; exit 2)
	@test -n "$(STEP1_MANIFEST)" || (echo "STEP1_MANIFEST is required" >&2; exit 2)
	$(PYTHON) scripts/verify_step1_series.py --runs-dir .agent-runs --manifest "$(STEP1_MANIFEST)" \
		--output ".agent-runs/$(RUN_ID)/artifacts/step1_evidence.json"

step2-verify:
	@test -n "$(RUN_ID)" || (echo "RUN_ID is required for the evidence artifact owner" >&2; exit 2)
	$(PYTHON) scripts/verify_step2.py --runs-dir .agent-runs --db "$(QUEUE_DB)" \
		--output ".agent-runs/$(RUN_ID)/artifacts/step2_evidence.json"

eval-score:
	@test -n "$(EVAL_RUN_DIR)" || (echo "EVAL_RUN_DIR is required" >&2; exit 2)
	@test -n "$(EVAL_OUTPUT)" || (echo "EVAL_OUTPUT is required" >&2; exit 2)
	$(PYTHON) scripts/score.py --run-dir "$(EVAL_RUN_DIR)" --rubric "$(EVAL_RUBRIC)" --output "$(EVAL_OUTPUT)"

eval-run:
	@test -n "$(EVAL_RUN_DIR)" || (echo "EVAL_RUN_DIR is required" >&2; exit 2)
	@test -n "$(EVAL_OUTPUT)" || (echo "EVAL_OUTPUT is required" >&2; exit 2)
	$(PYTHON) scripts/run_evals.py --dataset "$(EVAL_DATASET)" --rubric "$(EVAL_RUBRIC)" \
		--subject "$(EVAL_SUBJECT)=$(EVAL_RUN_DIR)" --output "$(EVAL_OUTPUT)"

eval-compare:
	@test -n "$(EVAL_BASELINE)" || (echo "EVAL_BASELINE is required" >&2; exit 2)
	@test -n "$(EVAL_CANDIDATE)" || (echo "EVAL_CANDIDATE is required" >&2; exit 2)
	@test -n "$(EVAL_OUTPUT)" || (echo "EVAL_OUTPUT is required" >&2; exit 2)
	$(PYTHON) scripts/compare_runs.py --baseline "$(EVAL_BASELINE)" --candidate "$(EVAL_CANDIDATE)" --output "$(EVAL_OUTPUT)"

eval-leaderboard:
	@test -n "$(EVAL_REPORTS)" || (echo "EVAL_REPORTS is required" >&2; exit 2)
	@test -n "$(EVAL_OUTPUT)" || (echo "EVAL_OUTPUT is required" >&2; exit 2)
	$(PYTHON) scripts/leaderboard.py $(EVAL_REPORTS) --output "$(EVAL_OUTPUT)"

eval-regression:
	$(PYTHON) scripts/eval_regression.py --manifest "$(EVAL_EXPERIMENT)" \
		$(if $(EVAL_CANDIDATE_REPORT),--candidate "$(EVAL_CANDIDATE_REPORT)",) \
		$(if $(EVAL_GATE_OUTPUT),--output "$(EVAL_GATE_OUTPUT)",)

adaptive-eval-plans:
	$(PYTHON) scripts/adaptive_evaluation.py plans --report "$(ADAPTIVE_REPORT)"

adaptive-eval-ab:
	@test -n "$(ADAPTIVE_MANIFEST)" || (echo "ADAPTIVE_MANIFEST is required" >&2; exit 2)
	$(PYTHON) scripts/adaptive_evaluation.py ab --manifest "$(ADAPTIVE_MANIFEST)" --report "$(ADAPTIVE_REPORT)"

adaptive-eval-leaderboard:
	@test -n "$(EVAL_REPORTS)" || (echo "EVAL_REPORTS is required" >&2; exit 2)
	$(PYTHON) scripts/adaptive_evaluation.py leaderboard --reports $(EVAL_REPORTS) --report "$(ADAPTIVE_REPORT)"

adaptive-eval-gate:
	$(PYTHON) scripts/adaptive_evaluation.py gate --report "$(ADAPTIVE_REPORT)"

queue-worker:
	$(PYTHON) scripts/worker_pool.py --db "$(QUEUE_DB)" --workers 3

worker-service-start:
	$(PYTHON) scripts/worker_service.py start --db "$(QUEUE_DB)" --workers 3

worker-service-restart:
	$(PYTHON) scripts/worker_service.py restart --db "$(QUEUE_DB)" --workers 3

worker-service-status:
	$(PYTHON) scripts/worker_service.py status

worker-service-health:
	$(PYTHON) scripts/worker_service.py health

worker-service-stop:
	$(PYTHON) scripts/worker_service.py stop

control-plane:
	$(PYTHON) scripts/control_plane_api.py --db "$(QUEUE_DB)"

dashboard: control-plane

metrics:
	$(PYTHON) scripts/operational_metrics.py --db "$(QUEUE_DB)"

approve-run:
	@test -n "$(RUN_ID)" || (echo "RUN_ID is required" >&2; exit 2)
	@test -n "$(ACTOR)" || (echo "ACTOR is required" >&2; exit 2)
	$(PYTHON) scripts/approval_lifecycle.py approve "$(RUN_ID)" --actor "$(ACTOR)" --reason "$(REASON)"

resume-run:
	@test -n "$(RUN_ID)" || (echo "RUN_ID is required" >&2; exit 2)
	$(PYTHON) scripts/approval_lifecycle.py resume "$(RUN_ID)" --db "$(QUEUE_DB)"

reject-run:
	@test -n "$(RUN_ID)" || (echo "RUN_ID is required" >&2; exit 2)
	@test -n "$(ACTOR)" || (echo "ACTOR is required" >&2; exit 2)
	@test -n "$(REASON)" || (echo "REASON is required" >&2; exit 2)
	$(PYTHON) scripts/approval_lifecycle.py reject "$(RUN_ID)" --actor "$(ACTOR)" --reason "$(REASON)"

list-exceptions:
	$(PYTHON) scripts/list_runs.py --db "$(QUEUE_DB)" --requires-human

publish-dry-run:
	@test -n "$(RUN_ID)" || (echo "RUN_ID is required" >&2; exit 2)
	$(PYTHON) scripts/publish_pr.py --run-id "$(RUN_ID)" --dry-run

publish:
	@test -n "$(RUN_ID)" || (echo "RUN_ID is required" >&2; exit 2)
	$(PYTHON) scripts/publish_pr.py --run-id "$(RUN_ID)"

agent-status:
	@test -n "$(RUN_ID)" || (echo "RUN_ID is required" >&2; exit 2)
	@$(PYTHON) -m json.tool ".agent-runs/$(RUN_ID)/workflow.json"
