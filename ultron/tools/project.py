"""Read-only project inspection tool (Phase 5).

Lets ULTRON inspect an authorized local software repository without modifying files,
installing packages, or executing repository code.

Security invariants:
* Confined to the canonical ``root`` directory (:func:`resolve_project_root` /
  :func:`resolve_confined_path`); rejects ``..`` traversal, unauthorized absolute
  paths, ``~`` expansion, and symbolic links.
* Never reads ``.env*``, tokens, credentials, private keys, or other sensitive files
  (:func:`is_sensitive_path`); sensitive files are recorded only as skipped paths.
* Bounds directory walk depth, total entries, per-file byte size, excerpt length,
  and wall-clock duration.
* Queries Git status via :func:`ultron.tools.process.run_command` with an absolute
  executable path, ``--no-optional-locks``, ``shell=False``, and a short timeout.
* Declares ``external_content=True`` so repository text is treated as untrusted data
  by the agent runtime.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import tomllib

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

        try:
            target = resolve_confined_path(canonical_root, raw_path)
        except PathPolicyError as exc:
            reason = _classify_path_error(exc)
            return ToolResult(False, f"Cannot inspect '{redact(raw_path)}': {exc}.", {"reason": reason})

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
                        # Check whether deeper entries exist to report truncation accurately
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
            if lang:
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
