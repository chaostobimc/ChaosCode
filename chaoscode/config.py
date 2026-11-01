"""User configuration and secret handling for ChaosCode."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

APP_DIR_NAME = "chaoscode"
TOKEN_ENV_VAR = "DEEPSEEK_AUTH_TOKEN"


class ConfigError(RuntimeError):
    """Raised when saved ChaosCode configuration cannot be read or written."""


def user_config_dir() -> Path:
    """Return the platform-appropriate per-user configuration directory."""
    if os.name == "nt":
        base = os.environ.get("APPDATA")
        return (Path(base) if base else Path.home() / "AppData" / "Roaming") / "ChaosCode"
    base = os.environ.get("XDG_CONFIG_HOME")
    return (Path(base) if base else Path.home() / ".config") / APP_DIR_NAME


def config_file() -> Path:
    return user_config_dir() / "config.json"


def session_dir() -> Path:
    return user_config_dir() / "sessions"


def ensure_private_dir(path: Path) -> None:
    try:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name != "nt":
            path.chmod(0o700)
    except OSError as exc:
        raise ConfigError(f"Kann Konfigurationsordner nicht anlegen: {path}: {exc}") from exc


def _read_config() -> dict[str, Any]:
    path = config_file()
    try:
        with path.open("r", encoding="utf-8") as stream:
            value = json.load(stream)
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"Konfiguration kann nicht gelesen werden ({path}): {exc}") from exc
    if not isinstance(value, dict):
        raise ConfigError(f"Ungültiges Konfigurationsformat in {path}.")
    return value


def _write_config(value: dict[str, Any]) -> None:
    path = config_file()
    ensure_private_dir(path.parent)
    temp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, prefix=".config-", suffix=".tmp", delete=False
        ) as stream:
            temp_name = stream.name
            if os.name != "nt":
                os.chmod(temp_name, 0o600)
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
        if os.name != "nt":
            path.chmod(0o600)
    except OSError as exc:
        raise ConfigError(f"Konfiguration kann nicht gespeichert werden ({path}): {exc}") from exc
    finally:
        if temp_name and os.path.exists(temp_name):
            try:
                os.unlink(temp_name)
            except OSError:
                pass


def get_preference(key: str, default: Any = None) -> Any:
    """Read a non-secret application preference from the user config."""
    if not isinstance(key, str) or not key.strip():
        raise ValueError("Der Einstellungsschlüssel darf nicht leer sein.")
    preferences = _read_config().get("preferences", {})
    if not isinstance(preferences, dict):
        return default
    return preferences.get(key, default)


def set_preference(key: str, value: Any) -> None:
    """Save a non-secret application preference while preserving the auth token."""
    if not isinstance(key, str) or not key.strip():
        raise ValueError("Der Einstellungsschlüssel darf nicht leer sein.")
    config = _read_config()
    preferences = config.get("preferences")
    if not isinstance(preferences, dict):
        preferences = {}
    preferences[key] = value
    config["preferences"] = preferences
    _write_config(config)


def get_auth_token() -> tuple[str | None, str | None]:
    """Return (token, source); the token is never logged or printed here."""
    from_environment = os.environ.get(TOKEN_ENV_VAR, "").strip()
    if from_environment:
        return from_environment, "Umgebungsvariable DEEPSEEK_AUTH_TOKEN"
    saved = _read_config().get("auth_token")
    if isinstance(saved, str) and saved.strip():
        return saved.strip(), "Benutzerkonfiguration"
    return None, None


def save_auth_token(token: str) -> None:
    value = token.strip()
    if not value:
        raise ValueError("Der Token darf nicht leer sein.")
    config = _read_config()
    config["auth_token"] = value
    _write_config(config)


def remove_saved_auth_token() -> bool:
    config = _read_config()
    existed = "auth_token" in config
    if existed:
        del config["auth_token"]
        if config:
            _write_config(config)
        else:
            try:
                config_file().unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                raise ConfigError(
                    f"Gespeicherter Token konnte nicht entfernt werden: {exc}"
                ) from exc
    return existed
