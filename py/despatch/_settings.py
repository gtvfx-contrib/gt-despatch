"""Atomic persistence for Despatch user preferences."""

from __future__ import annotations

import importlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from . import _constants


def getDefaultSettingsPath() -> Path:
    """Return the effective Despatch settings path.

    Returns:
        Default settings JSON path.

    """
    despatch_root = _nonEmptyEnvironmentPath("DESPATCH_CONFIG_ROOT")
    if despatch_root is not None:
        return despatch_root / "settings.json"
    return _getEnvoyConfigRoot() / "despatch" / "settings.json"


def _getEnvoyConfigRoot() -> Path:
    """Return Envoy's shared config root with a startup-safe fallback.

    Returns:
        Effective Envoy config root.

    """
    try:
        envoy_module = importlib.import_module("envoy")
        return Path(envoy_module.getConfigRoot())
    except (AttributeError, ImportError, OSError, RuntimeError, TypeError, ValueError):
        config_root = _nonEmptyEnvironmentPath("ENVOY_CONFIG_ROOT")
        return config_root if config_root is not None else Path.home() / ".envoy"


def _nonEmptyEnvironmentPath(name: str) -> Path | None:
    """Return a non-empty environment path.

    Args:
        name: Environment variable name.

    Returns:
        Configured path, or None when the variable is unset or blank.

    """
    value = os.environ.get(name)
    return Path(value) if value and value.strip() else None


def _defaultData() -> dict[str, Any]:
    """Return a new settings dictionary with defaults."""
    return {
        "schemaVersion": _constants.SETTINGS_SCHEMA_VERSION,
        "favorites": [],
        "recentApplications": [],
        "hideUnusedApplications": False,
        "theme": "system",
        "keepOpenAfterLaunch": False,
        "autostart": False,
        "globalShortcutEnabled": False,
        "globalShortcut": _constants.DEFAULT_GLOBAL_SHORTCUT,
        "stackRefreshIntervalSeconds": _constants.DEFAULT_STACK_REFRESH_INTERVAL_SECONDS,
        "windowGeometry": "",
    }


