"""Typed domain models used by Despatch."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


@dataclass(frozen=True, slots=True)
class NamedStack:
    """A published Envoy Stack."""

    name: str
    version: str
    path: Path


class StackMode(StrEnum):
    """The active Despatch Stack-resolution mode."""

    PROMPT = "prompt"
    AUTOMATIC = "automatic"
    EXPLICIT = "explicit"


@dataclass(frozen=True, slots=True)
class StackSelection:
    """A resolved explicit Envoy Stack selection."""

    persisted_value: str
    display_name: str
    path: Path
    version: str = ""
    is_custom: bool = False


@dataclass(frozen=True, slots=True)
class StackState:
    """The Stack resolution state used by the catalog and launch flow."""

    mode: StackMode
    selection: StackSelection | None = None


@dataclass(frozen=True, slots=True)
class StackFileState:
    """Lightweight filesystem identity used to detect Stack updates."""

    path: Path
    size: int
    modified_ns: int
    changed_ns: int


@dataclass(frozen=True, slots=True)
class BundleRecord:
    """Bundle metadata required for catalog discovery."""

    bundle_id: str
    root: Path
    envoy_directory: Path
    commands: tuple[str, ...]
    version: str = ""
    is_production: bool = False


@dataclass(frozen=True, slots=True)
class CatalogGroup:
    """A Stack-wide display group declared by one bundle manifest."""

    stable_id: str
    name: str
    order: int
    bundle_id: str


@dataclass(frozen=True, slots=True)
class ApplicationEntry:
    """A launchable Envoy command declared by a bundle manifest."""

    stable_id: str
    application_id: str
    bundle_id: str
    name: str
    command: str
    args: tuple[str, ...]
    description: str
    icon_path: Path | None
    group_id: str
    keywords: tuple[str, ...]
    in_terminal: bool
    order: int
    source_path: Path
    homepage: str = ""

    @property
    def command_line(self) -> tuple[str, ...]:
        """Command and arguments as an argv tuple."""
        return (self.command, *self.args)


@dataclass(frozen=True, slots=True)
class CatalogDiagnostic:
    """A recoverable issue encountered while loading a catalog."""

    severity: str
    message: str
    source_path: Path | None = None


@dataclass(frozen=True, slots=True)
class CatalogSnapshot:
    """A complete immutable application catalog snapshot."""

    stack_state: StackState
    applications: tuple[ApplicationEntry, ...]
    groups: tuple[CatalogGroup, ...]
    diagnostics: tuple[CatalogDiagnostic, ...]

    def applicationMap(self) -> dict[str, ApplicationEntry]:
        """Return applications keyed by stable identity.

        Returns:
            Mapping of stable identity to application entry.

        """
        return {application.stable_id: application for application in self.applications}
