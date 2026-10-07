from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from ai_harness.project import default_config, register_local_project, write_project_config
from ai_harness.result_acceptance import record_result_acceptance, result_acceptance, result_observation, compare_results
from run_direct_baseline import BaselineError, collect_baseline
from runtimes.base import RuntimeDescriptor


def git(repo: Path, *args: str) -> str:
    return subprocess.run(['git', *args], cwd=repo, capture_output=True, check=True).stdout.decode().strip()


def write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


@pytest.fixture
def disposable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, str]:
    monkeypatch.setenv('AI_HARNESS_CONFIG_HOME', str(tmp_path / 'trust'))
    repo = tmp_path / 'fixture'
    repo.mkdir()
    git(repo, 'init', '-b', 'main')
    git(repo, 'config', 'user.email', 'test@example.test')
    git(repo, 'config', 'user.name', 'Test')
    (repo / 'value.txt').write_text('old\n')
    (repo / 'Makefile').write_text('check:\n\t@test "$$(cat value.txt)" = new\nsecurity:\n\t@true\n')
    config = default_config(repo, profile='agent_workspace', base_branch='main')
    write_project_config(config)
    register_local_project(config)
    git(repo, 'add', '.')
    git(repo, 'commit', '-m', 'Fixture')
    base = git(repo, 'rev-parse', 'HEAD')
    git(repo, 'switch', '-c', 'baseline/once')
    return repo, base


class FixtureRuntime:
    descriptor = RuntimeDescriptor('codex-sdk', 'runtime_adapter', 'local_subscription', True, 'fixture', False)

    def __init__(self, *, edit: str = 'new\n', status: str = 'completed') -> None:
        self.calls = 0
        self.edit = edit
        self.status = status

    def preflight(self, **_: object) -> dict:
        return {'execution_status': 'completed'}

    def execute(self, *, role: str, context: Path, task: dict, worktree: Path, artifacts: Path) -> dict:
        self.calls += 1
        assert task['role'] == role == 'implementation-agent'
        assert context.is_file()
        (worktree / 'value.txt').write_text(self.edit)
        write(artifacts / 'implementation.json', {'changed_files': ['value.txt'], 'summary': 'Updated fixture'})
        result = {'status': self.status, 'next_action': 'continue', 'summary': 'Updated fixture', 'artifacts_created': ['implementation.json'], 'blockers': [], 'warnings': [], 'tokens_used': 17}
        write(artifacts.parent / 'raw-events' / 'implementation-agent.json', {'provider': 'codex-sdk', 'role': role, 'returncode': 0, 'stdout': json.dumps(result), 'stderr': ''})
        write(artifacts.parent / 'raw-events' / 'implementation-agent.jsonl', {'provider': 'codex-sdk', 'status': 'completed', 'thread_id': 'fixture-thread', 'turn_id': 'fixture-turn'})
        (artifacts.parent / 'raw-events' / 'sdk-events.jsonl').write_text(json.dumps({'method': 'turn/completed', 'params': {'turn': {'status': 'completed'}}}) + '\n')
        return result


def collect(tmp_path: Path, disposable: tuple[Path, str], runtime: FixtureRuntime, **kwargs: object) -> dict:
    repo, base = disposable
    return collect_baseline(repository=repo, base_sha=base, goal='Update the value text', allowed_paths=['value.txt'], run_id='baseline-one', confirmed_disposable=True, runs_dir=tmp_path / 'runs', runtime=runtime, **kwargs)


def test_collector_executes_once_and_records_real_deterministic_checks(tmp_path: Path, disposable: tuple[Path, str]) -> None:
    runtime = FixtureRuntime()
    result = collect(tmp_path, disposable, runtime)
    assert result['execution_status'] == 'completed' and runtime.calls == 1
    assert git(disposable[0], 'rev-parse', 'HEAD') == disposable[1]
    run = tmp_path / 'runs' / 'baseline-one'
    assert not (tmp_path / '.agent-queue').exists()
    assert result_acceptance(run)['current'] is None
    quality = json.loads((run / 'artifacts' / 'quality.json').read_text())
    assert quality['repository_checks_passed'] is True
    assert quality['commands_attempted'] == ['make check']
    assert json.loads((run / 'artifacts' / 'review.json').read_text())['verification_kind'] == 'deterministic_baseline'
    from validate_artifacts import validate_required
    verdict = json.loads((run / 'artifacts' / 'verdict.json').read_text())
    assert validate_required(verdict, json.loads((ROOT / 'schemas' / 'verdict.schema.json').read_text()), 'verdict') == []
    assert verdict['approval_required_before_publish'] is True


