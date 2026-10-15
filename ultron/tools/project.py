"""Read-only project inspection, code explanation, and repository search tools (Phases 5–6).

Lets ULTRON inspect, explain, and search an authorized local software repository without
modifying files, installing packages, accessing the network, or executing repository code.

Security invariants:
* Confined to the canonical ``root`` directory (:func:`resolve_project_root` /
  :func:`resolve_confined_path`); rejects ``..`` traversal, unauthorized absolute
  paths, ``~`` expansion, and symbolic links.
* Never reads ``.env*``, tokens, credentials, private keys, databases, or other sensitive
  files (:func:`is_sensitive_path`); sensitive files are rejected or recorded only as
  skipped paths.
* Directly targeting a directory the walks ignore (``IGNORED_DIRS``: VCS metadata such as
  ``.git``, virtual environments, caches, build output) is refused with reason ``ignored``,
  so a ``path`` argument cannot bypass the walk policy.
* Bounds directory walk depth, total files scanned, total bytes read, per-file byte size,
  AST recursion, excerpt length, result count, and wall-clock duration.
* Queries Git status via :func:`ultron.tools.process.run_command` with an absolute
  executable path, ``--no-optional-locks``, ``shell=False``, and a short timeout.
* Declares ``external_content=True`` on all three tools so repository text is treated as
  untrusted data by the agent runtime and cannot authorize state-changing actions.
"""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 fallback (tomli in uv.lock)
    import tomli as tomllib  # type: ignore[no-redef]

from ultron.security.policy import RiskTier
from ultron.security.redact import redact
from ultron.tools.base import ToolResult, ToolSpec
from ultron.tools.paths import (
    IGNORED_DIRS,
    MAX_RELATIVE_PATH_CHARS,
    PathPolicyError,
    is_sensitive_path,
    read_confined_text,
    resolve_confined_path,
    resolve_project_root,
)
from ultron.tools.process import Runner, run_command

__all__ = [
    "GIT_CANDIDATES",
    "PROJECT_EXPLAIN_SCHEMA",
    "PROJECT_INSPECT_SCHEMA",
    "PROJECT_SEARCH_SCHEMA",
    "ProjectExplainTool",
    "ProjectInspectTool",
    "ProjectSearchTool",
]

GIT_CANDIDATES: tuple[str, ...] = ("/usr/bin/git", "/opt/homebrew/bin/git", "/usr/local/bin/git")

PROJECT_INSPECT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "path": {
            "type": "string",
            "minLength": 1,
            "maxLength": MAX_RELATIVE_PATH_CHARS,
            "description": (
                "Optional relative subdirectory inside the authorized project root "
                "(omit or use '.' for the project root)."
            ),
        },
    },
}

PROJECT_EXPLAIN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["path"],
    "properties": {
        "path": {
            "type": "string",
            "minLength": 1,
            "maxLength": MAX_RELATIVE_PATH_CHARS,
            "description": (
                "Relative file path inside the authorized project root to explain "
                "(e.g. 'main.py' or 'ultron/tools/registry.py')."
            ),
        },
        "symbol": {
            "type": "string",
            "minLength": 1,
            "maxLength": 120,
            "description": (
                "Optional function, class, or method name inside the file "
                "(e.g. 'ToolRegistry', 'ToolRegistry.request', or 'build_tool_registry')."
            ),
        },
    },
}

PROJECT_SEARCH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["query"],
    "properties": {
        "query": {
            "type": "string",
            "minLength": 1,
            "maxLength": 160,
            "description": (
                "Non-empty search query (literal text, function/class name, "
                "configuration key, or import reference)."
            ),
        },
        "path": {
            "type": "string",
            "minLength": 1,
            "maxLength": MAX_RELATIVE_PATH_CHARS,
            "description": (
                "Optional relative directory or file path inside the authorized "
                "project root (default '.')."
            ),
        },
        "mode": {
            "type": "string",
            "minLength": 1,
            "maxLength": 16,
            "enum": ["auto", "literal", "symbol", "definition", "import"],
            "description": "Search mode: 'auto' (default), 'literal', 'symbol', 'definition', or 'import'.",
        },
        "extension": {
            "type": "string",
            "minLength": 1,
            "maxLength": 24,
            "description": "Optional file extension filter such as '.py', 'py', '.md', or '.toml'.",
        },
        "case_sensitive": {
            "type": "boolean",
            "description": "Whether matching is case-sensitive (default false).",
        },
        "max_results": {
            "type": "integer",
            "minimum": 1,
            "maximum": 50,
            "description": "Maximum number of matches to return (1-50, default 20).",
        },
    },
}

_DOC_CANDIDATES: tuple[str, ...] = (
    "README.md",
    "README.rst",
    "README.txt",
    "README",
    "AGENTS.md",
    "SECURITY.md",
    "CONTRIBUTING.md",
    "prd.md",
    "architecture.md",
    "rules.md",
    "phases.md",
    "design.md",
    "memory.md",
)

_ENTRY_CANDIDATES: tuple[str, ...] = (
    "main.py",
    "server.py",
    "app.py",
    "manage.py",
    "ultron/__main__.py",
    "ultron/cli.py",
    "src/main.py",
    "src/index.ts",
    "src/index.js",
    "src/main.rs",
    "main.go",
)

_EXT_LANGUAGE_MAP: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".rs": "rust",
    ".go": "go",
    ".java": "java",
    ".c": "c",
    ".cpp": "cpp",
    ".rb": "ruby",
    ".swift": "swift",
    ".md": "markdown",
    ".toml": "toml",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".html": "html",
    ".css": "css",
    ".sh": "shell",
}

_KNOWN_FRAMEWORKS: tuple[tuple[str, str], ...] = (
    ("flask", "flask"),
    ("fastapi", "fastapi"),
    ("django", "django"),
    ("pydantic", "pydantic"),
    ("openai", "openai"),
    ("pytest", "pytest"),
    ("ruff", "ruff"),
    ("react", "react"),
    ("next", "nextjs"),
    ("vite", "vite"),
    ("express", "express"),
    ("tailwindcss", "tailwindcss"),
    ("tokio", "tokio"),
    ("axum", "axum"),
)

_SEARCH_MODES: frozenset[str] = frozenset({"auto", "literal", "symbol", "definition", "import"})
_CONFIG_EXTS: frozenset[str] = frozenset({".toml", ".json", ".yaml", ".yml", ".ini", ".cfg"})
_EXTENSION_RE = re.compile(r"^\.?[A-Za-z0-9_+-]{1,20}$")


def _clean_excerpt(text: str, limit: int) -> str:
    lines = []
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            if lines:
                break
            continue
        lines.append(stripped)
        if sum(len(item) for item in lines) >= limit:
            break
    joined = " ".join(lines).strip()
    joined = redact(joined)
    if len(joined) <= limit:
        return joined
    return joined[: max(limit - 1, 0)].rstrip() + "…"


def _truncate_redacted(text: str, limit: int) -> str:
    cleaned = redact(text.strip())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(limit - 1, 0)].rstrip() + "…"


def _dep_name(raw_spec: str) -> str:
    match = re.match(r"^\s*([A-Za-z0-9_.-]+)", raw_spec)
    return match.group(1).lower().replace("_", "-") if match else ""


def _classify_path_error(exc: PathPolicyError) -> str:
    msg = str(exc).lower()
    if "traversal" in msg:
        return "path_traversal"
    if "symbolic link" in msg or "symlink" in msg:
        return "symlink_escape"
    if "relative" in msg or "outside" in msg:
        return "path_outside_root"
    if "does not exist" in msg:
        return "not_found"
    return "invalid_path"


def _precheck_relative_syntax(raw_path: str) -> str | None:
    """Return a rejection reason if ``raw_path`` is syntactically invalid or outside root."""
    if len(raw_path) > MAX_RELATIVE_PATH_CHARS:
        return "invalid_path"
    if any("\x00" in raw_path or ord(ch) < 32 or ord(ch) == 127 for ch in raw_path):
        return "invalid_path"
    stripped = raw_path.strip()
    if not stripped:
        return "invalid_path"
    if (
        stripped.startswith("~")
        or os.path.isabs(stripped)
        or Path(stripped).is_absolute()
        or "\\" in stripped
        or (len(stripped) >= 2 and stripped[0].isalpha() and stripped[1] == ":")
    ):
        return "path_outside_root"
    if ".." in Path(stripped).parts:
        return "path_traversal"
    return None


