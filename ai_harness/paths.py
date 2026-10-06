"""Locate the Harness control plane in a source checkout or pipx environment."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path


class HarnessNotFoundError(RuntimeError):
    """Raised when the installed CLI cannot find its bundled Harness resources."""


def is_harness_home(path: Path) -> bool:
    return (
        path.is_dir()
        and (path / ".agent-runtime.yaml").is_file()
        and (path / "scripts" / "task_queue.py").is_file()
        and (path / "schemas" / "task_envelope.schema.json").is_file()
    )


def home_config_path() -> Path:
    configured = os.environ.get("AI_HARNESS_CONFIG_HOME", "").strip()
    root = Path(configured).expanduser() if configured else Path.home() / ".config" / "ai-harness"
    return root / "home.json"


def validated_harness_home(path: Path) -> Path:
    try:
        selected = path.expanduser().resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        raise HarnessNotFoundError(
            "Cannot resolve Harness home; use `agent home --set PATH` or "
            "`agent home --clear`, or unset AI_HARNESS_HOME if it is configured."
        ) from exc
    if not is_harness_home(selected):
        raise HarnessNotFoundError(
            f"Invalid Harness home: {selected}. Select an existing control plane with "
            "`agent home --set PATH`; clear a stale selection with `agent home --clear` "
            "or unset AI_HARNESS_HOME if it is configured."
        )
    return selected


def save_harness_home(path: Path) -> Path:
    selected = validated_harness_home(path)
    config = home_config_path()
    if config.is_symlink() or config.parent.is_symlink():
        raise HarnessNotFoundError("Refusing to write Harness home through a symbolic link")
    config.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, name = tempfile.mkstemp(prefix=".home-", suffix=".tmp", dir=config.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump({"version": 1, "home": str(selected)}, output)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(config)
    finally:
        temporary.unlink(missing_ok=True)
    return selected


def saved_harness_home() -> Path | None:
    config = home_config_path()
    try:
        document = json.loads(config.read_text(encoding="utf-8"))
    except FileNotFoundError:
        if config.is_symlink():
            raise HarnessNotFoundError(
                f"Broken Harness home selection {config}; use `agent home --clear`."
            ) from None
        return None
    except (OSError, ValueError) as exc:
        raise HarnessNotFoundError(
            f"Cannot read Harness home selection {config}; use `agent home --set PATH` "
            "or `agent home --clear`."
        ) from exc
    if (
        not isinstance(document, dict)
        or document.get("version") != 1
        or not isinstance(document.get("home"), str)
        or not document["home"].strip()
    ):
        raise HarnessNotFoundError(
            f"Invalid Harness home selection {config}; use `agent home --set PATH` "
            "or `agent home --clear`."
        )
    return validated_harness_home(Path(document["home"]))


def harness_home() -> Path:
    configured = os.environ.get("AI_HARNESS_HOME", "").strip()
    if configured:
        return validated_harness_home(Path(configured))
    saved = saved_harness_home()
    if saved is not None:
        return saved
    candidates = [
        Path(__file__).resolve().parents[1],
        Path(sys.prefix) / "share" / "ai-harness",
    ]
    for candidate in candidates:
        if candidate is not None and is_harness_home(candidate.resolve()):
            return candidate.resolve()
    checked = ", ".join(str(path) for path in candidates if path is not None)
    raise HarnessNotFoundError(
        f"Harness resources were not found. Checked: {checked}. "
        "Reinstall ai-harness or set AI_HARNESS_HOME."
    )