@pytest.mark.parametrize('failure', ['model', 'checks'])
def test_failed_baseline_never_repairs_or_issues_comparable_receipt(tmp_path: Path, disposable: tuple[Path, str], failure: str) -> None:
    runtime = FixtureRuntime(status='blocked') if failure == 'model' else FixtureRuntime(edit='wrong\n')
    with pytest.raises(BaselineError):
        collect(tmp_path, disposable, runtime)
    run = tmp_path / 'runs' / 'baseline-one'
    assert runtime.calls == 1
    assert not (run / 'direct-baseline.json').exists()
    assert result_acceptance(run)['eligible'] is False


@pytest.mark.parametrize('change', ['dirty', 'remote', 'branch', 'untrusted'])
def test_target_validation_precedes_runtime_execution(tmp_path: Path, disposable: tuple[Path, str], change: str, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, _ = disposable
    if change == 'dirty':
        (repo / 'value.txt').write_text('dirty')
    elif change == 'remote':
        git(repo, 'remote', 'add', 'origin', 'https://example.test/repo.git')
    elif change == 'branch':
        git(repo, 'switch', 'main')
    else:
        monkeypatch.setenv('AI_HARNESS_CONFIG_HOME', str(tmp_path / 'untrusted'))
    runtime = FixtureRuntime()
    with pytest.raises(BaselineError):
        collect(tmp_path, disposable, runtime)
    assert runtime.calls == 0 and not (tmp_path / 'runs').exists()


def test_only_bound_runtime_receipt_enters_sdk_comparison_and_acceptance_is_human(tmp_path: Path, disposable: tuple[Path, str]) -> None:
    collect(tmp_path, disposable, FixtureRuntime())
    run = tmp_path / "runs" / "baseline-one"
    workflow = json.loads((run / "workflow.json").read_text())
    metrics = json.loads((run / "metrics.json").read_text())
    row = result_observation(run, workflow, metrics)
    cohort = compare_results([row])["direct_codex"]
    assert cohort["runs"] == 1 and cohort["rated"] == 0 and cohort["acceptance_rate"] is None
    assert cohort["measurements"]["tokens_used"]["mean"] is None
    record_result_acceptance(run, status="accepted", reason="", actor="human", expected_fingerprint=row["acceptance"]["result_fingerprint"])
    row = result_observation(run, workflow, metrics)
    assert compare_results([row])["direct_codex"]["accepted"] == 1
    fast = {**row, "run_id": "matched-fast", "mode": "fast"}
    comparison = compare_results([row, fast])
    assert comparison["paired_with_sdk"]["fast"]["rated_pairs"] == 1
    assert comparison["excluded_unknown_or_other_mode"] == 0
    # Editing purported cost evidence cannot manufacture a cheaper validated sample.
    write(run / "metrics.json", {"tokens_used": 0, "repair_attempts_per_task": 0})
    row = result_observation(run, workflow, {"tokens_used": 0})
    cohort = compare_results([row])["direct_codex"]
    assert cohort["runs"] == 0 and cohort["excluded_unverified"] == 1


@pytest.mark.parametrize("evidence", ["raw-events/sdk-events.jsonl", "role-results/implementation-agent.json", "artifacts/quality.json"])
def test_changed_evidence_is_excluded_even_after_checkout_archival(tmp_path: Path, disposable: tuple[Path, str], evidence: str) -> None:
    collect(tmp_path, disposable, FixtureRuntime())
    run = tmp_path / "runs" / "baseline-one"
    state = result_acceptance(run)
    record_result_acceptance(run, status="accepted", reason="", actor="human", expected_fingerprint=state["result_fingerprint"])
    disposable[0].rename(disposable[0].with_name("archived"))
    workflow = json.loads((run / "workflow.json").read_text())
    metrics = json.loads((run / "metrics.json").read_text())
    cohort = compare_results([result_observation(run, workflow, metrics)])["direct_codex"]
    assert cohort["runs"] == 1 and cohort["rated"] == 0 and cohort["historical_only"]["accepted"] == 1
    write(run / evidence, {"fabricated": True})
    assert compare_results([result_observation(run, workflow, metrics)])["direct_codex"]["runs"] == 0


@pytest.mark.parametrize("allowed", ["../escape.txt", "auth/session.py", ".env", "nested/.env.local", "secret.txt", "Makefile"])
def test_protected_and_control_paths_rejected_before_model(tmp_path: Path, disposable: tuple[Path, str], allowed: str) -> None:
    runtime = FixtureRuntime()
    # Makefile is execution configuration; the baseline must not let its writer weaken checks.
    with pytest.raises(BaselineError):
        collect_baseline(repository=disposable[0], base_sha=disposable[1], goal="Update one file", allowed_paths=[allowed], run_id="unsafe", confirmed_disposable=True, runs_dir=tmp_path / "runs", runtime=runtime)
    assert runtime.calls == 0


@pytest.mark.parametrize("violation", ["outside", "history", "large"])
def test_runtime_output_boundary_cannot_be_laundered_by_successful_checks(tmp_path: Path, disposable: tuple[Path, str], violation: str) -> None:
    class EscapingRuntime(FixtureRuntime):
        def execute(self, **kwargs: object) -> dict:
            result = super().execute(**kwargs)
            repository = disposable[0]
            if violation == "outside":
                (repository / "other.txt").write_text("unexpected\n")
            elif violation == "history":
                git(repository, "add", "value.txt")
                git(repository, "commit", "-m", "Forbidden commit")
            else:
                (repository / "value.txt").write_text("many\n" * 201)
            return result
    runtime = EscapingRuntime()
    with pytest.raises(BaselineError):
        collect(tmp_path, disposable, runtime)
    assert runtime.calls == 1
    assert not (tmp_path / "runs" / "baseline-one" / "direct-baseline.json").exists()


def test_check_side_effects_are_revalidated_before_issuing_receipt(tmp_path: Path, disposable: tuple[Path, str]) -> None:
    repo, _ = disposable
    (repo / "Makefile").write_text('check:\n\t@echo unexpected > other.txt\nsecurity:\n\t@true\n')
    git(repo, "add", "Makefile")
    git(repo, "commit", "-m", "Fixture with mutating check")
    with pytest.raises(BaselineError, match="allowed paths"):
        collect(tmp_path, (repo, git(repo, "rev-parse", "HEAD")), FixtureRuntime())
    assert not (tmp_path / "runs" / "baseline-one" / "direct-baseline.json").exists()


@pytest.mark.parametrize("protected", ["token.txt", "docs/projects/demo/issues/issue-1.md", "artifacts/policy.txt"])
def test_active_profile_protected_paths_block_before_runtime(tmp_path: Path, disposable: tuple[Path, str], protected: str) -> None:
    repo, _ = disposable
    config = default_config(repo, profile="nextjs_web", base_branch="main")
    write_project_config(config, force=True)
    register_local_project(config)
    git(repo, "add", ".agent/project.yaml")
    git(repo, "commit", "-m", "Select web profile")
    runtime = FixtureRuntime()
    with pytest.raises(BaselineError, match="active policy protects"):
        collect_baseline(repository=repo, base_sha=git(repo, "rev-parse", "HEAD"), goal="Update a small file", allowed_paths=[protected], run_id="protected", confirmed_disposable=True, runs_dir=tmp_path / "runs", runtime=runtime)
    assert runtime.calls == 0


def test_active_policy_additions_apply_to_actual_changes_after_runtime(tmp_path: Path, disposable: tuple[Path, str], monkeypatch: pytest.MonkeyPatch) -> None:
    import yaml
    import run_direct_baseline as collector
    home = tmp_path / "home"
    home.mkdir()
    policy = yaml.safe_load((ROOT / ".agent-policy.yaml").read_text())
    (home / ".agent-policy.yaml").write_text(yaml.safe_dump(policy))
    (home / ".agent-repositories.yaml").write_bytes((ROOT / ".agent-repositories.yaml").read_bytes())
    monkeypatch.setattr(collector, "harness_home", lambda: home)
    class PolicyChangeRuntime(FixtureRuntime):
        def execute(self, **kwargs: object) -> dict:
            result = super().execute(**kwargs)
            policy["projects"]["agent_workspace"] = {"protected_paths": ["value.txt"]}
            (home / ".agent-policy.yaml").write_text(yaml.safe_dump(policy))
            return result
    runtime = PolicyChangeRuntime()
    with pytest.raises(BaselineError, match="active policy protects"):
        collect(tmp_path, disposable, runtime)
    assert runtime.calls == 1 and not (tmp_path / "runs" / "baseline-one" / "direct-baseline.json").exists()
    # The same new rule also blocks the next request before any runtime work.
    git(disposable[0], "restore", "value.txt")
    second = FixtureRuntime()
    with pytest.raises(BaselineError, match="active policy protects"):
        collect(tmp_path, disposable, second)
    assert second.calls == 0


@pytest.mark.parametrize("repair", [False, True])
def test_baseline_token_measure_comes_from_sdk_usage_not_model_claim(tmp_path: Path, disposable: tuple[Path, str], repair: bool) -> None:
    class UsageRuntime(FixtureRuntime):
        def execute(self, **kwargs: object) -> dict:
            result = super().execute(**kwargs)
            artifacts = kwargs["artifacts"]
            if repair:
                result["output_repair_attempts"] = 1
                write(artifacts.parent / "raw-events" / "implementation-agent.json", {"provider": "codex-sdk", "role": "implementation-agent", "returncode": 0, "stdout": json.dumps(result), "stderr": ""})
            write(artifacts.parent / "raw-events" / "implementation-agent.jsonl", {"provider": "codex-sdk", "status": "completed", "usage": {"input_tokens": 100, "cached_input_tokens": 80, "output_tokens": 3}})
            return result
    collect(tmp_path, disposable, UsageRuntime())
    run = tmp_path / "runs" / "baseline-one"
    metrics = json.loads((run / "metrics.json").read_text())
    workflow = json.loads((run / "workflow.json").read_text())
    assert metrics["tokens_used"] == (None if repair else 23)
    cohort = compare_results([result_observation(run, workflow, metrics)])["direct_codex"]
    assert cohort["runs"] == 1
    assert cohort["measurements"]["tokens_used"]["mean"] == (None if repair else 23)


def test_unreviewed_archived_baseline_keeps_verified_historical_measurements(tmp_path: Path, disposable: tuple[Path, str]) -> None:
    collect(tmp_path, disposable, FixtureRuntime())
    run = tmp_path / "runs" / "baseline-one"
    disposable[0].rename(disposable[0].with_name("archived"))
    workflow = json.loads((run / "workflow.json").read_text())
    metrics = json.loads((run / "metrics.json").read_text())
    cohort = compare_results([result_observation(run, workflow, metrics)])["direct_codex"]
    assert cohort["runs"] == 1 and cohort["unrated"] == 1 and cohort["rated"] == 0
    assert cohort["excluded_unverified"] == 0 and cohort["historical_measurements"] == 1
    assert cohort["historical_only"]["rated"] == 0
    result = result_acceptance(run)
    assert result["eligible"] is False and result["current"] is None
    assert result["archived_result"]["evidence"]["changed_files"] == ["value.txt"]
    assert result["evidence"]["verification_kind"] == "deterministic_baseline"
    assert result["evidence"]["goal"] == "Update the value text"
    assert '"goal"' not in json.dumps(result_observation(run, workflow, metrics)["acceptance"])
