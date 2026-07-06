from __future__ import annotations

import importlib.util
import stat
import sys
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "check_codex_runtime.py"
SPEC = importlib.util.spec_from_file_location("check_codex_runtime", MODULE_PATH)
assert SPEC is not None
check_codex_runtime = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = check_codex_runtime
SPEC.loader.exec_module(check_codex_runtime)


def test_codex_preflight_blocks_when_help_probe_fails(tmp_path: Path) -> None:
    fake_codex = tmp_path / "codex"
    fake_codex.write_text(
        """
import sys

print("runtime too old", file=sys.stderr)
raise SystemExit(1)
""".lstrip(),
        encoding="utf-8",
    )
    fake_codex.chmod(fake_codex.stat().st_mode | stat.S_IXUSR)

    result = check_codex_runtime.check_codex_runtime(
        repo=tmp_path,
        codex_command=f"{sys.executable} {fake_codex}",
        run_exec_probe=False,
    )

    assert result["execution_status"] == "blocked"
    assert result["blockers"][0] == "Codex CLI is not available or not authenticated."
    assert "runtime too old" in result["blockers"][1]
