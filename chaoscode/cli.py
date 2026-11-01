"""Command-line interface for ChaosCode."""

from __future__ import annotations

import argparse
import getpass
import importlib.metadata
import importlib.util
import os
import platform
import sys
import uuid
from collections.abc import Sequence
from pathlib import Path

from . import __version__
from .agent import AgentError, CodingAgent
from .config import (
    ConfigError,
    config_file,
    ensure_private_dir,
    get_auth_token,
    remove_saved_auth_token,
    save_auth_token,
    user_config_dir,
)
from .references import select_file_references
from .sessions import Session, SessionError, SessionStore
from .ui import (
    ThinkingIndicator,
    error,
    info,
    print_banner,
    print_response,
    prompt_approval,
    styled,
    success,
    warning,
)

HELP_TEXT = """Befehle in der interaktiven Sitzung:
  /help          Diese Hilfe anzeigen
  /status        Projekt, Sitzung und Einstellungen anzeigen
  /search on|off DeepSeek-Websuche umschalten
  /new           Eine neue Unterhaltung beginnen
  /clear         Alias für /new
  /exit          ChaosCode beenden

Dateien als Kontext auswählen:
  @                              Vorschläge erscheinen; Tab übernimmt eine Auswahl
  @src/main.py                  Dateiinhalt als Kontext laden
  @"docs/notizen für heute.md"  Pfade mit Leerzeichen
  @{docs/notizen für heute.md}  Alternative Schreibweise

Die Dateiinhalte werden sicher aus dem Projektordner gelesen. Änderungen brauchen
weiterhin deine Zustimmung. Schreibe ansonsten einfach deine Aufgabe oder Frage.
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="chaoscode",
        description="Terminal-KI-Agent für Coding-Aufgaben (DeepSeek4Free, keine bezahlte API).",
        epilog="Tipp: `chaoscode auth login` richtet den kostenlosen DeepSeek-Webzugang ein.",
    )
    parser.add_argument(
        "prompt", nargs="*", help="Aufgabe direkt übergeben; ohne Aufgabe startet der Dialog"
    )
    parser.add_argument(
        "-C", "--workdir", default=".", help="Projektverzeichnis (Standard: aktuelles Verzeichnis)"
    )
    parser.add_argument(
        "--search", action="store_true", help="DeepSeek-Websuche für Antworten aktivieren"
    )
    parser.add_argument(
        "--resume", action="store_true", help="Letzte Sitzung dieses Projekts fortsetzen"
    )
    parser.add_argument(
        "--session",
        dest="resume_id",
        metavar="ID",
        help="Eine bestimmte gespeicherte Sitzung fortsetzen",
    )
    parser.add_argument("--no-save", action="store_true", help="Konversation nicht lokal speichern")
    parser.add_argument(
        "-p",
        "--print",
        dest="print_mode",
        action="store_true",
        help="Einmalige Anfrage, nur die Antwort ausgeben",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Dateiänderungen und Shell-Befehle ohne Rückfrage genehmigen",
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        default=12,
        metavar="N",
        help="Maximale Agent-Schritte pro Aufgabe (1–30; Standard: 12)",
    )
    parser.add_argument("--version", action="version", version=f"ChaosCode {__version__}")
    return parser


def _print_auth_instructions() -> None:
    print(
        "Um den kostenlosen DeepSeek-Webzugang zu verbinden:\n"
        "  1. In chat.deepseek.com anmelden.\n"
        "  2. In den Browser-Entwicklertools Application/Storage → Local Storage →\n"
        "     https://chat.deepseek.com öffnen und den Tokenwert von userToken kopieren.\n"
        '     Alternativ in der Browser-Konsole: JSON.parse(localStorage.getItem("userToken")).value\n'
        "  3. Den Token an der folgenden versteckten Eingabe einfügen.\n\n"
        "Der Token ist ein Passwort. Nicht teilen oder in ein Projekt einfügen.\n"
        "ChaosCode nutzt die inoffizielle DeepSeek4Free-Schnittstelle; sie kann sich ändern."
    )


def _auth_command(arguments: Sequence[str]) -> int:
    action = arguments[0] if arguments else "status"
    if action in {"-h", "--help"}:
        print("Verwendung: chaoscode auth [login|status|logout]")
        return 0
    if len(arguments) > 1:
        error("Verwendung: chaoscode auth [login|status|logout]")
        return 2
    try:
        if action == "login":
            if not sys.stdin.isatty():
                error("`chaoscode auth login` benötigt ein interaktives Terminal.")
                return 2
            _print_auth_instructions()
            token = getpass.getpass("DeepSeek-Web-Token (Eingabe verborgen): ").strip()
            if not token:
                error("Kein Token eingegeben.")
                return 2
            save_auth_token(token)
            success(
                f"Token sicher gespeichert unter {config_file()} (Dateirechte nur für deinen Benutzer)."
            )
            print("Du kannst jetzt `chaoscode` im Projektordner starten.")
            return 0
        if action == "status":
            token, source = get_auth_token()
            if token:
                success(
                    f"DeepSeek ist konfiguriert über: {source}. Der Token wird nicht angezeigt."
                )
            else:
                warning("Kein Token konfiguriert. Starte `chaoscode auth login`.")
            print(f"Benutzerkonfiguration: {config_file()}")
            return 0 if token else 1
        if action == "logout":
            removed = remove_saved_auth_token()
            token_from_env = bool(os.environ.get("DEEPSEEK_AUTH_TOKEN", "").strip())
            if removed:
                success("Gespeicherter DeepSeek-Token entfernt.")
            else:
                info("Es war kein gespeicherter Token vorhanden.")
            if token_from_env:
                warning(
                    "DEEPSEEK_AUTH_TOKEN ist weiterhin in der Umgebung gesetzt und hat Vorrang."
                )
            return 0
        error("Unbekannter auth-Befehl. Erlaubt: login, status, logout.")
        return 2
    except (ConfigError, ValueError) as exc:
        error(str(exc))
        return 1


def _sessions_command(arguments: Sequence[str]) -> int:
    store = SessionStore()
    action = arguments[0] if arguments else "list"
    if action in {"-h", "--help"}:
        print("Verwendung: chaoscode sessions [list|remove ID]")
        return 0
    if action == "list" and len(arguments) <= 1:
        try:
            items = store.list(Path.cwd())
        except (SessionError, OSError) as exc:
            error(str(exc))
            return 1
        if not items:
            print("Keine gespeicherten Sitzungen für dieses Verzeichnis.")
            return 0
        for item in items:
            turns = sum(message.get("role") == "user" for message in item.messages)
            print(f"{item.id}  {item.updated_at}  {turns} Aufgabe(n)  {item.cwd}")
        return 0
    if action == "remove" and len(arguments) == 2:
        try:
            store.delete(arguments[1])
        except SessionError as exc:
            error(str(exc))
            return 1
        success(f"Sitzung {arguments[1]} gelöscht.")
        return 0
    error("Verwendung: chaoscode sessions [list|remove ID]")
    return 2


def _doctor_command() -> int:
    print(f"ChaosCode {__version__} · Python {platform.python_version()} · {platform.system()}")
    checks: list[tuple[str, bool, str]] = []
    checks.append(("Python >= 3.10", sys.version_info >= (3, 10), platform.python_version()))
    for module, distribution in (("curl_cffi", "curl-cffi"), ("wasmtime", "wasmtime")):
        available = importlib.util.find_spec(module) is not None
        version = "nicht installiert"
        if available:
            try:
                version = importlib.metadata.version(distribution)
            except importlib.metadata.PackageNotFoundError:
                version = "installiert"
        checks.append((distribution, available, version))
    try:
        token, source = get_auth_token()
        token_ok = bool(token)
        token_detail = source or "nicht konfiguriert"
    except ConfigError as exc:
        token_ok = False
        token_detail = f"Konfigurationsfehler: {exc}"
    checks.append(("DeepSeek-Web-Token", token_ok, token_detail))
    current = Path.cwd()
    checks.append(("Projektordner", current.is_dir(), str(current.resolve())))
    try:
        path = user_config_dir()
        ensure_private_dir(path)
        writable = os.access(path, os.W_OK)
    except (ConfigError, OSError):
        writable = False
    checks.append(("Benutzerkonfiguration beschreibbar", writable, str(user_config_dir())))
    all_ok = True
    for label, passed, detail in checks:
        status = styled("OK", "32") if passed else styled("FEHLT", "31")
        print(f"[{status}] {label}: {detail}")
        all_ok = all_ok and passed
    if all_ok:
        print(
            "\nBereit. Falls der DeepSeek-Webzugang blockiert ist, verwendet ChaosCode keinen automatischen Bypass."
        )
        return 0
    if not token_ok:
        print("\nDeepSeek verbinden: `chaoscode auth login` (oder DEEPSEEK_AUTH_TOKEN setzen).")
    if any(not passed for label, passed, _detail in checks if label in {"curl-cffi", "wasmtime"}):
        print("\nFehlende Abhängigkeiten: aus dem Checkout `pipx install --force .` ausführen.")
    if not checks[-1][1]:
        print("\nDer ChaosCode-Konfigurationsordner ist nicht beschreibbar; Rechte/Platz prüfen.")
    return 1


def _resolve_workdir(value: str) -> Path:
    root = Path(value).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError(f"Das Arbeitsverzeichnis ist kein Ordner: {root}")
    return root


def _select_session(store: SessionStore, root: Path, resume: str | None, no_save: bool) -> Session:
    if no_save:
        return Session(id=uuid.uuid4().hex[:12], cwd=str(root))
    if resume is None:
        return store.new(root)
    sessions = store.list(root)
    if resume in {"latest", "last"}:
        if not sessions:
            raise SessionError("Für dieses Projekt gibt es keine gespeicherte Sitzung.")
        return sessions[0]
    matches = [item for item in sessions if item.id.startswith(resume)]
    if len(matches) == 1:
        return store.load(matches[0].id, root)
    if not matches:
        raise SessionError(f"Keine Sitzung mit ID-Präfix {resume!r} für dieses Projekt gefunden.")
    raise SessionError(f"ID-Präfix {resume!r} ist nicht eindeutig; gib mehr Zeichen an.")


def _read_stdin_prompt() -> str:
    text = sys.stdin.read(20_001)
    if len(text) > 20_000:
        raise ValueError("Die über stdin gelesene Aufgabe ist länger als 20.000 Zeichen.")
    return text.strip()


def _print_status(agent: CodingAgent, root: Path, search: bool) -> None:
    turn_count = sum(message.get("role") == "user" for message in agent.session.messages)
    print(
        f"Projekt: {root}\nSitzung: {agent.session.id}\nAufgaben in der Sitzung: {turn_count}\nWebsuche: {'an' if search else 'aus'}"
    )


def _handle_slash_command(
    command: str,
    agent: CodingAgent,
    root: Path,
    store: SessionStore,
    no_save: bool,
) -> tuple[bool, bool]:
    """Return (handled, should_exit)."""
    parts = command.strip().split()
    action = parts[0].lower() if parts else "/help"
    if action in {"/exit", "/quit"}:
        return True, True
    if action in {"/help", "/?"}:
        print(HELP_TEXT)
        return True, False
    if action == "/status":
        _print_status(agent, root, agent.search)
        return True, False
    if action == "/search":
        if len(parts) != 2 or parts[1].lower() not in {"on", "off"}:
            print("Verwendung: /search on|off")
        else:
            agent.search = parts[1].lower() == "on"
            print(f"DeepSeek-Websuche ist jetzt {'aktiviert' if agent.search else 'deaktiviert'}.")
        return True, False
    if action in {"/new", "/clear"}:
        agent.session = (
            Session(
                id=uuid.uuid4().hex[:12],
                cwd=str(root),
            )
            if no_save
            else store.new(root)
        )
        print(f"Neue Sitzung gestartet ({agent.session.id}).")
        return True, False
    if action.startswith("/"):
        print(f"Unbekannter Befehl: {action}. Nutze /help.")
        return True, False
    return False, False


def _run_prompt(
    agent: CodingAgent,
    prompt: str,
    *,
    print_mode: bool,
    auto_approve: bool,
) -> int:
    if print_mode and not auto_approve:

        def approve(title: str, detail: str) -> bool:
            print(
                f"\nAktion nicht ausgeführt: {title}. Im --print-Modus werden Änderungen und Befehle "
                "nur mit ausdrücklich gesetztem --yes genehmigt.",
                file=sys.stderr,
            )
            return False

        agent.approve = approve
    try:
        attachments = select_file_references(prompt, agent.tools)
        if attachments:
            selected = ", ".join(f"@{item.path}" for item in attachments)
            info(f"Datei-Kontext geladen: {selected}")
        with ThinkingIndicator():
            result = agent.run_turn(
                prompt,
                selected_files=[(item.path, item.content) for item in attachments],
            )
    except KeyboardInterrupt:
        print("\nAnfrage abgebrochen.", file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001 - keep unexpected failures user-facing in the CLI
        from .provider import ProviderError

        if isinstance(
            exc, (ProviderError, AgentError, SessionError, ConfigError, OSError, ValueError)
        ):
            error(str(exc))
            return 1
        error(f"Unerwarteter Fehler ({type(exc).__name__}): {exc}")
        return 1
    print_response(result.text, print_mode=print_mode)
    if result.tool_calls:
        info(f"Tool-Schritte (inkl. @Dateien): {result.tool_calls}")
    return 0


def _run_chat(options: argparse.Namespace) -> int:
    if options.max_steps < 1 or options.max_steps > 30:
        error("--max-steps muss zwischen 1 und 30 liegen.")
        return 2
    if options.no_save and (options.resume or options.resume_id):
        error("--no-save kann nicht mit --resume oder --session kombiniert werden.")
        return 2
    if options.resume and options.resume_id:
        error("Nutze entweder --resume oder --session ID, nicht beides.")
        return 2
    prompt = " ".join(options.prompt).strip()
    if options.print_mode and not prompt:
        if sys.stdin.isatty():
            error(
                "Im --print-Modus musst du eine Aufgabe angeben oder Eingabe über stdin weiterleiten."
            )
            return 2
        try:
            prompt = _read_stdin_prompt()
        except (OSError, ValueError) as exc:
            error(str(exc))
            return 2
        if not prompt:
            error("Keine Aufgabe übergeben.")
            return 2
    if not options.print_mode and not prompt and not sys.stdin.isatty():
        error("Kein Terminal-Eingabestream. Gib eine Aufgabe an oder nutze `--print` mit stdin.")
        return 2
    if options.yes:
        warning("--yes genehmigt Dateiänderungen und Shell-Befehle automatisch.")
    try:
        root = _resolve_workdir(options.workdir)
        token, _source = get_auth_token()
        if not token:
            error("Kein DeepSeek-Web-Token konfiguriert. Starte `chaoscode auth login`.")
            return 1
        store = SessionStore()
        resume_selector = options.resume_id or ("latest" if options.resume else None)
        session = _select_session(store, root, resume_selector, options.no_save)
        if options.no_save:
            session_store = None
        else:
            session_store = store
        from .provider import DeepSeek4FreeClient

        provider = DeepSeek4FreeClient(token)
        agent = CodingAgent(
            workspace=root,
            provider=provider,
            session=session,
            session_store=session_store,
            approve=lambda title, detail: prompt_approval(title, detail, auto_approve=options.yes),
            search=options.search,
            max_steps=options.max_steps,
        )
    except (ConfigError, SessionError, OSError, ValueError, RuntimeError) as exc:
        error(str(exc))
        return 1

    try:
        if sys.stdout.isatty() and not options.print_mode:
            print_banner()
            print(
                styled(f"v{__version__}", "2;37")
                + styled("  ·  DeepSeek4Free", "36")
                + f"  ·  {root}"
            )
            print(
                "Dateiänderungen und Shell-Befehle brauchen deine Zustimmung. "
                "Dateien im Prompt mit @pfad auswählen; /help zeigt Befehle."
            )
        if prompt:
            return _run_prompt(
                agent,
                prompt,
                print_mode=options.print_mode,
                auto_approve=options.yes,
            )
        if not sys.stdin.isatty():
            error(
                "Kein Terminal-Eingabestream. Gib eine Aufgabe an oder nutze `--print` mit stdin."
            )
            return 2

        from .completion import AtFilePrompt

        interactive_prompt = AtFilePrompt(agent.tools)
        while True:
            try:
                user_text = interactive_prompt.prompt()
            except EOFError:
                print()
                break
            except KeyboardInterrupt:
                print("\nAuf Wiedersehen.")
                break
            if not user_text.strip():
                continue
            handled, should_exit = _handle_slash_command(
                user_text, agent, root, store, options.no_save
            )
            if handled:
                if should_exit:
                    break
                continue
            code = _run_prompt(
                agent,
                user_text,
                print_mode=False,
                auto_approve=options.yes,
            )
            if code == 130:
                continue
            if code != 0:
                print(
                    "Die letzte Anfrage ist fehlgeschlagen; du kannst eine neue Aufgabe eingeben."
                )
        return 0
    finally:
        close = getattr(provider, "close", None)
        if callable(close):
            close()


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "auth":
        return _auth_command(args[1:])
    if args and args[0] == "sessions":
        return _sessions_command(args[1:])
    if args and args[0] == "doctor":
        if len(args) > 1:
            error("Verwendung: chaoscode doctor")
            return 2
        return _doctor_command()
    if args and args[0] == "version":
        print(f"ChaosCode {__version__}")
        return 0
    parser = build_parser()
    options = parser.parse_args(args)
    return _run_chat(options)


if __name__ == "__main__":
    raise SystemExit(main())
