"""Load Despatch manifests from active Envoy bundles."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import _constants, _models


@dataclass(frozen=True, slots=True)
class _ManifestRecord:
    """One validated manifest and its contributing bundle."""

    bundle: _models.BundleRecord
    path: Path
    data: dict[str, Any]


def getCurrentPlatform() -> str:
    """Return the manifest platform token for this interpreter."""
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


class CatalogLoader:
    """Build immutable catalog snapshots from an Envoy gateway.

    Args:
        gateway: Object providing Stack-aware bundle loading.
        platform_name: Optional manifest platform override used by tests.

    """

    def __init__(self, gateway: Any, platform_name: str | None = None):
        self._gateway = gateway
        self._platform_name = platform_name or getCurrentPlatform()

    def loadCatalog(self, stack_state: _models.StackState) -> _models.CatalogSnapshot:
        """Load and validate all manifests in the active Stack.

        Args:
            stack_state: Stack mode to use for bundle discovery.

        Returns:
            Complete application catalog and recoverable diagnostics.

        """
        bundles = self._gateway.loadBundles(stack_state)
        command_names = {command_name for bundle in bundles for command_name in bundle.commands}
        diagnostics: list[_models.CatalogDiagnostic] = []
        manifests: list[_ManifestRecord] = []
        for bundle in bundles:
            manifest_path = bundle.envoy_directory / _constants.MANIFEST_FILENAME
            if not manifest_path.is_file():
                continue
            manifest_data = self._readManifest(manifest_path, diagnostics)
            if manifest_data is None:
                continue
            manifests.append(_ManifestRecord(bundle, manifest_path, manifest_data))

        groups = self._parseGroups(manifests, diagnostics)
        group_ids = {group.stable_id for group in groups}
        applications: list[_models.ApplicationEntry] = []
        suppressed_bundles: set[str] = set()
        suppressed_applications: set[str] = set()
        for manifest in manifests:
            applications.extend(
                self._parseApplications(
                    manifest,
                    command_names,
                    group_ids,
                    diagnostics,
                )
            )
            manifest_bundles, manifest_applications = self._parseSuppressions(
                manifest,
                diagnostics,
            )
            suppressed_bundles.update(manifest_bundles)
            suppressed_applications.update(manifest_applications)

        applications = [
            application
            for application in applications
            if application.bundle_id not in suppressed_bundles
            and application.stable_id not in suppressed_applications
        ]

        groups.sort(key=lambda group: (group.order, group.name.casefold(), group.stable_id))
        applications.sort(key=lambda application: (application.order, application.name.casefold()))
        return _models.CatalogSnapshot(
            stack_state=stack_state,
            applications=tuple(applications),
            groups=tuple(groups),
            diagnostics=tuple(diagnostics),
        )

    @staticmethod
    def _readManifest(
        manifest_path: Path,
        diagnostics: list[_models.CatalogDiagnostic],
    ) -> dict[str, Any] | None:
        """Read one manifest and report recoverable parse failures."""
        try:
            with manifest_path.open("r", encoding="utf-8") as manifest_file:
                manifest_data = json.load(manifest_file)
        except OSError as error:
            diagnostics.append(
                _models.CatalogDiagnostic(
                    "error", f"Unable to read manifest: {error}", manifest_path
                )
            )
            return None
        except json.JSONDecodeError as error:
            diagnostics.append(
                _models.CatalogDiagnostic(
                    "error",
                    f"Invalid JSON at line {error.lineno}, column {error.colno}: {error.msg}",
                    manifest_path,
                )
            )
            return None
        if not isinstance(manifest_data, dict):
            diagnostics.append(
                _models.CatalogDiagnostic("error", "Manifest root must be an object", manifest_path)
            )
            return None
        if manifest_data.get("schemaVersion") != _constants.MANIFEST_SCHEMA_VERSION:
            diagnostics.append(
                _models.CatalogDiagnostic(
                    "error",
                    f"Unsupported schemaVersion; expected {_constants.MANIFEST_SCHEMA_VERSION}",
                    manifest_path,
                )
            )
            return None
        return manifest_data

    def _parseApplications(
        self,
        manifest: _ManifestRecord,
        command_names: set[str],
        group_ids: set[str],
        diagnostics: list[_models.CatalogDiagnostic],
    ) -> list[_models.ApplicationEntry]:
        """Validate and construct applications from one manifest."""
        applications_data = manifest.data.get("applications")
        if not isinstance(applications_data, list):
            diagnostics.append(
                _models.CatalogDiagnostic(
                    "error",
                    "'applications' must be an array",
                    manifest.path,
                )
            )
            return []

        applications: list[_models.ApplicationEntry] = []
        seen_ids: set[str] = set()
        for order, application_data in enumerate(applications_data):
            application = self._parseApplication(
                manifest.bundle,
                manifest.path,
                application_data,
                order,
                command_names,
                group_ids,
                seen_ids,
                diagnostics,
            )
            if application is not None:
                applications.append(application)
        return applications

    @staticmethod
    def _parseGroups(
        manifests: list[_ManifestRecord],
        diagnostics: list[_models.CatalogDiagnostic],
    ) -> list[_models.CatalogGroup]:
        """Validate and register Stack-wide group declarations."""
        groups_by_id: dict[str, _models.CatalogGroup] = {}
        for manifest in manifests:
            groups_data = manifest.data.get("groups")
            if groups_data is None:
                continue
            if not isinstance(groups_data, list):
                diagnostics.append(
                    _models.CatalogDiagnostic(
                        "warning",
                        "'groups' must be an array",
                        manifest.path,
                    )
                )
                continue
            for order, group_data in enumerate(groups_data):
                if not isinstance(group_data, dict):
                    diagnostics.append(
                        _models.CatalogDiagnostic(
                            "warning",
                            "Group entries must be objects",
                            manifest.path,
                        )
                    )
                    continue
                group_id = group_data.get("id")
                group_name = group_data.get("name")
                if not isinstance(group_id, str) or not group_id.strip():
                    diagnostics.append(
                        _models.CatalogDiagnostic(
                            "warning",
                            "Group 'id' must be a string",
                            manifest.path,
                        )
                    )
                    continue
                if not isinstance(group_name, str) or not group_name.strip():
                    diagnostics.append(
                        _models.CatalogDiagnostic(
                            "warning",
                            "Group 'name' must be a string",
                            manifest.path,
                        )
                    )
                    continue
                if group_id in groups_by_id:
                    first_group = groups_by_id[group_id]
                    diagnostics.append(
                        _models.CatalogDiagnostic(
                            "warning",
                            f"Global group id '{group_id}' is already declared by bundle "
                            f"'{first_group.bundle_id}'; using the first declaration",
                            manifest.path,
                        )
                    )
                    continue
                declared_order = group_data.get("order", order)
                if not isinstance(declared_order, int):
                    declared_order = order
                groups_by_id[group_id] = _models.CatalogGroup(
                    stable_id=group_id,
                    name=group_name.strip(),
                    order=declared_order,
                    bundle_id=manifest.bundle.bundle_id,
                )
        return list(groups_by_id.values())

    @staticmethod
    def _parseSuppressions(
        manifest: _ManifestRecord,
        diagnostics: list[_models.CatalogDiagnostic],
    ) -> tuple[set[str], set[str]]:
        """Validate and return suppression identities from one manifest."""
        suppression_data = manifest.data.get("suppress")
        if suppression_data is None:
            return set(), set()
        if not isinstance(suppression_data, dict):
            diagnostics.append(
                _models.CatalogDiagnostic(
                    "warning",
                    "'suppress' must be an object",
                    manifest.path,
                )
            )
            return set(), set()

        allowed_fields = {"bundles", "applications"}
        for field_name in sorted(set(suppression_data) - allowed_fields):
            diagnostics.append(
                _models.CatalogDiagnostic(
                    "warning",
                    f"Unknown suppress field '{field_name}'",
                    manifest.path,
                )
            )

        bundles = CatalogLoader._parseSuppressionArray(
            manifest,
            suppression_data,
            "bundles",
            diagnostics,
        )
        applications = CatalogLoader._parseSuppressionArray(
            manifest,
            suppression_data,
            "applications",
            diagnostics,
        )
        return bundles, applications

    @staticmethod
    def _parseSuppressionArray(
        manifest: _ManifestRecord,
        suppression_data: dict[str, Any],
        field_name: str,
        diagnostics: list[_models.CatalogDiagnostic],
    ) -> set[str]:
        """Validate one array below the suppression object."""
        targets = suppression_data.get(field_name, [])
        if not isinstance(targets, list):
            diagnostics.append(
                _models.CatalogDiagnostic(
                    "warning",
                    f"'suppress.{field_name}' must be an array",
                    manifest.path,
                )
            )
            return set()

        valid_targets: set[str] = set()
        for index, target in enumerate(targets):
            if not isinstance(target, str) or not target.strip():
                diagnostics.append(
                    _models.CatalogDiagnostic(
                        "warning",
                        f"Suppress {field_name} entry {index + 1} must be a non-empty string",
                        manifest.path,
                    )
                )
                continue
            valid_targets.add(target.strip())
        return valid_targets

    def _parseApplication(
        self,
        bundle: _models.BundleRecord,
        manifest_path: Path,
        application_data: Any,
        order: int,
        command_names: set[str],
        group_ids: set[str],
        seen_ids: set[str],
        diagnostics: list[_models.CatalogDiagnostic],
    ) -> _models.ApplicationEntry | None:
        """Validate one application entry and construct its model."""
        if not isinstance(application_data, dict):
            self._addApplicationDiagnostic(
                diagnostics, manifest_path, order, "entry must be an object"
            )
            return None
        application_id = application_data.get("id")
        name = application_data.get("name")
        command = application_data.get("command")
        if not isinstance(application_id, str) or not application_id.strip():
            self._addApplicationDiagnostic(diagnostics, manifest_path, order, "'id' is required")
            return None
        if application_id in seen_ids:
            self._addApplicationDiagnostic(
                diagnostics, manifest_path, order, f"duplicate id '{application_id}'"
            )
            return None
        seen_ids.add(application_id)
        if not isinstance(name, str) or not name.strip():
            self._addApplicationDiagnostic(diagnostics, manifest_path, order, "'name' is required")
            return None
        if not isinstance(command, str) or not command.strip():
            self._addApplicationDiagnostic(
                diagnostics, manifest_path, order, "'command' is required"
            )
            return None
        if command not in command_names:
            self._addApplicationDiagnostic(
                diagnostics,
                manifest_path,
                order,
                f"command '{command}' is not available in the active Envoy Stack",
            )
            return None
        platforms = application_data.get("platforms", [])
        if platforms and (
            not isinstance(platforms, list)
            or not all(isinstance(platform_name, str) for platform_name in platforms)
        ):
            self._addApplicationDiagnostic(
                diagnostics, manifest_path, order, "'platforms' must be an array of strings"
            )
            return None
        if platforms and self._platform_name not in {value.casefold() for value in platforms}:
            return None
        args = application_data.get("args", [])
        keywords = application_data.get("keywords", [])
        if not isinstance(args, list) or not all(isinstance(value, str) for value in args):
            self._addApplicationDiagnostic(
                diagnostics, manifest_path, order, "'args' must be an array of strings"
            )
            return None
        if not isinstance(keywords, list) or not all(isinstance(value, str) for value in keywords):
            self._addApplicationDiagnostic(
                diagnostics, manifest_path, order, "'keywords' must be an array of strings"
            )
            return None
        description = application_data.get("description", "")
        if not isinstance(description, str):
            self._addApplicationDiagnostic(
                diagnostics, manifest_path, order, "'description' must be a string"
            )
            return None
        in_terminal = application_data.get("inTerminal", False)
        if not isinstance(in_terminal, bool):
            self._addApplicationDiagnostic(
                diagnostics, manifest_path, order, "'inTerminal' must be a boolean"
            )
            return None
        group_id = ""
        declared_group = application_data.get("group")
        if declared_group is not None:
            if not isinstance(declared_group, str):
                self._addApplicationDiagnostic(
                    diagnostics, manifest_path, order, "'group' must be a string"
                )
                return None
            group_id = declared_group
            if group_id not in group_ids:
                self._addApplicationDiagnostic(
                    diagnostics,
                    manifest_path,
                    order,
                    f"group '{declared_group}' is not declared",
                )
                return None
        icon_path = self._resolveIconPath(
            bundle.root,
            application_data.get("icon"),
            manifest_path,
            order,
            diagnostics,
        )
        homepage = application_data.get("homepage", "")
        if not isinstance(homepage, str):
            self._addApplicationDiagnostic(
                diagnostics, manifest_path, order, "'homepage' must be a string"
            )
            return None
        homepage = homepage.strip()
        if homepage and not homepage.casefold().startswith(("http://", "https://")):
            self._addApplicationDiagnostic(
                diagnostics, manifest_path, order, "'homepage' must be an http(s) URL"
            )
            return None
        return _models.ApplicationEntry(
            stable_id=f"{bundle.bundle_id}:{application_id}",
            application_id=application_id,
            bundle_id=bundle.bundle_id,
            name=name.strip(),
            command=command,
            args=tuple(args),
            description=description.strip(),
            icon_path=icon_path,
            group_id=group_id,
            keywords=tuple(keywords),
            in_terminal=in_terminal,
            order=order,
            source_path=manifest_path,
            homepage=homepage,
        )

    def _resolveIconPath(
        self,
        bundle_root: Path,
        icon_value: Any,
        manifest_path: Path,
        order: int,
        diagnostics: list[_models.CatalogDiagnostic],
    ) -> Path | None:
        """Resolve a safe icon path below the bundle's resources/icons directory."""
        if icon_value is None:
            return None
        if not isinstance(icon_value, str) or not icon_value.strip():
            self._addApplicationDiagnostic(
                diagnostics, manifest_path, order, "'icon' must be a non-empty string"
            )
            return None
        root = (bundle_root / _constants.RESOURCE_ICONS_DIRECTORY).resolve()
        candidate = (root / icon_value).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            self._addApplicationDiagnostic(
                diagnostics,
                manifest_path,
                order,
                "icon path escapes the bundle resources/icons directory",
            )
            return None
        if not candidate.is_file():
            self._addApplicationDiagnostic(
                diagnostics,
                manifest_path,
                order,
                f"icon file does not exist under resources/icons: {icon_value}",
            )
            return None
        return candidate

    @staticmethod
    def _addApplicationDiagnostic(
        diagnostics: list[_models.CatalogDiagnostic],
        manifest_path: Path,
        order: int,
        message: str,
    ) -> None:
        """Append a consistently formatted application diagnostic."""
        diagnostics.append(
            _models.CatalogDiagnostic(
                "warning", f"Application entry {order + 1}: {message}", manifest_path
            )
        )
