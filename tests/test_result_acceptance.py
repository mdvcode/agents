from __future__ import annotations

import json
import stat
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from ai_harness.result_acceptance import (
    HISTORY_FILE,
    ResultAcceptanceError,
    StaleResultError,
    compare_results,
    record_result_acceptance,
    result_acceptance,
    result_observation,
)


def write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def git(repository: Path, *arguments: str) -> str:
    result = subprocess.run(["git", *arguments], cwd=repository, capture_output=True, check=True)
    return result.stdout.decode().strip()


@pytest.fixture
def completed_run(tmp_path: Path) -> Path:
    repository = tmp_path / "repository"
    repository.mkdir()
    git(repository, "init", "-b", "main")
    git(repository, "config", "user.email", "tests@example.test")
    git(repository, "config", "user.name", "Tests")
    (repository / "export.py").write_text("value = 'None'\n")
    git(repository, "add", "export.py")
    git(repository, "commit", "-m", "Base")
    base = git(repository, "rev-parse", "HEAD")
    git(repository, "switch", "-c", "fix/export")
    (repository / "export.py").write_text("value = ''\n")
    run = tmp_path / ".agent-runs" / "result-1"
    write(run / "workflow.json", {
        "run_id": run.name, "execution_status": "completed", "effective_mode": "fast",
        "repository": str(repository), "checkout_path": str(repository), "task_branch": "fix/export",
        "base_sha": base, "goal": "Fix CSV export", "project_profile": "agent_workspace",
        "elapsed_seconds": 12, "tokens_used": 50, "loops": {}, "attention_history": [],
        "roles": [{"role": "implementation-agent", "result": {"status": "completed", "summary": "Fixed empty CSV cells."}}],
    })
    write(run / "artifacts" / "verdict.json", {
        "execution_status": "completed", "decision": "local_complete", "checks_passed": True,
    })
    write(run / "artifacts" / "quality.json", {"overall_status": "pass"})
    write(run / "artifacts" / "security.json", {"verdict": "works"})
    write(run / "artifacts" / "review.json", {"verdict": "works"})
    return run


def assess(run: Path, status: str = "accepted", reason: str = "", actor: str = "reviewer") -> dict:
    return record_result_acceptance(
        run, status=status, reason=reason, actor=actor,
        expected_fingerprint=result_acceptance(run)["result_fingerprint"],
    )


def test_feedback_is_append_only_and_never_mutates_execution_authority(completed_run: Path) -> None:
    run = completed_run
    immutable = {path: path.read_bytes() for path in run.rglob("*") if path.is_file()}
    first = assess(run)
    prefix = (run / HISTORY_FILE).read_bytes()
    second = assess(run, "needs_changes", "Also preserve quoted empty values")
    third = assess(run, "rejected", "The behavior does not meet the requested format")
    assert first["current"]["status"] == "accepted"
    assert second["current"]["sequence"] == 2
    assert third["history_count"] == 3
    assert (run / HISTORY_FILE).read_bytes().startswith(prefix)
    assert result_acceptance(run)["current"]["status"] == "rejected"
    assert stat.S_IMODE((run / HISTORY_FILE).stat().st_mode) == 0o600
    assert all(path.read_bytes() == original for path, original in immutable.items())
    assert set(path for path in run.rglob("*") if path.is_file()) == set(immutable) | {run / HISTORY_FILE}


def test_repeated_post_is_idempotent_and_concurrent_changes_have_valid_history(completed_run: Path) -> None:
    run = completed_run
    fingerprint = result_acceptance(run)["result_fingerprint"]
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: assess(run), range(4)))
    assert all(result["history_count"] == 1 for result in results)
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(lambda index: record_result_acceptance(
            run, status="needs_changes", reason=f"Check case {index}", actor="reviewer",
            expected_fingerprint=fingerprint,
        ), range(4)))
    assert result_acceptance(run)["history_count"] == 5


