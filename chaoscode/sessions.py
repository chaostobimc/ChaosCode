"""Local, permission-restricted conversation history."""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import ensure_private_dir, session_dir


class SessionError(RuntimeError):
    """Raised for invalid or inaccessible saved sessions."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Session:
    id: str
    cwd: str
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    messages: list[dict[str, str]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": 1,
            "id": self.id,
            "cwd": self.cwd,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "messages": self.messages,
        }


class SessionStore:
    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or session_dir()

    def _path(self, session_id: str) -> Path:
        if not session_id or any(char not in "0123456789abcdef-" for char in session_id.lower()):
            raise SessionError("Ungültige Sitzungs-ID.")
        return self.directory / f"{session_id}.json"

    def new(self, cwd: Path) -> Session:
        resolved_cwd = str(cwd.resolve())
        session = Session(id=uuid.uuid4().hex[:12], cwd=resolved_cwd)
        self.save(session)
        return session

    def save(self, session: Session) -> None:
        ensure_private_dir(self.directory)
        session.updated_at = _now()
        path = self._path(session.id)
        temp_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=self.directory,
                prefix=".session-",
                suffix=".tmp",
                delete=False,
            ) as stream:
                temp_name = stream.name
                if os.name != "nt":
                    os.chmod(temp_name, 0o600)
                json.dump(session.as_dict(), stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_name, path)
            if os.name != "nt":
                path.chmod(0o600)
        except OSError as exc:
            raise SessionError(f"Sitzung konnte nicht gespeichert werden: {exc}") from exc
        finally:
            if temp_name and os.path.exists(temp_name):
                try:
                    os.unlink(temp_name)
                except OSError:
                    pass

    def load(self, session_id: str, cwd: Path | None = None) -> Session:
        path = self._path(session_id)
        try:
            with path.open("r", encoding="utf-8") as stream:
                raw = json.load(stream)
        except FileNotFoundError as exc:
            raise SessionError(f"Sitzung {session_id!r} wurde nicht gefunden.") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise SessionError(f"Sitzung kann nicht gelesen werden: {exc}") from exc
        try:
            session = Session(
                id=str(raw["id"]),
                cwd=str(raw["cwd"]),
                created_at=str(raw["created_at"]),
                updated_at=str(raw["updated_at"]),
                messages=[
                    {str(k): str(v) for k, v in item.items()}
                    for item in raw["messages"]
                    if isinstance(item, dict) and "role" in item and "content" in item
                ],
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise SessionError("Beschädigtes Sitzungsformat.") from exc
        if cwd is not None and Path(session.cwd).resolve() != cwd.resolve():
            raise SessionError("Diese Sitzung gehört zu einem anderen Arbeitsverzeichnis.")
        return session

    def list(self, cwd: Path | None = None) -> list[Session]:
        if not self.directory.exists():
            return []
        matches: list[Session] = []
        for path in self.directory.glob("*.json"):
            try:
                session = self.load(path.stem)
            except SessionError:
                continue
            if cwd is None or Path(session.cwd).resolve() == cwd.resolve():
                matches.append(session)
        return sorted(matches, key=lambda item: item.updated_at, reverse=True)

    def latest(self, cwd: Path) -> Session:
        sessions = self.list(cwd)
        if not sessions:
            raise SessionError("Für dieses Verzeichnis gibt es noch keine gespeicherte Sitzung.")
        return sessions[0]

    def delete(self, session_id: str) -> None:
        try:
            self._path(session_id).unlink()
        except FileNotFoundError as exc:
            raise SessionError(f"Sitzung {session_id!r} wurde nicht gefunden.") from exc
        except OSError as exc:
            raise SessionError(f"Sitzung konnte nicht gelöscht werden: {exc}") from exc
