from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_makefile_exposes_real_codex_smoke_gate() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")

    assert "codex-preflight:" in makefile
    assert "CODEX_NODE_PATH ?=" in makefile
    assert "python3 scripts/check_codex_runtime.py --repo ." in makefile
    assert "codex-smoke:" in makefile
    assert 'PATH="$(CODEX_NODE_PATH):$$PATH"' in makefile
    assert "AGENT_REAL_CODEX_SMOKE=1 AGENT_CODEX_CLI_COMMAND=codex" in makefile
    assert "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest tests/test_real_codex_smoke.py -q" in makefile


def test_docs_describe_codex_preflight_and_smoke() -> None:
    onboarding = (ROOT / "docs" / "onboarding.md").read_text(encoding="utf-8")
    agent_system = (ROOT / "docs" / "agent-system.md").read_text(encoding="utf-8")

    assert "make codex-preflight" in onboarding
    assert "make codex-smoke" in onboarding
    assert "make codex-preflight" in agent_system
    assert "make codex-smoke" in agent_system