@pytest.mark.parametrize("change", ["tracked", "new_file", "checks", "repair"])
def test_result_changes_invalidate_old_rating_and_refuse_stale_posts(completed_run: Path, change: str) -> None:
    run = completed_run
    first = assess(run)
    workflow = json.loads((run / "workflow.json").read_text())
    repository = Path(workflow["checkout_path"])
    if change == "tracked":
        (repository / "export.py").write_text("value = None\n")
    elif change == "new_file":
        (repository / 'new "test".py').write_text("assert True\n")
    elif change == "checks":
        write(run / "artifacts" / "quality.json", {"overall_status": "fail"})
    elif change == "repair":
        workflow["execution_status"] = "running"
        write(run / "workflow.json", workflow)
    current = result_acceptance(run)
    assert current["current"] is None
    assert current["stale"] is True
    assert current["history_count"] == 1
    with pytest.raises(ResultAcceptanceError):
        record_result_acceptance(run, status="accepted", reason="", actor="reviewer", expected_fingerprint=first["result_fingerprint"])
    assert len((run / HISTORY_FILE).read_text().splitlines()) == 1


def test_fingerprint_error_is_distinct_for_api_conflict(completed_run: Path) -> None:
    with pytest.raises(StaleResultError):
        record_result_acceptance(completed_run, status="accepted", reason="", actor="reviewer", expected_fingerprint="old")
    assert not (completed_run / HISTORY_FILE).exists()


@pytest.mark.parametrize("status,reason", [("approved", "yes"), ("needs_changes", ""), ("rejected", " "), ("accepted", "x" * 2001)])
def test_invalid_feedback_never_creates_history(completed_run: Path, status: str, reason: str) -> None:
    with pytest.raises(ResultAcceptanceError):
        assess(completed_run, status, reason)
    assert not (completed_run / HISTORY_FILE).exists()


def test_sensitive_feedback_is_not_persisted(completed_run: Path) -> None:
    with pytest.raises(ResultAcceptanceError, match="credential-like"):
        assess(completed_run, "rejected", "password=" + "synthetic-example-value")
    assert not (completed_run / HISTORY_FILE).exists()


@pytest.mark.parametrize("missing", ["verdict.json", "review.json", "quality.json", "security.json"])
def test_missing_verification_is_unknown_not_reviewable(completed_run: Path, missing: str) -> None:
    (completed_run / "artifacts" / missing).unlink()
    state = result_acceptance(completed_run)
    assert state["eligible"] is False
    assert state["current"] is None
    with pytest.raises(ResultAcceptanceError):
        assess(completed_run)


def test_malformed_verdict_is_unavailable_not_a_metrics_failure(completed_run: Path) -> None:
    write(completed_run / "artifacts" / "verdict.json", {"execution_status": "completed", "decision": []})
    assert result_acceptance(completed_run)["eligible"] is False


def test_switched_checkout_and_missing_base_are_not_another_result(completed_run: Path) -> None:
    workflow = json.loads((completed_run / "workflow.json").read_text())
    repository = Path(workflow["checkout_path"])
    git(repository, "switch", "main")
    assert result_acceptance(completed_run)["eligible"] is False
    git(repository, "switch", "fix/export")
    workflow.pop("base_sha")
    write(completed_run / "workflow.json", workflow)
    assert result_acceptance(completed_run)["eligible"] is False


