"""Command-line entry point: ``python -m ultron <command>`` (or ``uv run ultron <command>``).

Commands
  serve            start the local HUD/API server (same as ``python server.py``)
  token init       create the owner API token file (0600) if missing; never prints the token
  token rotate     replace the token, retire the old one for good and log out every HUD
                   session (persists across restarts; a running server picks it up at once)
  token show       print the token (only when you explicitly ask)
  token copy       copy the token to the macOS clipboard
  token path       print where the token file lives
  token ... --client   the same for the limited client token used by the wake-word listener
                   (it can send commands but can never log in to the HUD or approve
                   anything); ``token revoke --client`` retires and deletes it
  sessions status      show the persistent session state (no secret values)
  sessions revoke-all  log out every HUD session and invalidate outstanding login links
  sessions reset       owner recovery when the session state is corrupt (logs everyone out)
  open-hud         open the HUD with a one-time, short-lived login link
  doctor           report configuration and optional-feature status (no secret values)
  check-secrets    scan files that could be committed for hardcoded credentials
  security status  show whether face-check security mode is on
  security disable owner recovery: turn security mode off from this Mac's shell
                   (asks you to type DISABLE unless --yes is given)
  agent "<objective>"  run the bounded agent loop once as the local shell user: read-only and
                   routine tools only (anything that needs approval is refused here; use the HUD)
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import platform
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
from ultron.security.session_state import SessionStateError, SessionStateStore
from ultron.security.sessions import SessionSigner
from ultron.security.tokens import load_api_token, load_client_token, write_token_file
from ultron.tools.process import run_command

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _cmd_serve(_args: argparse.Namespace) -> int:
    from ultron.api.app import run

    return run()


def _store(settings) -> SessionStateStore:
    return SessionStateStore(settings.session_state_file)


def _read_token_file(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def _retire(store: SessionStateStore, tokens: list[str], *, revoke_sessions: bool) -> None:
    """Retire tokens even if the session state is corrupt: rotation is itself a recovery step."""
    try:
        store.retire_tokens(tokens, revoke_sessions=revoke_sessions)
    except SessionStateError as exc:
        print(f"Session state was unusable ({exc.problem}); resetting it (this logs out every HUD session).",
              file=sys.stderr)
        store.reset()
        store.retire_tokens(tokens, revoke_sessions=revoke_sessions)


def _cmd_token(args: argparse.Namespace) -> int:
    settings = load_settings()
    client = bool(getattr(args, "client", False))
    path = settings.client_token_file if client else settings.api_token_file
    env_name = "ULTRON_CLIENT_TOKEN" if client else "ULTRON_API_TOKEN"
    label = "client token" if client else "API token"
    loader = load_client_token if client else load_api_token
    if args.action == "path":
        print(path)
        return 0
    if args.action == "revoke":
        if not client:
            print("The owner API token cannot be revoked without a replacement; use `token rotate`.", file=sys.stderr)
            return 2
        old = [t for t in (_read_token_file(path), os.environ.get(env_name, "").strip()) if t]
        _retire(_store(settings), old, revoke_sessions=False)
        if path.exists():
            path.unlink()
        print(f"Client token revoked{' and deleted' if old else ''}. It will never be accepted again.")
        if os.environ.get(env_name):
            print(f"Remove {env_name} from your shell/.env as well.")
        return 0
    if args.action in ("init", "rotate"):
        env_set = bool(os.environ.get(env_name, "").strip())
        if path.exists() and args.action == "init":
            result = loader(settings, env={})
            if result.problem:
                print(f"Token file exists but is not usable: {result.problem}", file=sys.stderr)
                return 1
            print(f"{label.capitalize()} already exists at {path} (not shown). Use `token rotate` to replace it.")
            if env_set:
                print(f"Note: {env_name} is set in the environment and takes precedence over the token file.")
            return 0
        if args.action == "rotate":
            # Retire every token that could currently be in use (file *and* environment), so the
            # old one is refused even if it is still configured somewhere, and (owner token)
            # log out every HUD session. This is persisted before the new token is written.
            old = [t for t in (_read_token_file(path), os.environ.get(env_name, "").strip()) if t]
            _retire(_store(settings), old, revoke_sessions=not client)
        write_token_file(path, overwrite=True)
        print(f"{'Rotated' if args.action == 'rotate' else 'Created'} {label} at {path} (permissions 600).")
        if args.action == "rotate":
            if client:
                print("The old client token is retired. Restart the wake-word listener so it reads the new one.")
            else:
                print("The old token is retired and every HUD session is logged out. A running server picks up "
                      "the new token immediately; log in again with `python -m ultron open-hud`.")
        if env_set:
            print(f"WARNING: {env_name} is set in your shell or .env and overrides the token file. "
                  + ("The old value is now retired and will be refused: remove it." if args.action == "rotate"
                     else "Remove it to use the file."), file=sys.stderr)
        if not client and args.action == "init":
            print("Use `python -m ultron open-hud` to log in, or `python -m ultron token show` to view it.")
        return 0

    result = loader(settings)
    if not result.ok:
        print(result.problem or f"No {label} is configured. Run `python -m ultron token init"
              f"{' --client' if client else ''}`.", file=sys.stderr)
        return 1
    if args.action == "show":
        print(result.token)
        return 0
    if args.action == "copy":
        if sys.platform != "darwin":
            print("`token copy` uses pbcopy and is only available on macOS.", file=sys.stderr)
            return 1
        copied = run_command(["/usr/bin/pbcopy"], input_text=result.token, timeout=10, max_output=256)
        if not copied.ok:
            print("Could not copy to the clipboard.", file=sys.stderr)
            return 1
        print(f"{label.capitalize()} copied to the clipboard.")
        return 0
    return 2


def _cmd_sessions(args: argparse.Namespace) -> int:
    settings = load_settings()
    store = _store(settings)
    if args.action == "status":
        try:
            state = store.load()
        except SessionStateError as exc:
            print(f"[FAIL] session state: {exc}", file=sys.stderr)
            return 1
        print(f"Session state OK at {store.path}: {len(state.revoked)} logged-out session(s) remembered, "
              f"{len(state.retired_tokens)} retired token(s).")
        return 0
    if args.action == "revoke-all":
        try:
            store.revoke_all()
        except SessionStateError as exc:
            print(f"Could not update the session state: {exc}", file=sys.stderr)
            return 1
        print("Every HUD session and outstanding login link is now invalid (effective immediately, also after "
              "restarts). Log in again with `python -m ultron open-hud`.")
        return 0
    # reset (owner recovery)
    if not args.yes:
        if not sys.stdin.isatty():
            print("Refusing to reset the session state without confirmation; re-run with --yes.", file=sys.stderr)
            return 1
        if input("Type RESET to log out every HUD session and rebuild the session state: ").strip() != "RESET":
            print("Not changed.")
            return 1
    try:
        kept = store.reset()
    except SessionStateError as exc:
        print(f"Could not rebuild the session state: {exc.problem} Check permissions on {store.path.parent}.",
              file=sys.stderr)
        return 1
    print("Session state rebuilt; every HUD session is logged out.")
    if not kept:
        print("The old file was unreadable, so its list of retired tokens was lost. If you rotated a token "
              "because it leaked, run `python -m ultron token rotate` again.", file=sys.stderr)
    return 0


def _cmd_open_hud(_args: argparse.Namespace) -> int:
    settings = load_settings()
    result = load_api_token(settings)
    if not result.ok:
        print(result.problem, file=sys.stderr)
        return 1
    try:
        generation = _store(settings).load().generation
    except SessionStateError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    ticket = SessionSigner(result.token, settings.session_ttl_seconds).issue_login_ticket(generation)
    url = f"{settings.local_base_url}/login#ticket={ticket}"  # fragment: never sent to the server or its logs
    browser_app = os.environ.get("ULTRON_BROWSER_APP")
    if sys.platform == "darwin" and browser_app:
        run_command(["/usr/bin/open", "-a", browser_app, url], timeout=15, max_output=256)
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
        if token.source == "env":
            print("[--]   ULTRON_API_TOKEN overrides the token file; `token rotate` will retire it, "
                  "so prefer the file.")
    else:
        problems += 1
        print(f"[FAIL] API token: {token.problem}")
    client = load_client_token(settings)
    if client.ok:
        print(f"[ok]   client token configured for the wake-word listener (source: {client.source})")
    elif client.problem:
        problems += 1
        print(f"[FAIL] client token: {client.problem}")
    else:
        print("[--]   no client token: the wake-word listener would use the owner token "
              "(run `python -m ultron token init --client`)")
    try:
        state = _store(settings).load()
        if token.ok and state.is_retired(token.token):
            problems += 1
            print("[FAIL] the configured API token has been retired by `token rotate`; remove the old value.")
        else:
            print("[ok]   session state readable (persistent logout/rotation revocation)")
    except SessionStateError as exc:
        problems += 1
        print(f"[FAIL] session state: {exc}")

    for name, configured in credential_status().items():
        state = "configured" if configured else "not set (feature disabled)"
        print(f"[{'ok' if configured else '--'}]   {name}: {state}")
    from ultron.config import load_agent_config, load_chat_model

    agent = load_agent_config()
    print(f"[--]   models: chat={load_chat_model()} agent={agent.model} "
          f"(agent loop {'on' if agent.enabled else 'off'}; live check: docs/M2_1_REAL_MODEL_VALIDATION.md)")

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


def _cmd_security(args: argparse.Namespace) -> int:
    """Local-shell security-mode controls.

    Voice, HUD and API can only turn security mode *off* through a confirmed action that
    first passes the face check. If the camera or the vision extra is unavailable that check
    can never pass, so the owner needs a way back in: this command, which requires a shell
    on this Mac as the same OS user (the trust level needed to read the API token anyway).
    """
    import importlib

    legacy = importlib.import_module("main")
    if args.action == "status":
        print(legacy.security_status())
        return 0
    if not args.yes:
        if not sys.stdin.isatty():
            print("Refusing to disable security mode without confirmation; re-run with --yes.", file=sys.stderr)
            return 1
        if input("Type DISABLE to turn off security mode: ").strip() != "DISABLE":
            print("Not changed.")
            return 1
    print(legacy.disable_security_mode_offline())
    print("If JARVIS is running, restart it now: a running instance keeps its old setting in memory "
          "and could write it back.")
    return 0


def _cmd_agent(args: argparse.Namespace) -> int:
    """One agent run as the OS user at this shell (``Channel.CLI``: no approver attached)."""
    import importlib

    from ultron.security.policy import CommandContext

    objective = " ".join(args.objective).strip()
    if not objective:
        print("Tell me what to do, e.g.: python -m ultron agent \"what time is it and how is my battery?\"",
              file=sys.stderr)
        return 2
    legacy = importlib.import_module("main")
    if not legacy.agent_available():
        print("The agent loop is unavailable: set OPENAI_API_KEY in your .env (and do not set ULTRON_AGENT=0).",
              file=sys.stderr)
        return 2
    ctx = CommandContext.local_cli()
    try:
        reply = legacy.run_agent(objective, ctx)
    except KeyboardInterrupt:
        legacy.AGENT_RUNS.cancel(ctx.principal)
        print("\nStopped. A tool that was already running may still finish.", file=sys.stderr)
        return 130
    report = next((value for kind, value in reversed(ctx.events) if kind == "agent_report"), None)
    print(reply)
    if report is not None and args.steps:
        for step in report.steps:
            print(f"  {step.call_id} {step.tool}: {step.status} - {step.message}")
        print(f"  status={report.status} verified={report.verified}")
    return 0 if report is not None and report.status in ("answered", "completed") else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ultron", description="ULTRON/JARVIS local assistant utilities")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("serve", help="start the local HUD/API server").set_defaults(func=_cmd_serve)
    tok = sub.add_parser("token", help="manage the local API token (or, with --client, the client token)")
    tok.add_argument("action", choices=["init", "rotate", "show", "copy", "path", "revoke"])
    tok.add_argument("--client", action="store_true",
                     help="the limited wake-word/client token instead of the owner token")
    tok.set_defaults(func=_cmd_token)
    ses = sub.add_parser("sessions", help="HUD session revocation and recovery")
    ses.add_argument("action", choices=["status", "revoke-all", "reset"])
    ses.add_argument("--yes", action="store_true", help="reset without asking for typed confirmation")
    ses.set_defaults(func=_cmd_sessions)
    sub.add_parser("open-hud", help="open the HUD with a one-time login link").set_defaults(func=_cmd_open_hud)
    sub.add_parser("doctor", help="check configuration and optional features").set_defaults(func=_cmd_doctor)
    cs = sub.add_parser("check-secrets", help="scan for hardcoded credentials")
    cs.add_argument("--root", help="repository root (default: this checkout)")
    cs.set_defaults(func=_cmd_check_secrets)
    sec = sub.add_parser("security", help="show or (owner recovery) disable face-check security mode")
    sec.add_argument("action", choices=["status", "disable"])
    sec.add_argument("--yes", action="store_true", help="do not ask for typed confirmation")
    sec.set_defaults(func=_cmd_security)
    agent = sub.add_parser("agent", help="run the bounded agent loop once (read-only and routine tools)")
    agent.add_argument("objective", nargs="+", help="what you want done, in plain words")
    agent.add_argument("--steps", action="store_true", help="also print each tool call and the final status")
    agent.set_defaults(func=_cmd_agent)
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