def _resolve_tool_target(
    canonical_root: Path,
    raw_path: str,
) -> tuple[Path | None, str | None, str | None]:
    """Validate and resolve ``raw_path`` inside ``canonical_root`` with consistent policy."""
    pre_err = _precheck_relative_syntax(raw_path)
    if pre_err is not None:
        return None, pre_err, "path is not allowed"
    if raw_path.strip() != "." and is_sensitive_path(raw_path):
        return None, "sensitive", "sensitive path is not permitted"
    try:
        target = resolve_confined_path(canonical_root, raw_path)
    except PathPolicyError as exc:
        return None, _classify_path_error(exc), str(exc)
    rel = "." if target == canonical_root else str(target.relative_to(canonical_root))
    if rel != "." and is_sensitive_path(rel):
        return None, "sensitive", "sensitive path is not permitted"
    if rel != ".":
        # The structure/search walks never descend into IGNORED_DIRS, and a direct ``path``
        # argument must not bypass that policy. VCS metadata is the security-critical case:
        # .git/config can hold remote URLs with embedded credentials whose password shape
        # redact() does not pattern-match, and .git/logs or COMMIT_EDITMSG expose operation
        # history; none of those names trip the sensitive-name filter above.
        for part in Path(rel).parts:
            if part in IGNORED_DIRS or part.lower() in IGNORED_DIRS:
                return None, "ignored", (
                    "path is inside a directory the project tools ignore "
                    "(VCS metadata, virtual environments, caches or build output)"
                )
    return target, None, None