def test_symlink_history_cannot_read_or_overwrite_another_file(completed_run: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside.jsonl"
    outside.write_text("private\n")
    (completed_run / HISTORY_FILE).symlink_to(outside)
    assert result_acceptance(completed_run)["eligible"] is False
    with pytest.raises(ResultAcceptanceError):
        assess(completed_run)
    assert outside.read_text() == "private\n"


def test_damaged_history_fails_closed_and_does_not_overwrite(completed_run: Path) -> None:
    assess(completed_run)
    path = completed_run / HISTORY_FILE
    data = path.read_text().replace('"status": "accepted"', '"status": "rejected"')
    path.write_text(data)
    assert result_acceptance(completed_run)["eligible"] is False
    with pytest.raises(ResultAcceptanceError):
        assess(completed_run)
    assert path.read_text() == data


@pytest.mark.parametrize("url", ["javascript:alert(1)", "file:///private/result", "https://github.com/other/project/pull/1"])
def test_result_summary_never_exposes_unvalidated_publication_links(completed_run: Path, url: str) -> None:
    workflow = json.loads((completed_run / "workflow.json").read_text())
    repository = Path(workflow["checkout_path"])
    git(repository, "remote", "add", "origin", "git@github.com:example/project.git")
    write(completed_run / "artifacts" / "publication.json", {"pr_url": url})
    evidence = result_acceptance(completed_run)["evidence"]
    assert evidence["publication_url"] is None
    assert evidence["changed_files"] == ["export.py"]
    assert evidence["checks"] == [{"name": "quality", "status": "pass"}, {"name": "security", "status": "pass"}, {"name": "review", "status": "pass"}]
    write(completed_run / "artifacts" / "publication.json", {"pr_url": "https://github.com/example/project/pull/12"})
    assert result_acceptance(completed_run)["evidence"]["publication_url"] == "https://github.com/example/project/pull/12"


def observation(run: Path) -> dict:
    return result_observation(run, json.loads((run / "workflow.json").read_text()), {})


def test_comparison_uses_human_denominator_effective_mode_and_missing_values(completed_run: Path) -> None:
    unreviewed = observation(completed_run)
    assess(completed_run)
    accepted = observation(completed_run)
    assess(completed_run, "needs_changes", "Missing an edge case")
    needs_changes = observation(completed_run)
    needs_changes["run_id"] = "other-run"
    needs_changes["pair_key"] = "other-case"
    needs_changes["measurements"]["tokens_used"] = None
    result = compare_results([unreviewed, accepted, needs_changes])
    fast = result["modes"]["fast"]
    assert fast["runs"] == 3
    assert fast["rated"] == 2
    assert fast["acceptance_rate"] == 0.5
    assert fast["unrated"] == 1
    assert fast["measurements"]["tokens_used"] == {"samples": 2, "missing": 1, "mean": 50.0, "median": 50.0}
    assert fast["measurements"]["user_interventions"]["mean"] is None
    assert result["modes"]["full"]["acceptance_rate"] is None
    assert result["direct_codex"]["status"] == "not_available"
    assert result["winner"] is None


def test_observation_does_not_treat_requested_mode_or_approval_request_as_evidence(completed_run: Path) -> None:
    workflow = json.loads((completed_run / "workflow.json").read_text())
    workflow.pop("effective_mode")
    workflow["mode"] = "fast"
    workflow.pop("elapsed_seconds")
    workflow.pop("tokens_used")
    workflow["attention_history"] = [{"resolution": "answer_recorded"}]
    events = completed_run / "raw-events" / "approvals.jsonl"
    events.parent.mkdir()
    events.write_text('\n'.join(json.dumps({"event": kind}) for kind in ["approval.requested", "approval.approved", "approval.consumed"]) + '\n')
    row = result_observation(completed_run, workflow, {"human_interventions_per_task": 100})
    assert row["mode"] is None
    assert row["measurements"]["elapsed_seconds"] is None
    assert row["measurements"]["tokens_used"] is None
    assert row["measurements"]["user_interventions"] == 2
    assert compare_results([row])["excluded_unknown_or_other_mode"] == 1


def test_pairs_require_compatible_identity_and_do_not_cherry_pick_repeated_trials(completed_run: Path) -> None:
    assess(completed_run)
    fast = observation(completed_run)
    full = {**fast, "run_id": "full-run", "mode": "full"}
    paired = compare_results([fast, full])["paired"]
    assert paired["compatible_pairs"] == 1
    assert paired["rated_pairs"] == 1
    assert compare_results([fast, full, {**full, "run_id": "repeated-full"}])["paired"]["ambiguous_groups"] == 1
    assert compare_results([fast, {**full, "pair_key": "different-base"}])["paired"]["compatible_pairs"] == 0
    assert compare_results([fast, {**full, "acceptance": {**full["acceptance"], "current": None}}])["paired"]["rated_pairs"] == 0


def test_malformed_and_truncated_counters_stay_unknown(completed_run: Path) -> None:
    workflow = json.loads((completed_run / "workflow.json").read_text())
    workflow["attention_history"] = [{"resolution": "answer_recorded"}] * 50
    workflow["tokens_used"] = 10 ** 500
    workflow["elapsed_seconds"] = float("nan")
    path = completed_run / "raw-events" / "approvals.jsonl"
    path.parent.mkdir()
    path.write_text("")
    row = result_observation(completed_run, workflow, {})
    assert row["measurements"]["user_interventions"] is None
    assert row["measurements"]["tokens_used"] is None
    assert row["measurements"]["elapsed_seconds"] is None


def test_operational_metrics_exposes_feedback_without_changing_old_counters(completed_run: Path, tmp_path: Path) -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from operational_metrics import collect_metrics

    assess(completed_run)
    payload = collect_metrics(runs_dir=completed_run.parent, db_path=tmp_path / "queue.db")
    assert payload["runs"]["items"][0]["result_acceptance"]["current"]["status"] == "accepted"
    assert payload["result_comparison"]["modes"]["fast"]["rated"] == 1
    assert payload["budgets"]["tokens_used"] == 50
    assert payload["adaptive"]["adaptive_default_allowed"] is False


def test_staging_and_committing_identical_reviewed_files_preserves_current_rating(completed_run: Path) -> None:
    repository = Path(json.loads((completed_run / "workflow.json").read_text())["checkout_path"])
    (repository / "new_file.py").write_text("value = 1\n")
    first = assess(completed_run)
    git(repository, "add", ".")
    assert result_acceptance(completed_run)["current"]["result_fingerprint"] == first["result_fingerprint"]
    git(repository, "commit", "-m", "Save reviewed files")
    assert result_acceptance(completed_run)["current"]["result_fingerprint"] == first["result_fingerprint"]


def test_archived_checkout_preserves_exact_historical_rating_separately(completed_run: Path) -> None:
    first = assess(completed_run)
    repository = Path(json.loads((completed_run / "workflow.json").read_text())["checkout_path"])
    repository.rename(repository.with_name("archived"))
    state = result_acceptance(completed_run)
    assert state["eligible"] is False and state["current"] is None
    assert state["historical"]["availability"] == "unavailable"
    assert state["historical"]["snapshot"]["result_fingerprint"] == first["result_fingerprint"]
    cohort = compare_results([observation(completed_run)])["modes"]["fast"]
    assert cohort["rated"] == 0 and cohort["historical_only"]["accepted"] == 1


def test_repaired_result_keeps_old_snapshot_but_does_not_reuse_its_rating(completed_run: Path) -> None:
    first = assess(completed_run)
    write(completed_run / "artifacts" / "quality.json", {"overall_status": "fail"})
    state = result_acceptance(completed_run)
    assert state["current"] is None and state["historical"]["availability"] == "superseded"
    assert state["historical"]["snapshot"]["checks"]["quality"] == "pass"
    assert state["historical"]["snapshot"]["result_fingerprint"] == first["result_fingerprint"]


def test_original_goal_stays_in_result_endpoint_and_out_of_metrics(completed_run: Path) -> None:
    assess(completed_run)
    protected = result_acceptance(completed_run)
    assert protected["evidence"]["goal"] == "Fix CSV export"
    assert protected["historical"]["snapshot"]["evidence"]["goal"] == "Fix CSV export"
    assert '"goal"' not in json.dumps(observation(completed_run)["acceptance"])
