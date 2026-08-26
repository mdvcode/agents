"""Deterministic task classification for adaptive workflow planning."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence


TASK_CLASSES = {
    "documentation",
    "configuration",
    "bugfix",
    "feature",
    "refactor",
    "test",
    "security",
    "dependency",
    "migration",
    "frontend",
    "architecture",
    "multi_repo",
    "unknown",
}
SCOPES = {"trivial", "small", "medium", "large"}
RISK_LEVELS = {"low", "medium", "high"}

_CLASS_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("multi_repo", ("multi-repo", "multi repo", "нескольк", "cross-repository")),
    ("security", ("security", "auth", "permission", "secret", "csrf", "безопас", "авторизац", "аутентификац")),
    ("migration", ("migration", "schema change", "миграц", "database schema")),
    ("dependency", ("dependency", "dependencies", "upgrade package", "lockfile", "зависимост", "обновить пакет")),
    ("architecture", ("architecture", "boundary", "архитектур", "redesign", "re-architect")),
    ("frontend", ("frontend", "css", "browser", "ui", "верст", "интерфейс", "template")),
    ("documentation", ("documentation", "docs", "readme", "typo", "документац", "опечат")),
    ("configuration", ("configuration", "config", "настрой", "yaml", "toml")),
    ("test", ("tests only", "test only", "add tests", "coverage", "тест")),
    ("refactor", ("refactor", "cleanup", "рефактор", "перестро")),
    ("bugfix", ("bug", "fix", "broken", "regression", "ошиб", "исправ", "не работает")),
    ("feature", ("feature", "implement", "add ", "создать", "добавить", "реализ")),
)

_PATH_CLASS_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("migration", ("/migrations/",)),
    ("dependency", ("requirements", "pyproject.toml", "package.json", "lock")),
    ("security", ("/auth/", "/permissions/", "/secrets/", "csrf", "security")),
    ("frontend", (".css", ".scss", ".tsx", ".jsx", ".vue", "/templates/", "/pages/", "/app/")),
    ("documentation", (".md", ".rst", "/docs/")),
    ("test", ("/tests/", "test_", ".spec.", ".test.")),
    ("configuration", (".yaml", ".yml", ".toml", ".ini", ".cfg")),
)

_PROTECTED_PARTS = {
    "auth",
    "billing",
    "payments",
    "credentials",
    "secrets",
    "migrations",
    "terraform",
}
_HIGH_RISK_HINTS = (
    "production",
    "deploy",
    "credential",
    "secret",
    "payment",
    "billing",
    "migration",
    "irreversible",
    "продакш",
    "секрет",
    "оплат",
    "миграц",
)
_PUBLIC_INTERFACE_HINTS = (
    "public api",
    "public interface",
    "contract change",
    "breaking change",
    "backward compatibility",
)

_TOOL_ALIASES: Mapping[str, tuple[str, ...]] = {
    "pytest": ("tests",),
    "python": ("tests",),
    "python3": ("tests",),
    "ruff": ("format", "lint"),
    "black": ("format",),
    "prettier": ("format",),
    "eslint": ("lint",),
    "mypy": ("types",),
    "pyright": ("types",),
    "tsc": ("types",),
    "detect-secrets": ("secret_scan",),
    "security_scan": ("secret_scan",),
    "pip-audit": ("dependency_audit",),
    "npm-audit": ("dependency_audit",),
    "git": ("diff_size",),
}


def _normalize_path(value: str) -> str:
    normalized = value.replace("\\", "/").strip().casefold()
    return f"/{normalized.lstrip('/')}"


def _contains_any(text: str, values: Iterable[str]) -> bool:
    return any(value in text for value in values)


def _normalize_tools(values: Sequence[str]) -> tuple[str, ...]:
    normalized: set[str] = set()
    for value in values:
        tool = value.strip().casefold()
        if not tool:
            continue
        normalized.add(tool)
        normalized.update(_TOOL_ALIASES.get(tool, ()))
    return tuple(sorted(normalized))


@dataclass(frozen=True)
class TaskAnalysis:
    """Stable, JSON-serializable task facts used by the workflow compiler."""

    task_class: str
    scope: str
    risk: str
    domains: tuple[str, ...]
    requires_code_change: bool
    requires_tests: bool
    requires_security_review: bool
    requires_architecture_review: bool
    requires_frontend_verification: bool
    requires_semantic_review: bool
    confidence: float
    indicators: tuple[str, ...] = ()
    requested_paths: tuple[str, ...] = ()
    deterministic_tools: tuple[str, ...] = ()
    historical_failures: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.task_class not in TASK_CLASSES:
            raise ValueError(f"unknown task class: {self.task_class}")
        if self.scope not in SCOPES:
            raise ValueError(f"unknown task scope: {self.scope}")
        if self.risk not in RISK_LEVELS:
            raise ValueError(f"unknown task risk: {self.risk}")
        if not 0 <= self.confidence <= 1:
            raise ValueError("task confidence must be between zero and one")

    def as_dict(self) -> dict[str, object]:
        value = asdict(self)
        for field in (
            "domains",
            "indicators",
            "requested_paths",
            "deterministic_tools",
            "historical_failures",
        ):
            value[field] = list(value[field])
        return value


class TaskAnalyzer:
    """Classify task text and scoped repository facts without an LLM."""

    def analyze(
        self,
        task_text: str,
        *,
        repository_profile: str,
        requested_paths: Sequence[str] = (),
        project_type: str = "",
        deterministic_tools: Sequence[str] = (),
        historical_failures: Sequence[str] = (),
        metadata: Mapping[str, object] | None = None,
    ) -> TaskAnalysis:
        normalized = re.sub(r"\s+", " ", task_text.casefold()).strip()
        paths = tuple(sorted({_normalize_path(path) for path in requested_paths if path.strip()}))
        task_class, class_confidence = self._task_class(normalized, paths)
        scope = self._scope(normalized, paths, task_class, metadata or {})
        available_tools = set(_normalize_tools(deterministic_tools))
        indicator_values = set(self._indicators(normalized, paths, task_class, scope))
        risk = self._risk(tuple(indicator_values), scope, historical_failures)
        if risk == "low" and not indicator_values & {
            "auth_change",
            "permissions_change",
            "secrets_change",
            "dependency_change",
            "migration_change",
            "network_boundary_change",
        }:
            indicator_values.add("deterministic_security_sufficient")
        domains = self._domains(repository_profile, project_type, paths, task_class)
        code_change = task_class not in {"documentation", "configuration", "unknown"} or any(
            Path(path).suffix in {".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java"}
            for path in paths
        )
        frontend = task_class == "frontend" or "frontend_change" in indicator_values
        architecture = task_class in {"architecture", "multi_repo"} or bool(
            {"architecture_change", "public_interface_change", "cross_module_refactor", "dependency_boundary_change"}
            & indicator_values
        )
        security = task_class in {"security", "dependency", "migration"} or bool(
            {"auth_change", "permissions_change", "secrets_change", "network_boundary_change"}
            & indicator_values
        )
        semantic = bool(
            {"public_behavior_change", "complex_refactor", "contract_change"} & indicator_values
        )
        expected_tools = {"tests"} if code_change and task_class not in {"dependency", "migration"} else set()
        if task_class == "dependency":
            expected_tools.add("dependency_audit")
        if security:
            expected_tools.add("secret_scan")
        if expected_tools - available_tools:
            indicator_values.add("deterministic_tool_gap")
        indicators = tuple(sorted(indicator_values))
        confidence = class_confidence
        if not normalized:
            confidence = 0.1
        elif not paths:
            confidence -= 0.05
        if historical_failures:
            confidence -= min(0.15, len(historical_failures) * 0.03)
        if "deterministic_tool_gap" in indicator_values:
            confidence -= 0.05
        if task_class == "unknown":
            confidence = min(confidence, 0.45)
        confidence = round(max(0.0, min(0.99, confidence)), 2)
        return TaskAnalysis(
            task_class=task_class,
            scope=scope,
            risk=risk,
            domains=domains,
            requires_code_change=code_change,
            requires_tests=code_change and task_class not in {"dependency", "migration"},
            requires_security_review=security,
            requires_architecture_review=architecture,
            requires_frontend_verification=frontend,
            requires_semantic_review=semantic,
            confidence=confidence,
            indicators=indicators,
            requested_paths=tuple(path.lstrip("/") for path in paths),
            deterministic_tools=tuple(sorted(available_tools)),
            historical_failures=tuple(historical_failures),
        )

    @staticmethod
    def _task_class(text: str, paths: Sequence[str]) -> tuple[str, float]:
        path_text = " ".join(paths)
        for task_class, hints in _PATH_CLASS_HINTS:
            matches = [path for path in paths if _contains_any(path, hints)]
            if not matches:
                continue
            if task_class in {"documentation", "configuration", "test"} and len(matches) != len(paths):
                continue
            return task_class, 0.93
        for task_class, hints in _CLASS_HINTS:
            if _contains_any(text, hints):
                return task_class, 0.9
        return "unknown", 0.4

    @staticmethod
    def _scope(
        text: str,
        paths: Sequence[str],
        task_class: str,
        metadata: Mapping[str, object],
    ) -> str:
        explicit = str(metadata.get("scope", ""))
        if explicit in SCOPES:
            return explicit
        if _contains_any(text, ("repository-wide", "large", "major", "platform", "entire", "полностью", "весь репозитор")):
            return "large"
        if task_class in {"multi_repo", "architecture", "migration"}:
            return "large"
        if _contains_any(text, ("medium", "several modules", "несколько модул", "end-to-end", "cross-module")):
            return "medium"
        if len(paths) > 8:
            return "large"
        if len(paths) > 3 or task_class in {"feature", "refactor", "dependency"}:
            return "medium"
        if task_class in {"documentation", "configuration", "test"} and len(paths) <= 1:
            return "trivial"
        return "small"

    @staticmethod
    def _indicators(text: str, paths: Sequence[str], task_class: str, scope: str) -> tuple[str, ...]:
        indicators: set[str] = set()
        path_text = " ".join(paths)
        parts = {part for path in paths for part in Path(path).parts}
        if parts & _PROTECTED_PARTS:
            indicators.add("protected_path_change")
        if _contains_any(text, _HIGH_RISK_HINTS):
            indicators.add("high_risk_operation")
        if "auth" in parts or _contains_any(text, ("authentication", "authorization", " auth", "аутентификац", "авторизац")):
            indicators.add("auth_change")
        if "permissions" in parts or "permission" in text:
            indicators.add("permissions_change")
        if "secrets" in parts or _contains_any(text, ("secret", "credential", "token rotation")):
            indicators.add("secrets_change")
        if _contains_any(text + path_text, ("network", "webhook", "firewall", "external api")):
            indicators.add("network_boundary_change")
        if task_class == "frontend":
            indicators.update(("frontend_change", "public_behavior_change"))
        if _contains_any(path_text, ("/templates/", ".html")):
            indicators.add("template_change")
        if _contains_any(path_text, (".css", ".scss")):
            indicators.add("css_change")
        if _contains_any(text, ("browser", "interaction", "click", "navigation")):
            indicators.add("browser_behavior_change")
        if task_class == "architecture":
            indicators.add("architecture_change")
        if task_class == "dependency":
            indicators.add("dependency_change")
            if scope in {"medium", "large"}:
                indicators.add("dependency_boundary_change")
        if task_class == "refactor":
            indicators.add("complex_refactor" if scope in {"medium", "large"} else "local_refactor")
            if scope in {"medium", "large"}:
                indicators.add("cross_module_refactor")
        if _contains_any(text, _PUBLIC_INTERFACE_HINTS):
            indicators.update(("public_interface_change", "contract_change", "public_behavior_change"))
        if task_class == "feature":
            indicators.add("public_behavior_change")
        if task_class == "documentation":
            indicators.add("documentation_only")
        if task_class == "test":
            indicators.add("tests_only")
        if scope == "trivial":
            indicators.add("trivial_change")
        if scope == "small" and not ({"auth_change", "permissions_change", "secrets_change"} & indicators):
            indicators.add("low_risk_local_change")
        if task_class == "migration":
            indicators.add("migration_change")
        return tuple(sorted(indicators))

    @staticmethod
    def _risk(indicators: Sequence[str], scope: str, historical_failures: Sequence[str]) -> str:
        values = set(indicators)
        if values & {
            "auth_change",
            "permissions_change",
            "secrets_change",
            "migration_change",
            "protected_path_change",
            "high_risk_operation",
        }:
            return "high"
        if values & {"network_boundary_change", "dependency_change", "public_interface_change"}:
            return "medium"
        if scope == "large" or historical_failures:
            return "medium"
        return "low"

    @staticmethod
    def _domains(
        repository_profile: str,
        project_type: str,
        paths: Sequence[str],
        task_class: str,
    ) -> tuple[str, ...]:
        domains: set[str] = set()
        combined = f"{repository_profile} {project_type}".casefold()
        path_text = " ".join(paths)
        if task_class == "multi_repo":
            domains.add("multi_repo")
        if task_class == "frontend" or any(value in combined for value in ("nextjs", "frontend", "react")):
            domains.add("frontend")
        if task_class == "documentation":
            domains.add("documentation")
        if task_class == "configuration":
            domains.add("configuration")
        if task_class in {"security", "dependency", "migration"}:
            domains.add("security")
        if any(value in combined for value in ("django", "python", "control_plane", "agent_workspace")) or ".py" in path_text:
            domains.add("backend")
        return tuple(sorted(domains or {"general"}))