class ProjectInspectTool:
    """Safely inspect a local project directory within the authorized project root."""

    spec = ToolSpec(
        name="project.inspect",
        description=(
            "Inspect the authorized project directory (structure, docs, stack, entry points, "
            "test/lint commands, and Git status)."
        ),
        risk=RiskTier.OBSERVE,
        side_effects=frozenset(),
        parameters=PROJECT_INSPECT_SCHEMA,
        external_content=True,
    )

    def __init__(
        self,
        root: Path | str | None = None,
        *,
        runner: Runner = subprocess.run,
        git_candidates: Sequence[str] = GIT_CANDIDATES,
        max_depth: int = 2,
        max_entries: int = 60,
        max_file_bytes: int = 65_536,
        max_doc_chars: int = 400,
        max_duration: float = 5.0,
        git_timeout: float = 5.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._root = root
        self._runner = runner
        self._git_candidates = tuple(git_candidates)
        self._max_depth = max(1, max_depth)
        self._max_entries = max(5, max_entries)
        self._max_file_bytes = max(256, max_file_bytes)
        self._max_doc_chars = max(80, max_doc_chars)
        self._max_duration = max(0.2, max_duration)
        self._git_timeout = max(0.5, git_timeout)
        self._clock = clock

    def preview(self, arguments: Mapping[str, Any]) -> str:
        rel = arguments.get("path", ".") if isinstance(arguments, Mapping) else "."
        return f"Inspect project directory '{rel}' (read-only)."

    def run(self, arguments: Mapping[str, Any]) -> ToolResult:
        raw_path = arguments.get("path", ".") if isinstance(arguments, Mapping) else "."
        if raw_path is None:
            raw_path = "."
        if not isinstance(raw_path, str):
            return ToolResult(False, "Project path must be a relative string.", {"reason": "invalid_argument"})

        try:
            canonical_root = resolve_project_root(self._root)
        except (PathPolicyError, OSError) as exc:
            return ToolResult(
                False,
                f"Authorized project root is unavailable: {redact(exc)}.",
                {"reason": "invalid_root"},
            )

        target, err_reason, err_detail = _resolve_tool_target(canonical_root, raw_path)
        if err_reason == "sensitive":
            return ToolResult(
                False,
                f"Refusing to inspect sensitive path '{redact(raw_path)}'.",
                {"reason": "sensitive"},
            )
        if err_reason is not None or target is None:
            return ToolResult(
                False,
                f"Cannot inspect '{redact(raw_path)}': {err_detail}.",
                {"reason": err_reason or "invalid_path"},
            )

        if not target.is_dir():
            return ToolResult(
                False,
                f"Cannot inspect '{redact(raw_path)}': path is not a directory.",
                {"reason": "not_a_directory"},
            )

        deadline = self._clock() + self._max_duration
        rel_target = "." if target == canonical_root else str(target.relative_to(canonical_root))

        structure, skipped, ext_counts = self._scan_structure(canonical_root, target, deadline)
        docs_found, readme_excerpt = self._inspect_docs(canonical_root, target, skipped, deadline)
        stack_info = self._inspect_configs(canonical_root, target, skipped, ext_counts, deadline)
        git_info = self._inspect_git(target)

        project_name = stack_info["project_name"] or target.name
        languages = stack_info["languages"]
        frameworks = stack_info["frameworks"]
        entry_points = stack_info["entry_points"]
        commands = stack_info["commands"]
        config_errors = stack_info["config_errors"]

        data: dict[str, Any] = {
            "root": str(canonical_root),
            "target": rel_target,
            "project_name": project_name,
            "languages": languages,
            "frameworks": frameworks,
            "entry_points": entry_points,
            "commands": commands,
            "docs": docs_found,
            "readme_excerpt": readme_excerpt,
            "structure": structure,
            "git": git_info,
            "skipped": skipped[:25],
            "config_errors": config_errors,
        }

        summary = self._format_summary(
            project_name=project_name,
            languages=languages,
            frameworks=frameworks,
            entry_points=entry_points,
            commands=commands,
            docs_found=docs_found,
            git_info=git_info,
            config_errors=config_errors,
            truncated=structure["truncated"],
        )
        return ToolResult(True, summary, data)

    def _scan_structure(
        self,
        canonical_root: Path,
        target: Path,
        deadline: float,
    ) -> tuple[dict[str, Any], list[dict[str, str]], dict[str, int]]:
        directories: list[str] = []
        files: list[str] = []
        ignored_dirs: list[str] = []
        skipped: list[dict[str, str]] = []
        ext_counts: dict[str, int] = {}
        truncated = False

        queue: deque[tuple[Path, int]] = deque([(target, 0)])
        total_seen = 0

        while queue:
            if self._clock() >= deadline:
                truncated = True
                break
            current_dir, depth = queue.popleft()
            try:
                with os.scandir(current_dir) as it:
                    entries = sorted(it, key=lambda e: e.name.lower())
            except OSError:
                skipped.append({"path": str(current_dir.relative_to(target)), "reason": "unreadable"})
                continue

            for entry in entries:
                if self._clock() >= deadline:
                    truncated = True
                    break
                entry_path = Path(entry.path)
                rel_to_target = str(entry_path.relative_to(target))
                rel_to_root = entry_path.relative_to(canonical_root)

                if entry.is_symlink():
                    skipped.append({"path": rel_to_target, "reason": "symlink"})
                    continue

                try:
                    is_dir = entry.is_dir(follow_symlinks=False)
                except OSError:
                    skipped.append({"path": rel_to_target, "reason": "unreadable"})
                    continue

                if is_dir:
                    if entry.name in IGNORED_DIRS or entry.name.lower() in IGNORED_DIRS:
                        if rel_to_target not in ignored_dirs:
                            ignored_dirs.append(rel_to_target)
                        continue
                    if is_sensitive_path(rel_to_root):
                        skipped.append({"path": rel_to_target, "reason": "sensitive"})
                        continue
                    if total_seen >= self._max_entries:
                        truncated = True
                        continue
                    directories.append(rel_to_target)
                    total_seen += 1
                    if depth + 1 < self._max_depth:
                        queue.append((entry_path, depth + 1))
                    else:
                        try:
                            with os.scandir(entry_path) as sub_it:
                                if any(True for _ in sub_it):
                                    truncated = True
                        except OSError:
                            pass
                    continue

                if is_sensitive_path(rel_to_root):
                    skipped.append({"path": rel_to_target, "reason": "sensitive"})
                    continue

                if total_seen >= self._max_entries:
                    truncated = True
                    continue

                files.append(rel_to_target)
                total_seen += 1
                ext = entry_path.suffix.lower()
                if ext:
                    ext_counts[ext] = ext_counts.get(ext, 0) + 1

        structure = {
            "directories": directories,
            "files": files,
            "ignored_dirs": ignored_dirs[:20],
            "truncated": truncated,
        }
        return structure, skipped, ext_counts

    def _inspect_docs(
        self,
        canonical_root: Path,
        target: Path,
        skipped: list[dict[str, str]],
        deadline: float,
    ) -> tuple[list[str], str]:
        docs_found: list[str] = []
        readme_excerpt = ""

        for name in _DOC_CANDIDATES:
            if self._clock() >= deadline:
                break
            candidate = target / name
            if not os.path.lexists(candidate):
                continue
            rel_name = str(candidate.relative_to(target))
            if candidate.is_symlink():
                self._record_skip(skipped, rel_name, "symlink")
                continue
            if not candidate.is_file():
                continue

            if name.upper().startswith("README") and not readme_excerpt:
                text, err = read_confined_text(canonical_root, candidate, max_bytes=self._max_file_bytes)
                if err is not None:
                    self._record_skip(skipped, rel_name, err)
                    continue
                docs_found.append(rel_name)
                if text:
                    readme_excerpt = _clean_excerpt(text, self._max_doc_chars)
            else:
                try:
                    st = candidate.stat(follow_symlinks=False)
                except OSError:
                    self._record_skip(skipped, rel_name, "unreadable")
                    continue
                if st.st_size > self._max_file_bytes:
                    self._record_skip(skipped, rel_name, "oversized")
                    continue
                docs_found.append(rel_name)

        return docs_found, readme_excerpt

    def _inspect_configs(
        self,
        canonical_root: Path,
        target: Path,
        skipped: list[dict[str, str]],
        ext_counts: Mapping[str, int],
        deadline: float,
    ) -> dict[str, Any]:
        project_name = ""
        languages: list[str] = []
        frameworks: list[str] = []
        entry_points: list[str] = []
        commands: list[dict[str, str]] = []
        config_errors: list[dict[str, str]] = []
        dep_names: set[str] = set()

        has_uv_lock = (target / "uv.lock").is_file() and not (target / "uv.lock").is_symlink()

        # 1. pyproject.toml
        pyproject_path = target / "pyproject.toml"
        if os.path.lexists(pyproject_path) and self._clock() < deadline:
            text, err = read_confined_text(canonical_root, pyproject_path, max_bytes=self._max_file_bytes)
            if err is not None:
                self._record_skip(skipped, "pyproject.toml", err)
            elif text is not None:
                self._add_unique(languages, "python")
                try:
                    parsed = tomllib.loads(text)
                    if isinstance(parsed, dict):
                        proj = parsed.get("project")
                        if isinstance(proj, dict):
                            if isinstance(proj.get("name"), str) and proj["name"].strip():
                                project_name = proj["name"].strip()
                            for dep in proj.get("dependencies") or []:
                                if isinstance(dep, str) and _dep_name(dep):
                                    dep_names.add(_dep_name(dep))
                            opt_deps = proj.get("optional-dependencies")
                            if isinstance(opt_deps, dict):
                                for group_items in opt_deps.values():
                                    if isinstance(group_items, list):
                                        for dep in group_items:
                                            if isinstance(dep, str) and _dep_name(dep):
                                                dep_names.add(_dep_name(dep))
                            scripts = proj.get("scripts")
                            if isinstance(scripts, dict):
                                for script_name, script_target in scripts.items():
                                    if isinstance(script_name, str) and isinstance(script_target, str):
                                        self._add_unique(entry_points, f"{script_name} ({script_target})")
                        tool_cfg = parsed.get("tool")
                        if isinstance(tool_cfg, dict):
                            if "pytest" in tool_cfg:
                                dep_names.add("pytest")
                            if "ruff" in tool_cfg:
                                dep_names.add("ruff")
                except tomllib.TOMLDecodeError:
                    config_errors.append({"file": "pyproject.toml", "error": "invalid TOML"})

        # 2. requirements.txt
        req_path = target / "requirements.txt"
        if os.path.lexists(req_path) and self._clock() < deadline:
            text, err = read_confined_text(canonical_root, req_path, max_bytes=self._max_file_bytes)
            if err is not None:
                self._record_skip(skipped, "requirements.txt", err)
            elif text is not None:
                self._add_unique(languages, "python")
                for line in text.splitlines():
                    clean = line.split("#", 1)[0].strip()
                    if clean and not clean.startswith("-"):
                        name = _dep_name(clean)
                        if name:
                            dep_names.add(name)

        # 3. package.json
        pkg_path = target / "package.json"
        if os.path.lexists(pkg_path) and self._clock() < deadline:
            text, err = read_confined_text(canonical_root, pkg_path, max_bytes=self._max_file_bytes)
            if err is not None:
                self._record_skip(skipped, "package.json", err)
            elif text is not None:
                self._add_unique(languages, "javascript")
                try:
                    pkg = json.loads(text)
                    if isinstance(pkg, dict):
                        if not project_name and isinstance(pkg.get("name"), str) and pkg["name"].strip():
                            project_name = pkg["name"].strip()
                        if isinstance(pkg.get("main"), str) and pkg["main"].strip():
                            self._add_unique(entry_points, pkg["main"].strip())
                        for section in ("dependencies", "devDependencies"):
                            deps = pkg.get(section)
                            if isinstance(deps, dict):
                                for k in deps:
                                    if isinstance(k, str):
                                        dep_names.add(k.lower())
                        if "typescript" in dep_names:
                            self._add_unique(languages, "typescript")
                        scripts = pkg.get("scripts")
                        if isinstance(scripts, dict):
                            if isinstance(scripts.get("test"), str) and scripts["test"].strip():
                                self._add_command(commands, "test", "npm test", "package.json")
                            if isinstance(scripts.get("lint"), str) and scripts["lint"].strip():
                                self._add_command(commands, "lint", "npm run lint", "package.json")
                except ValueError:
                    config_errors.append({"file": "package.json", "error": "invalid JSON"})

        # 4. Cargo.toml
        cargo_path = target / "Cargo.toml"
        if os.path.lexists(cargo_path) and self._clock() < deadline:
            text, err = read_confined_text(canonical_root, cargo_path, max_bytes=self._max_file_bytes)
            if err is not None:
                self._record_skip(skipped, "Cargo.toml", err)
            elif text is not None:
                self._add_unique(languages, "rust")
                try:
                    cargo = tomllib.loads(text)
                    if isinstance(cargo, dict):
                        pkg_sec = cargo.get("package")
                        if not project_name and isinstance(pkg_sec, dict) and isinstance(pkg_sec.get("name"), str):
                            project_name = pkg_sec["name"].strip()
                        deps = cargo.get("dependencies")
                        if isinstance(deps, dict):
                            for k in deps:
                                dep_names.add(str(k).lower())
                    self._add_command(commands, "test", "cargo test", "Cargo.toml")
                except tomllib.TOMLDecodeError:
                    config_errors.append({"file": "Cargo.toml", "error": "invalid TOML"})

        # 5. go.mod
        gomod_path = target / "go.mod"
        if os.path.lexists(gomod_path) and self._clock() < deadline:
            text, err = read_confined_text(canonical_root, gomod_path, max_bytes=self._max_file_bytes)
            if err is not None:
                self._record_skip(skipped, "go.mod", err)
            elif text is not None:
                self._add_unique(languages, "go")
                self._add_command(commands, "test", "go test ./...", "go.mod")

        # Languages from file extensions
        for ext, _count in sorted(ext_counts.items(), key=lambda item: (-item[1], item[0])):
            lang = _EXT_LANGUAGE_MAP.get(ext)
            if lang and lang not in ("markdown", "toml", "json", "yaml"):
                self._add_unique(languages, lang)

        # Frameworks/libraries from dependencies
        for pkg_key, label in _KNOWN_FRAMEWORKS:
            if pkg_key in dep_names:
                self._add_unique(frameworks, label)

        # Existing entry-point files
        for rel_candidate in _ENTRY_CANDIDATES:
            candidate = target / rel_candidate
            if os.path.lexists(candidate) and not candidate.is_symlink() and candidate.is_file():
                self._add_unique(entry_points, rel_candidate)

        # Python test/lint commands from actual project evidence
        has_tests_dir = (target / "tests").is_dir() and not (target / "tests").is_symlink()
        if "pytest" in dep_names or has_tests_dir:
            cmd = "uv run pytest" if has_uv_lock else "pytest"
            src = "pyproject.toml" if (target / "pyproject.toml").is_file() else "tests/"
            self._add_command(commands, "test", cmd, src)
        if "ruff" in dep_names or (target / "ruff.toml").is_file():
            cmd = "uv run ruff check ." if has_uv_lock else "ruff check ."
            src = "pyproject.toml" if (target / "pyproject.toml").is_file() else "ruff.toml"
            self._add_command(commands, "lint", cmd, src)

        return {
            "project_name": project_name,
            "languages": languages,
            "frameworks": frameworks,
            "entry_points": entry_points,
            "commands": commands,
            "config_errors": config_errors,
        }

    def _inspect_git(self, target: Path) -> dict[str, Any]:
        git_bin = next((c for c in self._git_candidates if os.path.isabs(c) and os.path.exists(c)), None)
        if git_bin is None:
            if self._runner is not subprocess.run and self._git_candidates:
                git_bin = self._git_candidates[0]
            else:
                return {"available": False, "reason": "git_not_found"}

        res = run_command(
            [git_bin, "--no-optional-locks", "-C", str(target), "status", "--porcelain=v1", "--branch"],
            runner=self._runner,
            timeout=self._git_timeout,
            max_output=2048,
        )
        if not res.ok:
            return {"available": False, "reason": res.error or "not_a_git_repo"}

        lines = [line for line in res.stdout.splitlines() if line.strip()]
        if not lines:
            return {"available": False, "reason": "empty_status"}

        branch = "unknown"
        start_idx = 0
        if lines[0].startswith("## "):
            header = lines[0][3:].strip()
            branch = header.split("...", 1)[0].strip()
            start_idx = 1

        modified_count = 0
        untracked_count = 0
        changed_files: list[str] = []

        for raw_line in lines[start_idx:]:
            if len(raw_line) < 3:
                continue
            status_code = raw_line[:2]
            file_part = raw_line[3:].strip().strip('"')
            if " -> " in file_part:
                file_part = file_part.split(" -> ", 1)[1].strip().strip('"')
            if status_code == "??":
                untracked_count += 1
            else:
                modified_count += 1
            if file_part and not is_sensitive_path(file_part) and len(changed_files) < 10:
                changed_files.append(file_part)

        return {
            "available": True,
            "branch": redact(branch),
            "clean": (modified_count == 0 and untracked_count == 0),
            "modified_count": modified_count,
            "untracked_count": untracked_count,
            "changed_files": changed_files,
        }

    @staticmethod
    def _record_skip(skipped: list[dict[str, str]], path: str, reason: str) -> None:
        item = {"path": path, "reason": reason}
        if item not in skipped:
            skipped.append(item)

    @staticmethod
    def _add_unique(items: list[str], value: str) -> None:
        if value and value not in items:
            items.append(value)

    @staticmethod
    def _add_command(commands: list[dict[str, str]], kind: str, command: str, source: str) -> None:
        entry = {"kind": kind, "command": command, "source": source}
        if entry not in commands:
            commands.append(entry)

    @staticmethod
    def _format_summary(
        *,
        project_name: str,
        languages: Sequence[str],
        frameworks: Sequence[str],
        entry_points: Sequence[str],
        commands: Sequence[Mapping[str, str]],
        docs_found: Sequence[str],
        git_info: Mapping[str, Any],
        config_errors: Sequence[Mapping[str, str]],
        truncated: bool,
    ) -> str:
        parts = [f"Project '{project_name}'"]
        if languages:
            parts.append(f"languages: {', '.join(languages[:4])}")
        if frameworks:
            parts.append(f"stack: {', '.join(frameworks[:5])}")
        if entry_points:
            parts.append(f"entry points: {', '.join(entry_points[:4])}")
        if commands:
            cmd_list = ", ".join(f"{c['command']} ({c['source']})" for c in commands[:3])
            parts.append(f"checks: {cmd_list}")
        if docs_found:
            parts.append(f"docs: {', '.join(docs_found[:4])}")
        if git_info.get("available"):
            state = "clean" if git_info.get("clean") else (
                f"{git_info.get('modified_count', 0)} modified, {git_info.get('untracked_count', 0)} untracked"
            )
            parts.append(f"git: branch {git_info.get('branch')} ({state})")
        if config_errors:
            err_files = ", ".join(e["file"] for e in config_errors[:3])
            parts.append(f"config warnings: {err_files}")
        if truncated:
            parts.append("directory scan truncated to bounds")
        return redact(". ".join(parts) + ".")


# ============================================================ Phase 6: project.explain
class ProjectExplainTool:
    """Explain source files, classes, functions, parameters, returns, and execution flow."""

    spec = ToolSpec(
        name="project.explain",
        description=(
            "Explain a source file or named symbol (function, class, or method) inside the "
            "authorized project root with line ranges, citations, and observed vs. inferred behavior."
        ),
        risk=RiskTier.OBSERVE,
        side_effects=frozenset(),
        parameters=PROJECT_EXPLAIN_SCHEMA,
        external_content=True,
    )

    def __init__(
        self,
        root: Path | str | None = None,
        *,
        max_file_bytes: int = 65_536,
        max_doc_chars: int = 320,
        max_excerpt_chars: int = 800,
        max_symbols: int = 30,
        max_related_files: int = 8,
        max_ast_nodes: int = 8_000,
        max_duration: float = 5.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._root = root
        self._max_file_bytes = max(256, max_file_bytes)
        self._max_doc_chars = max(60, max_doc_chars)
        self._max_excerpt_chars = max(120, max_excerpt_chars)
        self._max_symbols = max(5, max_symbols)
        self._max_related_files = max(1, max_related_files)
        self._max_ast_nodes = max(100, max_ast_nodes)
        self._max_duration = max(0.2, max_duration)
        self._clock = clock

    def preview(self, arguments: Mapping[str, Any]) -> str:
        path = arguments.get("path", "") if isinstance(arguments, Mapping) else ""
        symbol = arguments.get("symbol") if isinstance(arguments, Mapping) else None
        target = f"{path}:{symbol}" if symbol else str(path)
        return f"Explain project source '{target}' (read-only)."

    def run(
        self,
        arguments: Mapping[str, Any],
        *,
        cancel: threading.Event | None = None,
    ) -> ToolResult:
        if not isinstance(arguments, Mapping):
            return ToolResult(False, "Arguments must be a JSON object.", {"reason": "invalid_argument"})
        unknown = set(arguments) - {"path", "symbol"}
        if unknown:
            return ToolResult(
                False,
                "Unsupported arguments for project.explain.",
                {"reason": "invalid_argument", "unknown": sorted(str(k) for k in unknown)},
            )

        raw_path = arguments.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            return ToolResult(False, "A relative file 'path' is required.", {"reason": "invalid_argument"})

        raw_symbol = arguments.get("symbol")
        symbol: str | None = None
        if raw_symbol is not None:
            if not isinstance(raw_symbol, str) or not raw_symbol.strip() or len(raw_symbol) > 120:
                return ToolResult(
                    False,
                    "Optional 'symbol' must be a non-empty string up to 120 characters.",
                    {"reason": "invalid_argument"},
                )
            if any("\x00" in raw_symbol or ord(ch) < 32 or ord(ch) == 127 for ch in raw_symbol):
                return ToolResult(
                    False,
                    "Symbol contains invalid control characters.",
                    {"reason": "invalid_argument"},
                )
            symbol = raw_symbol.strip()

        if cancel is not None and cancel.is_set():
            return ToolResult(False, "Project explanation was cancelled.", {"reason": "cancelled"})

        deadline = self._clock() + self._max_duration

        try:
            canonical_root = resolve_project_root(self._root)
        except (PathPolicyError, OSError) as exc:
            return ToolResult(
                False,
                f"Authorized project root is unavailable: {redact(exc)}.",
                {"reason": "invalid_root"},
            )

        target, err_reason, err_detail = _resolve_tool_target(canonical_root, raw_path)
        if err_reason == "sensitive":
            return ToolResult(
                False,
                f"Refusing to explain sensitive file '{redact(raw_path)}'.",
                {"reason": "sensitive"},
            )
        if err_reason is not None or target is None:
            return ToolResult(
                False,
                f"Cannot explain '{redact(raw_path)}': {err_detail}.",
                {"reason": err_reason or "invalid_path"},
            )

        rel_path = str(target.relative_to(canonical_root))

        if target.is_dir():
            return ToolResult(
                False,
                f"Cannot explain '{redact(rel_path)}': path is a directory (use project.inspect).",
                {"reason": "is_a_directory"},
            )

        text, read_err = read_confined_text(canonical_root, target, max_bytes=self._max_file_bytes)
        if read_err is not None or text is None:
            return ToolResult(
                False,
                f"Cannot explain '{redact(rel_path)}': file is {read_err or 'unreadable'}.",
                {"reason": read_err or "unreadable", "path": rel_path},
            )

        if (cancel is not None and cancel.is_set()) or self._clock() >= deadline:
            reason = "cancelled" if (cancel is not None and cancel.is_set()) else "timeout"
            return ToolResult(False, f"Project explanation stopped ({reason}).", {"reason": reason})

        ext = target.suffix.lower()
        language = _EXT_LANGUAGE_MAP.get(ext, "text")
        lines = text.splitlines()
        line_count = len(lines)

        if language == "python":
            return self._explain_python(
                canonical_root=canonical_root,
                rel_path=rel_path,
                text=text,
                lines=lines,
                line_count=line_count,
                symbol=symbol,
                deadline=deadline,
                cancel=cancel,
            )

        return self._explain_non_python(
            rel_path=rel_path,
            language=language,
            lines=lines,
            line_count=line_count,
            symbol=symbol,
        )

    def _explain_python(
        self,
        *,
        canonical_root: Path,
        rel_path: str,
        text: str,
        lines: list[str],
        line_count: int,
        symbol: str | None,
        deadline: float,
        cancel: threading.Event | None,
    ) -> ToolResult:
        syntax_error: dict[str, Any] | None = None
        tree: ast.Module | None = None
        try:
            tree = ast.parse(text, filename=rel_path)
        except SyntaxError as exc:
            syntax_error = {
                "line": int(exc.lineno or 1),
                "message": _truncate_redacted(str(exc.msg or "syntax error"), 120),
            }
        except (ValueError, RecursionError):
            syntax_error = {"line": 1, "message": "source exceeded parser recursion or value limits"}

        if tree is None:
            return self._explain_python_fallback(
                rel_path=rel_path,
                lines=lines,
                line_count=line_count,
                symbol=symbol,
                syntax_error=syntax_error,
            )

        # Bound AST traversal on pathological files
        node_count = 0
        for _ in ast.walk(tree):
            node_count += 1
            if node_count > self._max_ast_nodes:
                return ToolResult(
                    False,
                    f"Cannot explain '{rel_path}': AST complexity exceeds {self._max_ast_nodes} nodes.",
                    {"reason": "ast_limit_exceeded", "path": rel_path},
                )

        if (cancel is not None and cancel.is_set()) or self._clock() >= deadline:
            reason = "cancelled" if (cancel is not None and cancel.is_set()) else "timeout"
            return ToolResult(False, f"Project explanation stopped ({reason}).", {"reason": reason})

        module_doc = _clean_excerpt(ast.get_docstring(tree) or "", self._max_doc_chars)
        imports, connected_files = self._extract_imports(canonical_root, rel_path, tree)

        classes: list[dict[str, Any]] = []
        functions: list[dict[str, Any]] = []
        entry_guard: dict[str, Any] | None = None

        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                if len(classes) < self._max_symbols:
                    classes.append(self._analyze_class(node))
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if len(functions) < self._max_symbols:
                    functions.append(self._analyze_callable(node, qualified_name=node.name))
            elif isinstance(node, ast.If) and self._is_main_guard(node):
                entry_guard = {
                    "start_line": int(node.lineno),
                    "end_line": int(getattr(node, "end_lineno", node.lineno)),
                    "calls": self._collect_calls(node)[:10],
                }

        if symbol is not None:
            return self._build_symbol_explanation(
                rel_path=rel_path,
                lines=lines,
                symbol=symbol,
                classes=classes,
                functions=functions,
                imports=imports,
                connected_files=connected_files,
            )

        citations: list[dict[str, Any]] = [
            {"path": rel_path, "start_line": 1, "end_line": max(1, line_count)}
        ]
        for cf in connected_files[:4]:
            citations.append({"path": cf["path"], "start_line": 1, "end_line": 1})

        execution_flow: list[str] = []
        for fn in functions[:6]:
            call_str = f" -> calls {', '.join(fn['calls'][:4])}" if fn["calls"] else ""
            execution_flow.append(f"{rel_path}:{fn['start_line']}-{fn['end_line']} {fn['name']}(){call_str}")
        for cls in classes[:6]:
            m_names = ", ".join(m["name"] for m in cls["methods"][:4])
            suffix = f" (methods: {m_names})" if m_names else ""
            execution_flow.append(f"{rel_path}:{cls['start_line']}-{cls['end_line']} class {cls['name']}{suffix}")
        if entry_guard is not None:
            g_calls = ", ".join(entry_guard["calls"][:4]) or "body"
            execution_flow.append(
                f"{rel_path}:{entry_guard['start_line']}-{entry_guard['end_line']} __main__ guard -> {g_calls}"
            )

        observed = {
            "path": rel_path,
            "language": "python",
            "line_count": line_count,
            "module_docstring": module_doc,
            "imports": imports,
            "connected_files": connected_files,
            "classes": [
                {
                    "name": c["name"],
                    "start_line": c["start_line"],
                    "end_line": c["end_line"],
                    "bases": c["bases"],
                    "docstring": c["docstring"],
                    "methods": [
                        {
                            "name": m["name"],
                            "start_line": m["start_line"],
                            "end_line": m["end_line"],
                            "parameters": m["parameters"],
                            "returns": m["returns"],
                        }
                        for m in c["methods"][:10]
                    ],
                }
                for c in classes
            ],
            "functions": [
                {
                    "name": f["name"],
                    "kind": f["kind"],
                    "start_line": f["start_line"],
                    "end_line": f["end_line"],
                    "parameters": f["parameters"],
                    "returns": f["returns"],
                    "docstring": f["docstring"],
                    "calls": f["calls"],
                    "raises": f["raises"],
                }
                for f in functions
            ],
            "entry_guard": entry_guard,
        }

        inferred: list[str] = []
        if entry_guard is not None:
            inferred.append(
                f"Inferred role: executable script entry point via __main__ guard at "
                f"{rel_path}:{entry_guard['start_line']}."
            )
        elif connected_files:
            targets = ", ".join(c["path"] for c in connected_files[:4])
            inferred.append(f"Inferred connections: integrates with local project modules ({targets}).")

        cls_names = [c["name"] for c in classes[:5]]
        fn_names = [f["name"] for f in functions[:6]]
        summary_parts = [f"Explained '{rel_path}' ({line_count} lines, python)"]
        if module_doc:
            summary_parts.append(f"purpose: {module_doc}")
        if cls_names:
            summary_parts.append(f"classes: {', '.join(cls_names)}")
        if fn_names:
            summary_parts.append(f"functions: {', '.join(fn_names)}")
        if connected_files:
            summary_parts.append(f"connected files: {', '.join(c['path'] for c in connected_files[:4])}")
        summary = _truncate_redacted(". ".join(summary_parts) + ".", 520)

        data = {
            "path": rel_path,
            "language": "python",
            "line_count": line_count,
            "symbol": None,
            "observed": observed,
            "inferred": inferred,
            "execution_flow": execution_flow,
            "citations": citations,
            "syntax_error": None,
        }
        return ToolResult(True, summary, data)

    def _build_symbol_explanation(
        self,
        *,
        rel_path: str,
        lines: list[str],
        symbol: str,
        classes: list[dict[str, Any]],
        functions: list[dict[str, Any]],
        imports: list[dict[str, Any]],
        connected_files: list[dict[str, Any]],
    ) -> ToolResult:
        matched: dict[str, Any] | None = None
        available: list[str] = []

        for fn in functions:
            available.append(fn["name"])
            if fn["name"] == symbol:
                matched = fn

        for cls in classes:
            available.append(cls["name"])
            if cls["name"] == symbol and matched is None:
                matched = cls
            for method in cls["methods"]:
                qname = f"{cls['name']}.{method['name']}"
                available.append(qname)
                if symbol in (qname, method["name"]) and matched is None:
                    matched = method

        if matched is None:
            return ToolResult(
                False,
                f"Symbol '{redact(symbol)}' was not found in '{rel_path}'.",
                {
                    "reason": "symbol_not_found",
                    "path": rel_path,
                    "symbol": redact(symbol),
                    "available_symbols": available[:25],
                },
            )

        start_line = int(matched["start_line"])
        end_line = int(matched["end_line"])
        excerpt = self._numbered_excerpt(lines, start_line, min(end_line, start_line + 24))

        calls = matched.get("calls", [])
        used_connections = [
            cf for cf in connected_files
            if any(imp_name in calls or imp_name in excerpt for imp_name in cf.get("imported_names", []))
        ] or connected_files[:4]

        execution_flow: list[str] = []
        if matched["kind"] == "class":
            for m in matched.get("methods", [])[:8]:
                m_calls = f" -> calls {', '.join(m['calls'][:4])}" if m.get("calls") else ""
                execution_flow.append(
                    f"{rel_path}:{m['start_line']}-{m['end_line']} {matched['name']}.{m['name']}(){m_calls}"
                )
        else:
            params_desc = ", ".join(p["name"] for p in matched.get("parameters", []))
            execution_flow.append(
                f"{rel_path}:{start_line}-{end_line} {matched['qualified_name']}({params_desc})"
            )
            if calls:
                execution_flow.append(f"Observed calls in body: {', '.join(calls[:8])}")
            if matched.get("raises"):
                execution_flow.append(f"Observed exceptions raised: {', '.join(matched['raises'])}")
            ret = matched.get("returns", {})
            if ret.get("expressions"):
                execution_flow.append(f"Observed return expressions: {', '.join(ret['expressions'][:4])}")

        citations = [{"path": rel_path, "start_line": start_line, "end_line": end_line}]
        for cf in used_connections[:4]:
            citations.append({"path": cf["path"], "start_line": cf["line"], "end_line": cf["line"]})

        observed = {
            "path": rel_path,
            "symbol": matched["qualified_name"],
            "kind": matched["kind"],
            "start_line": start_line,
            "end_line": end_line,
            "docstring": matched.get("docstring", ""),
            "parameters": matched.get("parameters", []),
            "returns": matched.get("returns", {}),
            "calls": calls,
            "raises": matched.get("raises", []),
            "bases": matched.get("bases", []),
            "methods": [
                {
                    "name": m["name"],
                    "start_line": m["start_line"],
                    "end_line": m["end_line"],
                    "parameters": m["parameters"],
                    "returns": m["returns"],
                }
                for m in matched.get("methods", [])[:12]
            ],
            "connected_files": used_connections,
            "imports": imports[:12],
            "source_excerpt": excerpt,
        }

        inferred: list[str] = []
        if calls:
            inferred.append(
                f"Inferred behavior from AST calls: coordinates {', '.join(calls[:5])} "
                f"(verify against {rel_path}:{start_line}-{end_line})."
            )

        param_names = [p["name"] for p in matched.get("parameters", [])]
        ret_info = matched.get("returns", {})
        ret_label = ret_info.get("annotation") or (
            ", ".join(ret_info.get("expressions", [])[:2]) if ret_info.get("expressions") else "unspecified"
        )
        summary_parts = [
            f"Explained {matched['kind']} '{matched['qualified_name']}' at {rel_path}:{start_line}-{end_line}"
        ]
        if matched.get("docstring"):
            summary_parts.append(f"doc: {matched['docstring']}")
        if matched["kind"] != "class":
            summary_parts.append(f"params: ({', '.join(param_names)})")
            summary_parts.append(f"returns: {ret_label}")
        if calls:
            summary_parts.append(f"calls: {', '.join(calls[:5])}")
        summary = _truncate_redacted(". ".join(summary_parts) + ".", 520)

        data = {
            "path": rel_path,
            "language": "python",
            "line_count": len(lines),
            "symbol": matched["qualified_name"],
            "observed": observed,
            "inferred": inferred,
            "execution_flow": execution_flow,
            "citations": citations,
            "syntax_error": None,
        }
        return ToolResult(True, summary, data)

    def _explain_python_fallback(
        self,
        *,
        rel_path: str,
        lines: list[str],
        line_count: int,
        symbol: str | None,
        syntax_error: dict[str, Any] | None,
    ) -> ToolResult:
        found_defs: list[dict[str, Any]] = []
        for idx, raw_line in enumerate(lines, start=1):
            m = re.match(r"^\s*(?:async\s+def|def|class)\s+([A-Za-z_][A-Za-z0-9_]*)", raw_line)
            if m and len(found_defs) < self._max_symbols:
                found_defs.append({"name": m.group(1), "line": idx, "excerpt": _truncate_redacted(raw_line, 120)})

        if symbol is not None:
            match = next((d for d in found_defs if d["name"] == symbol), None)
            if match is None:
                return ToolResult(
                    False,
                    f"Symbol '{redact(symbol)}' could not be verified in '{rel_path}' (file has syntax errors).",
                    {
                        "reason": "symbol_not_found",
                        "path": rel_path,
                        "symbol": redact(symbol),
                        "syntax_error": syntax_error,
                        "available_symbols": [d["name"] for d in found_defs],
                    },
                )

        err_desc = (
            f"syntax error at line {syntax_error['line']}: {syntax_error['message']}"
            if syntax_error
            else "parse error"
        )
        summary = _truncate_redacted(
            f"Partial explanation of '{rel_path}' ({err_desc}); found {len(found_defs)} definition line(s).",
            520,
        )
        data = {
            "path": rel_path,
            "language": "python",
            "line_count": line_count,
            "symbol": symbol,
            "partial": True,
            "observed": {
                "path": rel_path,
                "definitions": found_defs,
                "source_excerpt": self._numbered_excerpt(lines, 1, min(line_count, 20)),
            },
            "inferred": [],
            "execution_flow": [],
            "citations": [{"path": rel_path, "start_line": 1, "end_line": max(1, line_count)}],
            "syntax_error": syntax_error,
        }
        return ToolResult(True, summary, data)

    def _explain_non_python(
        self,
        *,
        rel_path: str,
        language: str,
        lines: list[str],
        line_count: int,
        symbol: str | None,
    ) -> ToolResult:
        definitions: list[dict[str, Any]] = []
        imports: list[dict[str, Any]] = []
        def_re = re.compile(
            r"^\s*(?:export\s+)?(?:async\s+)?(?:function|class|const|let|fn| pub\s+fn|func|type|struct|interface)\s+"
            r"([A-Za-z_][A-Za-z0-9_]*)"
        )
        heading_re = re.compile(r"^\s*(#{1,4})\s+(.+?)\s*$")
        import_re = re.compile(r"^\s*(?:import\s+.+|const\s+.+=\s*require\(.+\)|use\s+.+)")

        for idx, raw_line in enumerate(lines, start=1):
            m_def = def_re.match(raw_line)
            if m_def and len(definitions) < self._max_symbols:
                definitions.append(
                    {"name": m_def.group(1), "line": idx, "excerpt": _truncate_redacted(raw_line, 120)}
                )
                continue
            if language == "markdown":
                m_head = heading_re.match(raw_line)
                if m_head and len(definitions) < self._max_symbols:
                    definitions.append(
                        {"name": m_head.group(2), "line": idx, "excerpt": _truncate_redacted(raw_line, 120)}
                    )
            if import_re.match(raw_line) and len(imports) < 15:
                imports.append({"line": idx, "statement": _truncate_redacted(raw_line, 120)})

        if symbol is not None:
            matched = next((d for d in definitions if d["name"] == symbol), None)
            if matched is None:
                return ToolResult(
                    False,
                    f"Symbol '{redact(symbol)}' was not found in '{rel_path}' ({language}).",
                    {
                        "reason": "symbol_not_found",
                        "path": rel_path,
                        "language": language,
                        "symbol": redact(symbol),
                        "available_symbols": [d["name"] for d in definitions[:20]],
                    },
                )
            start_line = int(matched["line"])
            end_line = min(line_count, start_line + 18)
            excerpt = self._numbered_excerpt(lines, start_line, end_line)
            data = {
                "path": rel_path,
                "language": language,
                "line_count": line_count,
                "symbol": symbol,
                "observed": {
                    "path": rel_path,
                    "symbol": symbol,
                    "start_line": start_line,
                    "end_line": end_line,
                    "definition": matched,
                    "imports": imports,
                    "source_excerpt": excerpt,
                },
                "inferred": [
                    f"Inferred from line-based scan ({language}); full AST analysis is Python-only."
                ],
                "execution_flow": [f"{rel_path}:{start_line} {matched['excerpt']}"],
                "citations": [{"path": rel_path, "start_line": start_line, "end_line": end_line}],
                "syntax_error": None,
            }
            return ToolResult(
                True,
                f"Explained symbol '{redact(symbol)}' in '{rel_path}' at line {start_line} ({language}).",
                data,
            )

        excerpt = self._numbered_excerpt(lines, 1, min(line_count, 20))
        names = ", ".join(d["name"] for d in definitions[:6])
        summary = f"Explained '{rel_path}' ({line_count} lines, {language})"
        if names:
            summary += f". Observed definitions/sections: {names}"
        summary = _truncate_redacted(summary + ".", 520)

        data = {
            "path": rel_path,
            "language": language,
            "line_count": line_count,
            "symbol": None,
            "observed": {
                "path": rel_path,
                "language": language,
                "line_count": line_count,
                "definitions": definitions,
                "imports": imports,
                "source_excerpt": excerpt,
            },
            "inferred": [],
            "execution_flow": [f"{rel_path}:{d['line']} {d['name']}" for d in definitions[:8]],
            "citations": [{"path": rel_path, "start_line": 1, "end_line": max(1, line_count)}],
            "syntax_error": None,
        }
        return ToolResult(True, summary, data)

    def _extract_imports(
        self,
        canonical_root: Path,
        rel_path: str,
        tree: ast.Module,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        imports: list[dict[str, Any]] = []
        connected_files: list[dict[str, Any]] = []
        seen_connected: set[str] = set()
        current_dir = (canonical_root / rel_path).parent

        for node in tree.body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    mod = alias.name
                    resolved = self._resolve_local_module(canonical_root, current_dir, mod)
                    imports.append(
                        {
                            "module": mod,
                            "names": [alias.asname or alias.name],
                            "line": int(node.lineno),
                            "resolved_path": resolved,
                        }
                    )
                    if resolved and resolved not in seen_connected and len(connected_files) < self._max_related_files:
                        seen_connected.add(resolved)
                        connected_files.append(
                            {
                                "path": resolved,
                                "module": mod,
                                "imported_names": [alias.asname or alias.name.split(".")[0]],
                                "line": int(node.lineno),
                            }
                        )
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                names = [alias.name for alias in node.names]
                resolved = self._resolve_local_module(canonical_root, current_dir, mod, level=node.level)
                imports.append(
                    {
                        "module": ("." * node.level) + mod,
                        "names": names[:12],
                        "line": int(node.lineno),
                        "resolved_path": resolved,
                    }
                )
                if resolved and resolved not in seen_connected and len(connected_files) < self._max_related_files:
                    seen_connected.add(resolved)
                    connected_files.append(
                        {
                            "path": resolved,
                            "module": ("." * node.level) + mod,
                            "imported_names": names[:12],
                            "line": int(node.lineno),
                        }
                    )

        return imports[:25], connected_files

    @staticmethod
    def _resolve_local_module(
        canonical_root: Path,
        current_dir: Path,
        module_name: str,
        *,
        level: int = 0,
    ) -> str | None:
        base_dir = canonical_root
        if level > 0:
            base_dir = current_dir
            for _ in range(level - 1):
                base_dir = base_dir.parent
        parts = [p for p in module_name.split(".") if p]
        rel_target = Path(*parts) if parts else Path(".")
        candidates = [
            base_dir / rel_target.with_suffix(".py") if parts else None,
            base_dir / rel_target / "__init__.py",
        ]
        for cand in candidates:
            if cand is None or not os.path.lexists(cand) or cand.is_symlink():
                continue
            try:
                resolved = cand.resolve(strict=True)
                rel = resolved.relative_to(canonical_root)
            except (OSError, ValueError):
                continue
            if not is_sensitive_path(rel) and resolved.is_file():
                return str(rel)
        return None

    def _analyze_class(self, node: ast.ClassDef) -> dict[str, Any]:
        bases = [_truncate_redacted(ast.unparse(b), 50) for b in node.bases[:6]]
        doc = _clean_excerpt(ast.get_docstring(node) or "", self._max_doc_chars)
        methods: list[dict[str, Any]] = []
        for item in node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and len(methods) < self._max_symbols:
                methods.append(self._analyze_callable(item, qualified_name=f"{node.name}.{item.name}", is_method=True))
        return {
            "name": node.name,
            "qualified_name": node.name,
            "kind": "class",
            "start_line": int(node.lineno),
            "end_line": int(getattr(node, "end_lineno", node.lineno)),
            "bases": bases,
            "docstring": doc,
            "methods": methods,
        }

    def _analyze_callable(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        *,
        qualified_name: str,
        is_method: bool = False,
    ) -> dict[str, Any]:
        kind = "method" if is_method else ("async_function" if isinstance(node, ast.AsyncFunctionDef) else "function")
        args_obj = node.args
        params: list[dict[str, Any]] = []

        pos_args = list(args_obj.posonlyargs) + list(args_obj.args)
        num_defaults = len(args_obj.defaults)
        default_offset = len(pos_args) - num_defaults

        for idx, arg in enumerate(pos_args):
            has_def = idx >= default_offset
            def_val = (
                _truncate_redacted(ast.unparse(args_obj.defaults[idx - default_offset]), 40)
                if has_def
                else None
            )
            params.append(
                {
                    "name": arg.arg,
                    "annotation": _truncate_redacted(ast.unparse(arg.annotation), 50) if arg.annotation else None,
                    "has_default": has_def,
                    "default": def_val,
                }
            )

        if args_obj.vararg is not None:
            params.append(
                {
                    "name": f"*{args_obj.vararg.arg}",
                    "annotation": (
                        _truncate_redacted(ast.unparse(args_obj.vararg.annotation), 50)
                        if args_obj.vararg.annotation
                        else None
                    ),
                    "has_default": False,
                    "default": None,
                }
            )

        for kw_arg, kw_def in zip(args_obj.kwonlyargs, args_obj.kw_defaults, strict=False):
            params.append(
                {
                    "name": kw_arg.arg,
                    "annotation": _truncate_redacted(ast.unparse(kw_arg.annotation), 50) if kw_arg.annotation else None,
                    "has_default": kw_def is not None,
                    "default": _truncate_redacted(ast.unparse(kw_def), 40) if kw_def is not None else None,
                }
            )

        if args_obj.kwarg is not None:
            params.append(
                {
                    "name": f"**{args_obj.kwarg.arg}",
                    "annotation": (
                        _truncate_redacted(ast.unparse(args_obj.kwarg.annotation), 50)
                        if args_obj.kwarg.annotation
                        else None
                    ),
                    "has_default": False,
                    "default": None,
                }
            )

        ret_ann = _truncate_redacted(ast.unparse(node.returns), 60) if node.returns else None
        ret_exprs = self._collect_returns(node)
        calls = self._collect_calls(node)
        raises = self._collect_raises(node)
        doc = _clean_excerpt(ast.get_docstring(node) or "", self._max_doc_chars)

        return {
            "name": node.name,
            "qualified_name": qualified_name,
            "kind": kind,
            "start_line": int(node.lineno),
            "end_line": int(getattr(node, "end_lineno", node.lineno)),
            "parameters": params[:15],
            "returns": {"annotation": ret_ann, "expressions": ret_exprs[:5]},
            "docstring": doc,
            "calls": calls[:12],
            "raises": raises[:6],
        }

    @staticmethod
    def _iter_shallow(root_node: ast.AST):
        """Yield AST nodes inside ``root_node`` without descending into nested functions/classes."""
        queue: deque[ast.AST] = deque(ast.iter_child_nodes(root_node))
        while queue:
            cur = queue.popleft()
            if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            yield cur
            queue.extend(ast.iter_child_nodes(cur))

    @classmethod
    def _collect_returns(cls, node: ast.AST) -> list[str]:
        exprs: list[str] = []
        for child in cls._iter_shallow(node):
            if isinstance(child, ast.Return):
                val = "None" if child.value is None else _truncate_redacted(ast.unparse(child.value), 60)
                if val not in exprs:
                    exprs.append(val)
        return exprs

    @classmethod
    def _collect_calls(cls, node: ast.AST) -> list[str]:
        calls: list[str] = []
        for child in cls._iter_shallow(node):
            if isinstance(child, ast.Call):
                name = cls._call_name(child.func)
                if name and name not in calls:
                    calls.append(name)
        return calls

    @classmethod
    def _collect_raises(cls, node: ast.AST) -> list[str]:
        raises: list[str] = []
        for child in cls._iter_shallow(node):
            if isinstance(child, ast.Raise) and child.exc is not None:
                target = child.exc.func if isinstance(child.exc, ast.Call) else child.exc
                label = _truncate_redacted(ast.unparse(target), 50)
                if label and label not in raises:
                    raises.append(label)
        return raises

    @staticmethod
    def _call_name(func_node: ast.AST) -> str:
        if isinstance(func_node, ast.Name):
            return func_node.id
        if isinstance(func_node, ast.Attribute):
            if isinstance(func_node.value, ast.Name):
                return f"{func_node.value.id}.{func_node.attr}"
            return func_node.attr
        return ""

    @staticmethod
    def _is_main_guard(node: ast.If) -> bool:
        try:
            unparsed = ast.unparse(node.test)
        except Exception:
            return False
        return "__name__" in unparsed and "__main__" in unparsed

    def _numbered_excerpt(self, lines: Sequence[str], start_line: int, end_line: int) -> str:
        lo = max(1, start_line)
        hi = min(len(lines), max(lo, end_line))
        snippet = "\n".join(f"{ln}: {lines[ln - 1]}" for ln in range(lo, hi + 1))
        return _truncate_redacted(snippet, self._max_excerpt_chars)


# ============================================================ Phase 6: project.search
class ProjectSearchTool:
    """Search the authorized repository for literal text, symbols, definitions, and imports."""

    spec = ToolSpec(
        name="project.search",
        description=(
            "Search the authorized project root for literal text, function/class symbols, "
            "definitions, or import/configuration references with file and line numbers."
        ),
        risk=RiskTier.OBSERVE,
        side_effects=frozenset(),
        parameters=PROJECT_SEARCH_SCHEMA,
        external_content=True,
    )

    def __init__(
        self,
        root: Path | str | None = None,
        *,
        max_files: int = 200,
        max_total_bytes: int = 2_000_000,
        max_file_bytes: int = 65_536,
        max_results: int = 20,
        max_depth: int = 6,
        max_excerpt_chars: int = 160,
        max_duration: float = 5.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._root = root
        self._max_files = max(1, max_files)
        self._max_total_bytes = max(512, max_total_bytes)
        self._max_file_bytes = max(256, max_file_bytes)
        self._max_results = max(1, min(50, max_results))
        self._max_depth = max(1, max_depth)
        self._max_excerpt_chars = max(60, max_excerpt_chars)
        self._max_duration = max(0.1, max_duration)
        self._clock = clock

    def preview(self, arguments: Mapping[str, Any]) -> str:
        query = arguments.get("query", "") if isinstance(arguments, Mapping) else ""
        return f"Search project for '{redact(query)}' (read-only)."

    def run(
        self,
        arguments: Mapping[str, Any],
        *,
        cancel: threading.Event | None = None,
    ) -> ToolResult:
        if not isinstance(arguments, Mapping):
            return ToolResult(False, "Arguments must be a JSON object.", {"reason": "invalid_argument"})
        unknown = set(arguments) - {"query", "path", "mode", "extension", "case_sensitive", "max_results"}
        if unknown:
            return ToolResult(
                False,
                "Unsupported arguments for project.search.",
                {"reason": "invalid_argument", "unknown": sorted(str(k) for k in unknown)},
            )

        raw_query = arguments.get("query")
        if not isinstance(raw_query, str) or not raw_query.strip() or len(raw_query) > 160:
            return ToolResult(
                False,
                "A non-empty 'query' string (up to 160 characters) is required.",
                {"reason": "invalid_argument"},
            )
        if any("\x00" in raw_query or ord(ch) < 32 or ord(ch) == 127 for ch in raw_query):
            return ToolResult(
                False,
                "Search query contains invalid control characters.",
                {"reason": "invalid_argument"},
            )
        query = raw_query.strip()

        mode = arguments.get("mode", "auto")
        if not isinstance(mode, str) or mode not in _SEARCH_MODES:
            return ToolResult(
                False,
                f"Invalid search mode; must be one of {sorted(_SEARCH_MODES)}.",
                {"reason": "invalid_argument"},
            )

        case_sensitive = arguments.get("case_sensitive", False)
        if not isinstance(case_sensitive, bool):
            return ToolResult(False, "'case_sensitive' must be a boolean.", {"reason": "invalid_argument"})

        req_max = arguments.get("max_results", self._max_results)
        if isinstance(req_max, bool) or not isinstance(req_max, int) or not (1 <= req_max <= 50):
            return ToolResult(
                False,
                "'max_results' must be an integer between 1 and 50.",
                {"reason": "invalid_argument"},
            )
        effective_max_results = min(req_max, self._max_results)

        raw_ext = arguments.get("extension")
        ext_filter: str | None = None
        if raw_ext is not None:
            if not isinstance(raw_ext, str) or not _EXTENSION_RE.fullmatch(raw_ext.strip()):
                return ToolResult(
                    False,
                    "Invalid file extension filter.",
                    {"reason": "invalid_argument"},
                )
            normalized_ext = raw_ext.strip().lower()
            if not normalized_ext.startswith("."):
                normalized_ext = "." + normalized_ext
            if is_sensitive_path(f"sample{normalized_ext}") or normalized_ext == ".env":
                return ToolResult(
                    False,
                    f"Searching sensitive file extension '{normalized_ext}' is not permitted.",
                    {"reason": "sensitive"},
                )
            ext_filter = normalized_ext

        raw_path = arguments.get("path", ".")
        if raw_path is None:
            raw_path = "."
        if not isinstance(raw_path, str):
            return ToolResult(False, "Search 'path' must be a relative string.", {"reason": "invalid_argument"})

        if cancel is not None and cancel.is_set():
            return ToolResult(False, "Project search was cancelled.", {"reason": "cancelled"})

        deadline = self._clock() + self._max_duration

        try:
            canonical_root = resolve_project_root(self._root)
        except (PathPolicyError, OSError) as exc:
            return ToolResult(
                False,
                f"Authorized project root is unavailable: {redact(exc)}.",
                {"reason": "invalid_root"},
            )

        target, err_reason, err_detail = _resolve_tool_target(canonical_root, raw_path)
        if err_reason == "sensitive":
            return ToolResult(
                False,
                f"Refusing to search sensitive path '{redact(raw_path)}'.",
                {"reason": "sensitive"},
            )
        if err_reason is not None or target is None:
            return ToolResult(
                False,
                f"Cannot search '{redact(raw_path)}': {err_detail}.",
                {"reason": err_reason or "invalid_path"},
            )

        rel_target = "." if target == canonical_root else str(target.relative_to(canonical_root))

        candidate_files, skipped, walk_truncated, walk_reason = self._collect_files(
            canonical_root=canonical_root,
            target=target,
            ext_filter=ext_filter,
            deadline=deadline,
            cancel=cancel,
        )

        matches: list[dict[str, Any]] = []
        files_scanned = 0
        bytes_read = 0
        truncated = walk_truncated
        stop_reason = walk_reason or "completed"

        flags = 0 if case_sensitive else re.IGNORECASE
        escaped_q = re.escape(query)
        symbol_re = re.compile(rf"(?<![A-Za-z0-9_]){escaped_q}(?![A-Za-z0-9_])", flags)
        def_re = re.compile(
            rf"^\s*(?:export\s+)?(?:async\s+def|def|class|function|const|let|var|fn|pub\s+fn|func|type|struct)\s+"
            rf"{escaped_q}\b|^\s*{escaped_q}\s*[:=]",
            flags,
        )
        import_re = re.compile(
            rf"^\s*(?:from\s+\S*{escaped_q}\S*\s+import\s+.+|import\s+.*{escaped_q}.*|"
            rf"from\s+\S+\s+import\s+.*{escaped_q}.*|.*require\([^)]*{escaped_q}[^)]*\)|"
            rf"use\s+.*{escaped_q}.*)",
            flags,
        )

        for file_path in candidate_files:
            if cancel is not None and cancel.is_set():
                truncated = True
                stop_reason = "cancelled"
                break
            if self._clock() >= deadline:
                truncated = True
                stop_reason = "timeout"
                break
            if files_scanned >= self._max_files:
                truncated = True
                stop_reason = "max_files_reached"
                break

            rel_file = str(file_path.relative_to(canonical_root))
            text, err = read_confined_text(canonical_root, file_path, max_bytes=self._max_file_bytes)
            if err is not None or text is None:
                skipped.append({"path": rel_file, "reason": err or "unreadable"})
                continue

            file_bytes = len(text.encode("utf-8", errors="replace"))
            if bytes_read + file_bytes > self._max_total_bytes:
                truncated = True
                stop_reason = "max_bytes_reached"
                break

            files_scanned += 1
            bytes_read += file_bytes
            is_config = file_path.suffix.lower() in _CONFIG_EXTS

            for lineno, line in enumerate(text.splitlines(), start=1):
                match_info = self._match_line(
                    line=line,
                    query=query,
                    mode=mode,
                    case_sensitive=case_sensitive,
                    is_config=is_config,
                    symbol_re=symbol_re,
                    def_re=def_re,
                    import_re=import_re,
                )
                if match_info is None:
                    continue
                kind, col = match_info
                if len(matches) >= effective_max_results:
                    truncated = True
                    stop_reason = "max_results_reached"
                    break
                matches.append(
                    {
                        "path": rel_file,
                        "line": lineno,
                        "column": col,
                        "kind": kind,
                        "excerpt": _truncate_redacted(line, self._max_excerpt_chars),
                    }
                )
            if stop_reason == "max_results_reached":
                break

        if mode == "auto":
            priority = {"definition": 0, "import": 1, "symbol": 2, "literal": 3}
            matches.sort(key=lambda item: (priority.get(item["kind"], 9), item["path"], item["line"]))

        data = {
            "root": str(canonical_root),
            "target": rel_target,
            "query": redact(query),
            "mode": mode,
            "extension": ext_filter,
            "matches": matches,
            "count": len(matches),
            "files_scanned": files_scanned,
            "bytes_read": bytes_read,
            "truncated": truncated,
            "stop_reason": stop_reason,
            "skipped": skipped[:25],
        }

        if truncated:
            msg = (
                f"Found {len(matches)} match(es) for '{redact(query)}' across {files_scanned} file(s) "
                f"(partial results: stopped at {stop_reason})."
            )
        elif matches:
            top = ", ".join(f"{m['path']}:{m['line']}" for m in matches[:4])
            msg = f"Found {len(matches)} match(es) for '{redact(query)}' across {files_scanned} file(s): {top}."
        else:
            msg = f"No matches found for '{redact(query)}' across {files_scanned} scanned file(s)."

        return ToolResult(True, redact(msg), data)

    def _collect_files(
        self,
        *,
        canonical_root: Path,
        target: Path,
        ext_filter: str | None,
        deadline: float,
        cancel: threading.Event | None,
    ) -> tuple[list[Path], list[dict[str, str]], bool, str | None]:
        skipped: list[dict[str, str]] = []
        if target.is_file():
            if ext_filter is not None and target.suffix.lower() != ext_filter:
                return [], skipped, False, None
            return [target], skipped, False, None

        files: list[Path] = []
        queue: deque[tuple[Path, int]] = deque([(target, 0)])
        truncated = False
        stop_reason: str | None = None

        while queue:
            if cancel is not None and cancel.is_set():
                return files, skipped, True, "cancelled"
            if self._clock() >= deadline:
                return files, skipped, True, "timeout"

            current_dir, depth = queue.popleft()
            try:
                with os.scandir(current_dir) as it:
                    entries = sorted(it, key=lambda e: e.name.lower())
            except OSError:
                skipped.append({"path": str(current_dir.relative_to(canonical_root)), "reason": "unreadable"})
                continue

            for entry in entries:
                if cancel is not None and cancel.is_set():
                    return files, skipped, True, "cancelled"
                if self._clock() >= deadline:
                    return files, skipped, True, "timeout"

                entry_path = Path(entry.path)
                rel_to_root = entry_path.relative_to(canonical_root)

                if entry.is_symlink():
                    skipped.append({"path": str(rel_to_root), "reason": "symlink"})
                    continue

                try:
                    is_dir = entry.is_dir(follow_symlinks=False)
                except OSError:
                    skipped.append({"path": str(rel_to_root), "reason": "unreadable"})
                    continue

                if is_dir:
                    if entry.name in IGNORED_DIRS or entry.name.lower() in IGNORED_DIRS:
                        continue
                    if is_sensitive_path(rel_to_root):
                        skipped.append({"path": str(rel_to_root), "reason": "sensitive"})
                        continue
                    if depth + 1 < self._max_depth:
                        queue.append((entry_path, depth + 1))
                    else:
                        truncated = True
                        stop_reason = stop_reason or "max_depth_reached"
                    continue

                if is_sensitive_path(rel_to_root):
                    skipped.append({"path": str(rel_to_root), "reason": "sensitive"})
                    continue

                if ext_filter is not None and entry_path.suffix.lower() != ext_filter:
                    continue

                files.append(entry_path)

        return files, skipped, truncated, stop_reason

    @staticmethod
    def _match_line(
        *,
        line: str,
        query: str,
        mode: str,
        case_sensitive: bool,
        is_config: bool,
        symbol_re: re.Pattern[str],
        def_re: re.Pattern[str],
        import_re: re.Pattern[str],
    ) -> tuple[str, int] | None:
        haystack = line if case_sensitive else line.lower()
        needle = query if case_sensitive else query.lower()
        pos = haystack.find(needle)
        if pos < 0:
            return None
        col = pos + 1

        is_def = bool(def_re.search(line))
        is_imp = bool(import_re.search(line)) or (is_config and bool(symbol_re.search(line)))
        is_sym = bool(symbol_re.search(line))

        if mode == "definition":
            return ("definition", col) if is_def else None
        if mode == "import":
            return ("import", col) if is_imp else None
        if mode == "symbol":
            if not is_sym:
                return None
            return ("definition" if is_def else "symbol", col)
        if mode == "literal":
            return ("literal", col)

        # mode == "auto"
        if is_def:
            return ("definition", col)
        if is_imp:
            return ("import", col)
        if is_sym:
            return ("symbol", col)
        return ("literal", col)
