"""Command-line entry point: ``python -m ultron <command>`` (or ``uv run ultron <command>``).

Commands
  serve            start the local HUD/API server (same as ``python server.py``)
  token init       create the API token file (0600) if missing; never prints the token
  token rotate     replace the token (logs out every HUD session)
  token show       print the token (only when you explicitly ask)
  token copy       copy the token to the macOS clipboard
  token path       print where the token file lives
  open-hud         open the HUD with a one-time, short-lived login link
  doctor           report configuration and optional-feature status (no secret values)
  check-secrets    scan files that could be committed for hardcoded credentials
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import platform
import subprocess
import sys
import webbrowser
from pathlib import Path

from ultron import __version__
from ultron.config import (
    ConfigurationError,
    credential_status,
    is_loopback_host,
    load_dotenv_if_enabled,
    load_settings,
)
from ultron.security.secret_scan import scan_repository
from ultron.security.sessions import SessionSigner
from ultron.security.tokens import load_api_token, write_token_file

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _cmd_serve(_args: argparse.Namespace) -> int:
    from ultron.api.app import run

    return run()


def _cmd_token(args: argparse.Namespace) -> int:
    settings = load_settings()
    path = settings.api_token_file
    if args.action == "path":
        print(path)
        return 0
    if args.action in ("init", "rotate"):
        if os.environ.get("ULTRON_API_TOKEN"):
            print("Note: ULTRON_API_TOKEN is set in the environment and takes precedence over the token file.")
        if path.exists() and args.action == "init":
            result = load_api_token(settings, env={})
            if result.problem:
                print(f"Token file exists but is not usable: {result.problem}", file=sys.stderr)
                return 1
            print(f"API token already exists at {path} (not shown). Use `token rotate` to replace it.")
            return 0
        write_token_file(path, overwrite=True)
        print(f"{'Rotated' if args.action == 'rotate' else 'Created'} API token at {path} (permissions 600).")
        if args.action == "rotate":
            print("Restart the server; all existing HUD sessions are now invalid.")
        print("Use `python -m ultron open-hud` to log in, or `python -m ultron token show` to view it.")
        return 0

    result = load_api_token(settings)
    if not result.ok:
        print(result.problem, file=sys.stderr)
        return 1
    if args.action == "show":
        print(result.token)
        return 0
    if args.action == "copy":
        if sys.platform != "darwin":
            print("`token copy` uses pbcopy and is only available on macOS.", file=sys.stderr)
            return 1
        subprocess.run(["/usr/bin/pbcopy"], input=result.token.encode(), check=True)  # noqa: S603
        print("API token copied to the clipboard.")
        return 0
    return 2


def _cmd_open_hud(_args: argparse.Namespace) -> int:
    settings = load_settings()
    result = load_api_token(settings)
    if not result.ok:
        print(result.problem, file=sys.stderr)
        return 1
    ticket = SessionSigner(result.token, settings.session_ttl_seconds).issue_login_ticket()
    url = f"{settings.local_base_url}/login#ticket={ticket}"  # fragment: never sent to the server or its logs
    browser_app = os.environ.get("ULTRON_BROWSER_APP")
    if sys.platform == "darwin" and browser_app:
        subprocess.run(["/usr/bin/open", "-a", browser_app, url], check=False)  # noqa: S603
    elif not webbrowser.open(url):
        print("Could not open a browser. Open /login and paste your token instead.", file=sys.stderr)
        return 1
    print("Opened the HUD with a one-time login link (valid for 2 minutes).")
    return 0


def _feature(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def _cmd_doctor(_args: argparse.Namespace) -> int:
    problems = 0
    print(f"ULTRON/JARVIS {__version__} — Python {platform.python_version()} "
          f"on {platform.system()} {platform.release()}")
    try:
        settings = load_settings()
        settings.validate()
        print(f"[ok]   server bind {settings.host}:{settings.port} (loopback={is_loopback_host(settings.host)}), "
              f"debug={'on' if settings.debug else 'off'}")
    except ConfigurationError as exc:
        print(f"[FAIL] server settings: {exc}")
        return 1
    token = load_api_token(settings)
    if token.ok:
        print(f"[ok]   API token configured (source: {token.source})")
    else:
        problems += 1
        print(f"[FAIL] API token: {token.problem}")

    for name, configured in credential_status().items():
        state = "configured" if configured else "not set (feature disabled)"
        print(f"[{'ok' if configured else '--'}]   {name}: {state}")

    print("Optional features:")
    for label, modules, extra in (
        ("vision: face utilities (OpenCV)", ["cv2"], "vision"),
        ("vision: hand gestures (MediaPipe)", ["mediapipe"], "vision"),
        ("knowledge base / RAG", ["sentence_transformers", "pypdf"], "rag"),
        ("microphone voice loop", ["speech_recognition", "pyaudio"], "voice"),
        ("wake word (Porcupine)", ["pvporcupine", "pyaudio", "speech_recognition"], "wakeword"),
    ):
        missing = [m for m in modules if not _feature(m)]
        state = "installed" if not missing else f"missing {', '.join(missing)} (install extra '{extra}')"
        print(f"  [{'ok' if not missing else '--'}] {label}: {state}")
    if _feature("cv2") or _feature("mediapipe"):
        try:
            import vision

            print(f"  vision status: {vision.status()}")
        except Exception as exc:  # noqa: BLE001 - diagnostics only
            print(f"  vision status: unavailable ({type(exc).__name__})")
    if sys.platform != "darwin":
        print("[--]   Not running on macOS: say/osascript/open/screencapture features will not work here.")
    return 1 if problems else 0


def _cmd_check_secrets(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve() if args.root else PROJECT_ROOT
    findings = scan_repository(root)
    for finding in findings:
        print(finding)
    if findings:
        print(f"{len(findings)} possible hardcoded credential(s) found. Values are not shown.", file=sys.stderr)
        return 1
    print("No hardcoded credentials found in files that could be committed.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ultron", description="ULTRON/JARVIS local assistant utilities")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("serve", help="start the local HUD/API server").set_defaults(func=_cmd_serve)
    tok = sub.add_parser("token", help="manage the local API token")
    tok.add_argument("action", choices=["init", "rotate", "show", "copy", "path"])
    tok.set_defaults(func=_cmd_token)
    sub.add_parser("open-hud", help="open the HUD with a one-time login link").set_defaults(func=_cmd_open_hud)
    sub.add_parser("doctor", help="check configuration and optional features").set_defaults(func=_cmd_doctor)
    cs = sub.add_parser("check-secrets", help="scan for hardcoded credentials")
    cs.add_argument("--root", help="repository root (default: this checkout)")
    cs.set_defaults(func=_cmd_check_secrets)
    return parser


def main(argv: list[str] | None = None) -> int:
    load_dotenv_if_enabled()
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except ConfigurationError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