class SettingsStore:
    """Read and atomically update Despatch preferences.

    Args:
        settings_path: Optional JSON path used instead of the platform default.

    """

    def __init__(self, settings_path: Path | str | None = None):
        self._settings_path = Path(settings_path) if settings_path else getDefaultSettingsPath()
        self._data = _defaultData()
        self._load()

    @property
    def settings_path(self) -> Path:
        """Path used for persistence."""
        return self._settings_path

    @property
    def favorites(self) -> frozenset[str]:
        """Stable identities of favorited applications."""
        return frozenset(self._data["favorites"])

    @property
    def recent_applications(self) -> tuple[str, ...]:
        """Stable identities ordered from most to least recent."""
        return tuple(self._data["recentApplications"])

    @property
    def hide_unused_applications(self) -> bool:
        """Whether the launcher hides applications with no favorite or history."""
        return self._data["hideUnusedApplications"]

    @property
    def theme(self) -> str:
        """Theme preference: system, light, or dark."""
        return self._data["theme"]

    @property
    def keep_open_after_launch(self) -> bool:
        """Whether the launcher remains visible after a successful spawn."""
        return self._data["keepOpenAfterLaunch"]

    @property
    def autostart(self) -> bool:
        """Whether Windows login startup is requested."""
        return self._data["autostart"]

    @property
    def global_shortcut_enabled(self) -> bool:
        """Whether the configured system-wide shortcut is enabled."""
        return self._data["globalShortcutEnabled"]

    @property
    def global_shortcut(self) -> str:
        """Configured system-wide shortcut text."""
        return self._data["globalShortcut"]

    @property
    def stack_refresh_interval_seconds(self) -> int:
        """Polling interval for explicitly selected Stack files."""
        return self._data["stackRefreshIntervalSeconds"]

    @property
    def window_geometry(self) -> str:
        """Base64-encoded Qt window geometry."""
        return self._data["windowGeometry"]

    def isFavorite(self, stable_id: str) -> bool:
        """Return whether an application is a favorite.

        Args:
            stable_id: Stable application identity.

        Returns:
            True when the application is favorited.

        """
        return stable_id in self.favorites

    def toggleFavorite(self, stable_id: str) -> bool:
        """Toggle and persist an application's favorite state.

        Args:
            stable_id: Stable application identity.

        Returns:
            The new favorite state.

        """
        favorites = list(self._data["favorites"])
        if stable_id in favorites:
            favorites.remove(stable_id)
            is_favorite = False
        else:
            favorites.append(stable_id)
            is_favorite = True
        self._data["favorites"] = favorites
        self.save()
        return is_favorite

    def recordLaunch(self, stable_id: str) -> None:
        """Move an application to the front of launch history.

        Args:
            stable_id: Stable application identity.

        """
        recent = list(self._data["recentApplications"])
        if stable_id in recent:
            recent.remove(stable_id)
        recent.insert(0, stable_id)
        self._data["recentApplications"] = recent[: _constants.MAX_RECENT_APPLICATIONS]
        self.save()

    def clearLaunchHistory(self, stable_id: str) -> None:
        """Remove one application from launch history.

        Args:
            stable_id: Stable application identity.

        """
        recent = list(self._data["recentApplications"])
        if stable_id not in recent:
            return
        recent.remove(stable_id)
        self._data["recentApplications"] = recent
        self.save()

    def setHideUnusedApplications(self, hidden: bool) -> None:
        """Persist whether unused applications are hidden.

        Args:
            hidden: Whether to hide applications with no favorite or history.

        """
        self._data["hideUnusedApplications"] = bool(hidden)
        self.save()

    def updatePreferences(
        self,
        *,
        theme: str,
        keep_open_after_launch: bool,
        autostart: bool,
        global_shortcut_enabled: bool,
        global_shortcut: str,
        stack_refresh_interval_seconds: int | None = None,
    ) -> None:
        """Validate and persist settings edited by the user.

        Args:
            theme: One of system, light, or dark.
            keep_open_after_launch: Keep the main window visible after launch.
            autostart: Start Despatch when the user logs in.
            global_shortcut_enabled: Register the configured global shortcut.
            global_shortcut: Portable shortcut text.
            stack_refresh_interval_seconds: Whole-minute Stack polling interval.
                Defaults to the current value when omitted.

        Raises:
            ValueError: If a supplied setting is invalid.

        """
        if theme not in {"system", "light", "dark"}:
            raise ValueError(f"Unsupported theme: {theme}")
        if global_shortcut_enabled and not global_shortcut.strip():
            raise ValueError("A global shortcut is required when the shortcut is enabled")
        if stack_refresh_interval_seconds is None:
            stack_refresh_interval_seconds = self.stack_refresh_interval_seconds
        if (
            isinstance(stack_refresh_interval_seconds, bool)
            or not isinstance(stack_refresh_interval_seconds, int)
            or stack_refresh_interval_seconds < _constants.MINIMUM_STACK_REFRESH_INTERVAL_SECONDS
            or stack_refresh_interval_seconds > _constants.MAXIMUM_STACK_REFRESH_INTERVAL_SECONDS
            or stack_refresh_interval_seconds % 60
        ):
            raise ValueError("Stack refresh interval must be a whole number from 1 to 60 minutes")
        self._data.update(
            {
                "theme": theme,
                "keepOpenAfterLaunch": bool(keep_open_after_launch),
                "autostart": bool(autostart),
                "globalShortcutEnabled": bool(global_shortcut_enabled),
                "globalShortcut": global_shortcut.strip(),
                "stackRefreshIntervalSeconds": stack_refresh_interval_seconds,
            }
        )
        self.save()

    def setWindowGeometry(self, geometry: str) -> None:
        """Persist encoded window geometry.

        Args:
            geometry: Base64-encoded Qt geometry.

        """
        self._data["windowGeometry"] = geometry
        self.save()

    def save(self) -> None:
        """Write current settings atomically."""
        self._settings_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=self._settings_path.parent,
                prefix=f".{self._settings_path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temp_file:
                json.dump(self._data, temp_file, indent=2, sort_keys=True)
                temp_file.write("\n")
                temp_file.flush()
                os.fsync(temp_file.fileno())
                temp_path = Path(temp_file.name)
            os.replace(temp_path, self._settings_path)
        finally:
            if temp_path and temp_path.exists():
                temp_path.unlink(missing_ok=True)

    def _load(self) -> None:
        """Load valid values while preserving defaults for missing keys."""
        if not self._settings_path.exists():
            return
        try:
            with self._settings_path.open("r", encoding="utf-8") as settings_file:
                loaded = json.load(settings_file)
            if not isinstance(loaded, dict):
                return
            self._mergeLoadedData(loaded)
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            self._data = _defaultData()

    def _mergeLoadedData(self, loaded: dict[str, Any]) -> None:
        """Merge validated persisted values into defaults.

        Args:
            loaded: Parsed settings dictionary.

        """
        favorites = loaded.get("favorites", [])
        recent = loaded.get("recentApplications", [])
        if isinstance(favorites, list) and all(isinstance(value, str) for value in favorites):
            self._data["favorites"] = list(dict.fromkeys(favorites))
        if isinstance(recent, list) and all(isinstance(value, str) for value in recent):
            self._data["recentApplications"] = list(dict.fromkeys(recent))[
                : _constants.MAX_RECENT_APPLICATIONS
            ]
        theme = loaded.get("theme")
        if theme in {"system", "light", "dark"}:
            self._data["theme"] = theme
        for key in ("keepOpenAfterLaunch", "autostart", "globalShortcutEnabled"):
            if isinstance(loaded.get(key), bool):
                self._data[key] = loaded[key]
        if isinstance(loaded.get("hideUnusedApplications"), bool):
            self._data["hideUnusedApplications"] = loaded["hideUnusedApplications"]
        shortcut = loaded.get("globalShortcut")
        if isinstance(shortcut, str) and shortcut.strip():
            self._data["globalShortcut"] = shortcut.strip()
        refresh_interval = loaded.get("stackRefreshIntervalSeconds")
        if (
            isinstance(refresh_interval, int)
            and not isinstance(refresh_interval, bool)
            and _constants.MINIMUM_STACK_REFRESH_INTERVAL_SECONDS
            <= refresh_interval
            <= _constants.MAXIMUM_STACK_REFRESH_INTERVAL_SECONDS
            and refresh_interval % 60 == 0
        ):
            self._data["stackRefreshIntervalSeconds"] = refresh_interval
        geometry = loaded.get("windowGeometry")
        if isinstance(geometry, str):
            self._data["windowGeometry"] = geometry
