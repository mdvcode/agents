"""Bounded static knowledge sources for Context Engine."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Protocol, Sequence

import yaml

from .content_guard import source_privacy

from .models import (
    DocumentType,
    KnowledgeDocument,
    KnowledgeRequest,
    KnowledgeType,
    TrustStatus,
)


DEFAULT_MAX_SOURCE_BYTES = 256_000
DEFAULT_MAX_FILES = 240
TEXT_SUFFIXES = {".md", ".mdx", ".txt", ".json", ".yaml", ".yml", ".toml"}
IGNORED_PARTS = {
    ".git",
    ".agent-runs",
    ".agent-queue",
    ".mypy_cache",
    ".pytest_cache",
    ".venv",
    "__pycache__",
    "node_modules",
}

ROLE_SKILLS: dict[str, tuple[str, ...]] = {
    "issue-intake": ("issue-intake", "repo-policy", "context-engineering"),
    "context-compiler": ("context-engineering", "repo-policy"),
    "planner": ("context-engineering", "repo-policy", "structured-output-guard"),
    "risk-classifier": ("repo-policy", "security-checklist"),
    "implementation-agent": ("repo-policy", "python-standards", "test-writing"),
    "test-generator": ("test-writing", "structured-output-guard"),
    "quality-runner": ("structured-output-guard",),
    "security-agent": ("security-checklist", "repo-policy"),
    "frontend-qa-agent": ("context-engineering",),
    "architecture-consistency-agent": ("repo-policy", "context-engineering"),
    "semantic-conflict-agent": ("repo-policy", "structured-output-guard"),
    "reviewer": ("repo-policy", "structured-output-guard"),
    "ci-repair-agent": ("repo-policy", "test-writing"),
    "orchestrator": ("repo-policy", "git-workflow", "structured-output-guard"),
    "eval-runner": ("structured-output-guard",),
    "report-agent": ("structured-output-guard",),
    "publication": ("git-workflow", "release-safety", "repo-policy"),
}

ROLE_ARTIFACTS: dict[str, set[str]] = {
    "planner": {"issue.json", "project_profile.json"},
    "risk-classifier": {"issue.json", "plan.md", "project_profile.json"},
    "implementation-agent": {"issue.json", "plan.md", "risk.json", "project_profile.json"},
    "test-generator": {"plan.md", "risk.json", "implementation.json", "project_profile.json"},
    "quality-runner": {"implementation.json", "test_plan.json", "test_result.json", "project_profile.json"},
    "security-agent": {"risk.json", "implementation.json", "security.json", "project_profile.json"},
    "frontend-qa-agent": {"implementation.json", "project_profile.json"},
    "architecture-consistency-agent": {"plan.md", "implementation.json", "project_profile.json"},
    "semantic-conflict-agent": {"plan.md", "implementation.json", "project_profile.json"},
    "reviewer": {
        "risk.json",
        "implementation.json",
        "test_result.json",
        "quality.json",
        "security.json",
        "frontend_qa.json",
        "architecture_consistency.json",
        "semantic_conflict.json",
        "project_profile.json",
    },
    "orchestrator": {
        "risk.json",
        "quality.json",
        "security.json",
        "review.json",
        "frontend_qa.json",
        "project_profile.json",
    },
}


class KnowledgeSource(Protocol):
    name: str

    def collect(self, request: KnowledgeRequest) -> tuple[KnowledgeDocument, ...]: ...


def _within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _document_id(source: str, path: str, title: str) -> str:
    value = f"{source}\0{path}\0{title}".encode("utf-8")
    return hashlib.sha256(value).hexdigest()[:20]


def _safe_text(path: Path, root: Path, max_bytes: int = DEFAULT_MAX_SOURCE_BYTES) -> str | None:
    if path.is_symlink() or not path.is_file() or not _within(path, root):
        return None
    try:
        size = path.stat().st_size
    except OSError:
        return None
    if size <= 0 or size > max_bytes:
        return None
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _relative_or_absolute(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path.resolve())


def _file_document(
    *,
    source: str,
    path: Path,
    root: Path,
    knowledge_type: KnowledgeType,
    document_type: DocumentType,
    priority: int,
    title: str = "",
    display_path: str = "",
    metadata: dict[str, str] | None = None,
) -> KnowledgeDocument | None:
    content = _safe_text(path, root)
    if content is None:
        return None
    selected_path = display_path or _relative_or_absolute(path, root)
    selected_title = title or path.stem.replace("-", " ").replace("_", " ").strip()
    return KnowledgeDocument(
        id=_document_id(source, selected_path, selected_title),
        title=selected_title,
        content=content,
        source=source,
        path=selected_path,
        knowledge_type=knowledge_type,
        document_type=document_type,
        priority=priority,
        metadata=metadata or {},
        privacy=source_privacy(content),
        trust=(
            TrustStatus.TRUSTED
            if knowledge_type
            in {
                KnowledgeType.ARTIFACT,
                KnowledgeType.CONTRACT,
                KnowledgeType.POLICY,
                KnowledgeType.PROJECT_PROFILE,
                KnowledgeType.SKILL,
            }
            else TrustStatus.UNTRUSTED_REFERENCE
        ),
    )


def _iter_bounded_files(
    roots: Sequence[Path],
    *,
    suffixes: set[str] = TEXT_SUFFIXES,
    max_files: int = DEFAULT_MAX_FILES,
) -> Iterable[tuple[Path, Path]]:
    yielded = 0
    for root in roots:
        if yielded >= max_files or root.is_symlink() or not root.is_dir():
            continue
        resolved_root = root.resolve()
        for current, directory_names, file_names in os.walk(resolved_root, topdown=True, followlinks=False):
            current_path = Path(current)
            directory_names[:] = sorted(
                name
                for name in directory_names
                if name not in IGNORED_PARTS
                and not name.startswith(".")
                and not (current_path / name).is_symlink()
            )
            for file_name in sorted(file_names):
                if yielded >= max_files:
                    return
                if file_name.startswith("."):
                    continue
                path = current_path / file_name
                if path.suffix.lower() not in suffixes or path.is_symlink() or not _within(path, resolved_root):
                    continue
                try:
                    size = path.stat().st_size
                except OSError:
                    continue
                if size <= 0 or size > DEFAULT_MAX_SOURCE_BYTES:
                    continue
                yielded += 1
                yield path, resolved_root


@dataclass(frozen=True)
class RepositoryDocumentationSource:
    name: str = "repository_documentation"
    max_files: int = DEFAULT_MAX_FILES

    def collect(self, request: KnowledgeRequest) -> tuple[KnowledgeDocument, ...]:
        repository = request.repository.resolve()
        candidates: set[Path] = set(repository.glob("README*"))
        candidates.update(repository.glob("ARCHITECTURE*"))
        docs = repository / "docs"
        if docs.is_dir() and not docs.is_symlink():
            candidates.update(path for path, _ in _iter_bounded_files((docs,), max_files=self.max_files))
        documents: list[KnowledgeDocument] = []
        for path in sorted(candidates)[: self.max_files]:
            if path.suffix.lower() not in TEXT_SUFFIXES and not path.name.lower().startswith("readme"):
                continue
            lowered = "/".join(part.lower() for part in path.relative_to(repository).parts)
            if "/memory/" in f"/{lowered}/" or "/issues/" in f"/{lowered}/":
                continue
            if path.name == "AGENTS.md":
                continue
            if "decision" in lowered or "/adr" in f"/{lowered}" or path.name.lower().startswith("adr-"):
                document_type = DocumentType.ADR
                priority = 70
            elif path.name.lower().startswith("readme"):
                document_type = DocumentType.README
                priority = 60
            elif "/wiki/" in f"/{lowered}/":
                document_type = DocumentType.WIKI
                priority = 55
            else:
                document_type = DocumentType.PROJECT_DOC
                priority = 30
            document = _file_document(
                source=self.name,
                path=path,
                root=repository,
                knowledge_type=KnowledgeType.DOCUMENTATION,
                document_type=document_type,
                priority=priority,
            )
            if document is not None:
                documents.append(document)
        return tuple(documents)


@dataclass(frozen=True)
class RepositoryMetadataSource:
    name: str = "git_repository"
    max_entries: int = 400

    def collect(self, request: KnowledgeRequest) -> tuple[KnowledgeDocument, ...]:
        repository = request.repository.resolve()
        entries: list[str] = []
        for current, directory_names, file_names in os.walk(repository, topdown=True, followlinks=False):
            current_path = Path(current)
            directory_names[:] = sorted(
                name
                for name in directory_names
                if name not in IGNORED_PARTS
                and not name.startswith(".")
                and not (current_path / name).is_symlink()
            )
            for file_name in sorted(file_names):
                if file_name.startswith("."):
                    continue
                path = current_path / file_name
                if path.is_symlink() or not path.is_file():
                    continue
                entries.append(str(path.relative_to(repository)))
                if len(entries) >= self.max_entries:
                    break
            if len(entries) >= self.max_entries:
                break
        content = "Repository file index (bounded):\n" + "\n".join(f"- {item}" for item in entries)
        return (
            KnowledgeDocument(
                id=_document_id(self.name, ".", "Repository index"),
                title="Repository index",
                content=content,
                source=self.name,
                path=".",
                knowledge_type=KnowledgeType.REPOSITORY,
                document_type=DocumentType.REPOSITORY,
                priority=35,
                metadata={"entry_count": str(len(entries))},
            ),
        )


@dataclass(frozen=True)
class PolicySource:
    control_root: Path
    name: str = "policies"

    def collect(self, request: KnowledgeRequest) -> tuple[KnowledgeDocument, ...]:
        control_root = self.control_root.resolve()
        candidates: list[tuple[Path, Path, DocumentType, int, str, str]] = [
            (
                control_root / "AGENTS.md",
                control_root,
                DocumentType.AGENTS,
                85,
                "Harness control-plane instructions",
                "control-plane/AGENTS.md",
            ),
            (
                control_root / ".agent-policy.yaml",
                control_root,
                DocumentType.POLICY,
                100,
                "Harness autonomy policy",
                "control-plane/.agent-policy.yaml",
            ),
            (
                control_root / ".agent-tool-policy.yaml",
                control_root,
                DocumentType.POLICY,
                88,
                "Harness tool policy",
                "control-plane/.agent-tool-policy.yaml",
            ),
        ]
        repository = request.repository.resolve()
        if repository != control_root:
            candidates.append(
                (
                    repository / "AGENTS.md",
                    repository,
                    DocumentType.AGENTS,
                    90,
                    "Target repository instructions",
                    "repository/AGENTS.md",
                )
            )
        if request.project_profile != "agent_workspace" and request.project:
            projects = control_root / "docs" / "projects"
            privacy = projects / request.project / "privacy.md"
            if _within(privacy, projects):
                candidates.append(
                    (
                        privacy,
                        projects,
                        DocumentType.POLICY,
                        82,
                        "Target project privacy policy",
                        f"control-plane/projects/{request.project}/privacy.md",
                    )
                )
        documents: list[KnowledgeDocument] = []
        seen: set[Path] = set()
        for path, root, document_type, priority, title, display_path in candidates:
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            document = _file_document(
                source=self.name,
                path=path,
                root=root,
                knowledge_type=KnowledgeType.POLICY,
                document_type=document_type,
                priority=priority,
                title=title,
                display_path=display_path,
                metadata={
                    "scope": "target_repository"
                    if display_path.startswith("repository/")
                    else "control_plane"
                },
            )
            if document is not None:
                documents.append(document)
        return tuple(documents)


@dataclass(frozen=True)
class ProjectProfileSource:
    control_root: Path
    name: str = "project_profile"

    def collect(self, request: KnowledgeRequest) -> tuple[KnowledgeDocument, ...]:
        control_root = self.control_root.resolve()
        documents: list[KnowledgeDocument] = []
        for path, root in (
            (request.repository.resolve() / ".agent" / "project.yaml", request.repository.resolve()),
            (control_root / ".agent-project-profiles.yaml", control_root),
        ):
            document = _file_document(
                source=self.name,
                path=path,
                root=root,
                knowledge_type=KnowledgeType.PROJECT_PROFILE,
                document_type=DocumentType.PROJECT_PROFILE,
                priority=90,
            )
            if document is not None:
                documents.append(document)
        identity = json.dumps(
            {
                "project": request.project,
                "project_profile": request.project_profile,
                "repository": str(request.repository.resolve()),
            },
            indent=2,
            ensure_ascii=False,
        )
        documents.append(
            KnowledgeDocument(
                id=_document_id(self.name, "resolved", request.project_profile),
                title="Resolved project identity",
                content=identity,
                source=self.name,
                path="resolved",
                knowledge_type=KnowledgeType.PROJECT_PROFILE,
                document_type=DocumentType.PROJECT_PROFILE,
                priority=95,
                trust=TrustStatus.TRUSTED,
            )
        )
        return tuple(documents)


@dataclass(frozen=True)
class SkillSource:
    control_root: Path
    name: str = "skills"

    def collect(self, request: KnowledgeRequest) -> tuple[KnowledgeDocument, ...]:
        skills_root = self.control_root.resolve() / ".agents" / "skills"
        role_skills = set(ROLE_SKILLS.get(request.role, ()))
        documents: list[KnowledgeDocument] = []
        if not skills_root.is_dir() or skills_root.is_symlink():
            return ()
        for path in sorted(skills_root.glob("*/SKILL.md")):
            skill_name = path.parent.name
            if request.project_profile == "nextjs_web" and skill_name == "python-standards":
                continue
            document = _file_document(
                source=self.name,
                path=path,
                root=self.control_root.resolve(),
                knowledge_type=KnowledgeType.SKILL,
                document_type=DocumentType.SKILL,
                priority=58 if skill_name in role_skills else 48,
                title=skill_name,
                metadata={"role_match": str(skill_name in role_skills).lower()},
            )
            if document is not None:
                documents.append(document)
        return tuple(documents)


@dataclass(frozen=True)
class ContractSource:
    control_root: Path
    name: str = "contracts"

    def _role_excerpt(self, path: Path, role: str) -> str | None:
        text = _safe_text(path, self.control_root.resolve())
        if text is None:
            return None
        try:
            document = yaml.safe_load(text)
        except yaml.YAMLError:
            return text
        if not isinstance(document, dict):
            return text
        roles = document.get("roles")
        excerpt: dict[str, object] = {"version": document.get("version", 1)}
        if isinstance(document.get("default"), dict):
            excerpt["default"] = document["default"]
        if isinstance(roles, dict) and role in roles:
            excerpt["roles"] = {role: roles[role]}
        elif path.name == ".agent-artifact-owners.yaml":
            excerpt = document
        return yaml.safe_dump(excerpt, sort_keys=False, allow_unicode=True)

    def collect(self, request: KnowledgeRequest) -> tuple[KnowledgeDocument, ...]:
        root = self.control_root.resolve()
        paths = (
            root / ".agent-role-contracts.yaml",
            root / ".agent-role-capabilities.yaml",
            root / ".agent-artifact-owners.yaml",
        )
        documents: list[KnowledgeDocument] = []
        for path in paths:
            content = self._role_excerpt(path, request.role)
            if not content:
                continue
            display = _relative_or_absolute(path, root)
            documents.append(
                KnowledgeDocument(
                    id=_document_id(self.name, display, request.role),
                    title=f"{path.name}: {request.role}",
                    content=content,
                    source=self.name,
                    path=display,
                    knowledge_type=KnowledgeType.CONTRACT,
                    document_type=DocumentType.CONTRACT,
                    priority=78,
                    metadata={"role": request.role},
                    trust=TrustStatus.TRUSTED,
                )
            )
        return tuple(documents)


@dataclass(frozen=True)
class PrivateProjectKnowledgeSource:
    control_root: Path
    name: str = "private_project_knowledge"

    def collect(self, request: KnowledgeRequest) -> tuple[KnowledgeDocument, ...]:
        if request.project_profile == "agent_workspace" or not request.project:
            return ()
        projects_root = self.control_root.resolve() / "docs" / "projects"
        project_root = (projects_root / request.project).resolve()
        if not _within(project_root, projects_root) or not (project_root / "privacy.md").is_file():
            return ()
        documents: list[KnowledgeDocument] = []
        for path, root in _iter_bounded_files((project_root / "wiki", project_root / "graph")):
            document_type = DocumentType.ADR if "decisions" in path.parts else DocumentType.WIKI
            document = _file_document(
                source=self.name,
                path=path,
                root=projects_root,
                knowledge_type=KnowledgeType.DOCUMENTATION,
                document_type=document_type,
                priority=70 if document_type == DocumentType.ADR else 55,
            )
            if document is not None:
                documents.append(document)
        return tuple(documents)


@dataclass(frozen=True)
class ArtifactSource:
    artifacts_dir: Path
    name: str = "run_artifacts"

    def collect(self, request: KnowledgeRequest) -> tuple[KnowledgeDocument, ...]:
        root = self.artifacts_dir.resolve()
        documents: list[KnowledgeDocument] = []
        if not root.is_dir() or root.is_symlink():
            return ()
        allowed = ROLE_ARTIFACTS.get(request.role)
        for path, resolved_root in _iter_bounded_files((root,), max_files=60):
            if allowed is not None and path.name not in allowed:
                continue
            document = _file_document(
                source=self.name,
                path=path,
                root=resolved_root,
                knowledge_type=KnowledgeType.ARTIFACT,
                document_type=DocumentType.ARTIFACT,
                priority=88,
                metadata={
                    "layer": "role",
                    "authoritative": "true",
                    "created_at": datetime.fromtimestamp(
                        path.stat().st_mtime,
                        tz=timezone.utc,
                    ).isoformat(),
                },
            )
            if document is not None:
                documents.append(document)
        execution_plan = root.parent / "execution-plan.json"
        plan_document = _file_document(
            source=self.name,
            path=execution_plan,
            root=root.parent,
            knowledge_type=KnowledgeType.ARTIFACT,
            document_type=DocumentType.ARTIFACT,
            priority=96,
            title="Authoritative execution plan",
            metadata={"layer": "base", "authoritative": "true"},
        )
        if plan_document is not None:
            documents.append(plan_document)
        return tuple(documents)


@dataclass(frozen=True)
class RuntimeDeltaSource:
    """Authoritative changes since the preceding role checkpoint."""

    content: str
    name: str = "runtime_delta"

    def collect(self, request: KnowledgeRequest) -> tuple[KnowledgeDocument, ...]:
        if not self.content.strip():
            return ()
        return (
            KnowledgeDocument(
                id=_document_id(self.name, "current", request.role),
                title="Changes since previous role",
                content=self.content,
                source=self.name,
                path="current",
                knowledge_type=KnowledgeType.ARTIFACT,
                document_type=DocumentType.ARTIFACT,
                priority=99,
                metadata={"layer": "delta", "authoritative": "true"},
                trust=TrustStatus.TRUSTED,
            ),
        )


@dataclass(frozen=True)
class ObsidianSource:
    vaults: tuple[Path, ...]
    name: str = "obsidian"
    max_files: int = DEFAULT_MAX_FILES

    @classmethod
    def discover(
        cls,
        repository: Path,
        explicit_vaults: Sequence[Path] = (),
    ) -> "ObsidianSource":
        roots = [Path(path).expanduser() for path in explicit_vaults]
        configured = os.environ.get("AI_HARNESS_OBSIDIAN_VAULTS", "").strip()
        if configured:
            roots.extend(Path(value).expanduser() for value in configured.split(os.pathsep) if value.strip())
        if (repository.resolve() / ".obsidian").is_dir():
            roots.append(repository.resolve())
        unique: list[Path] = []
        seen: set[Path] = set()
        for root in roots:
            resolved = root.resolve()
            if resolved in seen or root.is_symlink() or not (resolved / ".obsidian").is_dir():
                continue
            seen.add(resolved)
            unique.append(resolved)
        return cls(tuple(unique))

    def collect(self, request: KnowledgeRequest) -> tuple[KnowledgeDocument, ...]:
        documents: list[KnowledgeDocument] = []
        for path, vault in _iter_bounded_files(self.vaults, suffixes={".md"}, max_files=self.max_files):
            document = _file_document(
                source=self.name,
                path=path,
                root=vault,
                knowledge_type=KnowledgeType.DOCUMENTATION,
                document_type=DocumentType.OBSIDIAN,
                priority=45,
                metadata={"vault": str(vault)},
            )
            if document is not None:
                documents.append(document)
        return tuple(documents)
