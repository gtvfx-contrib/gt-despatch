"""Boundary between Despatch and the Envoy Python API."""

from __future__ import annotations

import importlib
import json
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path
from types import ModuleType
from typing import Any

from . import _models


class EnvoyUnavailableError(RuntimeError):
    """Raised when the Envoy Python API cannot be loaded or queried."""


class StackSwitchError(RuntimeError):
    """Raised when an Envoy Stack cannot be activated."""


class EnvoyGateway:
    """Discover Stacks and dispatch applications through Envoy.

    Args:
        envoy_module: Optional module override used by tests.

    """

    def __init__(self, envoy_module: ModuleType | Any | None = None):
        self._envoy_module = envoy_module

    def preflight(self) -> str:
        """Validate the Envoy Python API and return its version.

        Returns:
            Envoy version string.

        Raises:
            EnvoyUnavailableError: If the module or required API is unavailable.

        """
        envoy_module = self._getEnvoyModule()
        required_names = (
            "Stack",
            "discoverBundlesAuto",
            "getConfigRoot",
            "isStackName",
            "listNamedStacks",
            "loadUserConfig",
            "proc",
            "resolveNamedStack",
        )
        missing = [name for name in required_names if not hasattr(envoy_module, name)]
        if missing:
            joined_names = ", ".join(missing)
            raise EnvoyUnavailableError(f"Envoy Python API is missing: {joined_names}")
        return str(getattr(envoy_module, "__version__", "unknown"))

    def listStacks(self) -> tuple[_models.NamedStack, ...]:
        """Return published named Stacks.

        Returns:
            Stacks sorted by name.

        """
        try:
            entries = self._getEnvoyModule().listNamedStacks()
            stacks = [
                _models.NamedStack(
                    name=str(entry.name),
                    version=str(entry.version),
                    path=self._canonicalPath(entry.path),
                )
                for entry in entries
            ]
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as error:
            raise EnvoyUnavailableError(f"Unable to list Envoy Stacks: {error}") from error
        return tuple(sorted(stacks, key=lambda entry: entry.name.casefold()))

    def getStackState(self, automatic_requested: bool) -> _models.StackState:
        """Resolve the shared Stack selection and session fallback.

        Args:
            automatic_requested: Whether this session should use automatic resolution when the
                shared user configuration has no Stack.

        Returns:
            Resolved Stack state.

        """
        try:
            user_config = self._getEnvoyModule().loadUserConfig()
            configured_value = user_config.get("stack")
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as error:
            raise EnvoyUnavailableError(
                f"Unable to read Envoy user configuration: {error}"
            ) from error
        if not configured_value:
            mode = _models.StackMode.AUTOMATIC if automatic_requested else _models.StackMode.PROMPT
            return _models.StackState(mode)
        try:
            selection = self._resolveStackSelection(str(configured_value))
        except StackSwitchError as error:
            raise EnvoyUnavailableError(str(error)) from error
        return _models.StackState(_models.StackMode.EXPLICIT, selection)

    def switchStack(self, stack_value: str) -> _models.StackSelection:
        """Validate and persist a named or custom Stack globally.

        Args:
            stack_value: Published Stack name or `.estack` filesystem path.

        Returns:
            Normalized explicit Stack selection.

        Raises:
            StackSwitchError: If resolution, validation, or persistence fails.

        """
        envoy_module = self._getEnvoyModule()
        try:
            selection = self._resolveStackSelection(stack_value)
            stack = envoy_module.Stack(selection.path)
            tuple(stack.bundles)
            user_config = envoy_module.loadUserConfig()
            user_config.set("stack", selection.persisted_value)
            user_config.save()
            return selection
        except StackSwitchError:
            raise
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as error:
            raise StackSwitchError(
                f"Unable to activate Envoy Stack '{stack_value}': {error}"
            ) from error

    def clearStack(self) -> None:
        """Clear the shared Stack setting for Automatic resolution."""
        try:
            user_config = self._getEnvoyModule().loadUserConfig()
            user_config.unset("stack")
            user_config.save()
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as error:
            raise StackSwitchError(f"Unable to enable Automatic resolution: {error}") from error

    def loadBundles(self, stack_state: _models.StackState) -> tuple[_models.BundleRecord, ...]:
        """Load bundles participating in the selected Stack mode.

        Args:
            stack_state: Resolved explicit, automatic, or prompt state.

        Returns:
            Bundle records suitable for catalog discovery.

        """
        envoy_module = self._getEnvoyModule()
        try:
            if stack_state.mode == _models.StackMode.PROMPT:
                return ()
            if stack_state.mode == _models.StackMode.AUTOMATIC:
                return tuple(
                    self._recordFromBundleInfo(bundle_info)
                    for bundle_info in envoy_module.discoverBundlesAuto()
                )
            if stack_state.selection is None:
                raise ValueError("Explicit Stack state has no selection")
            stack = envoy_module.Stack(stack_state.selection.path)
            return tuple(self._recordFromBundle(bundle) for bundle in stack.bundles)
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as error:
            raise EnvoyUnavailableError(f"Unable to load Envoy bundles: {error}") from error

    def getStackFileState(
        self,
        selection: _models.StackSelection,
        *,
        resolve_registered: bool = True,
    ) -> _models.StackFileState:
        """Return lightweight metadata for an explicit Stack selection.

        Args:
            selection: Named or custom Stack selection to inspect.
            resolve_registered: Re-resolve a named Stack's latest publication
                before reading metadata.

        Returns:
            Filesystem identity suitable for change detection.

        Raises:
            EnvoyUnavailableError: If the Stack cannot be resolved or inspected.

        """
        stack_path = selection.path
        try:
            if resolve_registered and not selection.is_custom:
                resolved_path = self._getEnvoyModule().resolveNamedStack(selection.persisted_value)
                if resolved_path is None:
                    raise ValueError(
                        f"Named Stack '{selection.persisted_value}' could not be resolved"
                    )
                stack_path = Path(resolved_path)
            file_stat = stack_path.stat()
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as error:
            raise EnvoyUnavailableError(
                f"Unable to check Envoy Stack '{selection.display_name}': {error}"
            ) from error
        return _models.StackFileState(
            path=self._canonicalPath(stack_path),
            size=file_stat.st_size,
            modified_ns=file_stat.st_mtime_ns,
            changed_ns=file_stat.st_ctime_ns,
        )

    def spawnApplication(
        self,
        application: _models.ApplicationEntry,
        stack_state: _models.StackState,
    ) -> int:
        """Dispatch an application through an isolated Envoy worker.

        Args:
            application: Catalog entry to launch.
            stack_state: Stack mode that produced the catalog.

        Returns:
            Process identifier reported by Envoy.

        """
        if stack_state.mode == _models.StackMode.PROMPT:
            raise RuntimeError("Choose a Stack before launching applications")
        stack_path = None
        if stack_state.mode == _models.StackMode.EXPLICIT:
            if stack_state.selection is None:
                raise RuntimeError("Explicit Stack state has no selection")
            stack_path = str(stack_state.selection.path)
        request = {
            "commandLine": [application.command, *application.args],
            "inTerminal": application.in_terminal,
            "stackPath": stack_path,
        }
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        with tempfile.TemporaryDirectory(prefix="despatch-launch-") as temporary_directory:
            request_path = Path(temporary_directory) / "request.json"
            result_path = Path(temporary_directory) / "result.json"
            request_path.write_text(json.dumps(request), encoding="utf-8")
            completed_process = subprocess.run(
                self._launchWorkerCommand(request_path, result_path),
                check=False,
                creationflags=creation_flags,
            )
            result = self._readLaunchResult(result_path)
        if completed_process.returncode != 0:
            message = result.get("error") or "Envoy launch worker failed"
            raise RuntimeError(str(message))
        process_id = result.get("processId")
        if not isinstance(process_id, int):
            raise RuntimeError("Envoy launch worker returned an invalid process id")
        return process_id

    @staticmethod
    def _launchWorkerCommand(request_path: Path, result_path: Path) -> list[str]:
        """Build the isolated worker command for source or frozen execution."""
        worker_arguments = [str(request_path), str(result_path)]
        if getattr(sys, "frozen", False):
            return [sys.executable, "--launch-worker", *worker_arguments]
        return [
            sys.executable,
            "-m",
            "despatch._launch_worker",
            *worker_arguments,
        ]

    @staticmethod
    def _readLaunchResult(result_path: Path) -> dict[str, Any]:
        """Read a result produced by the isolated launch worker."""
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as error:
            raise RuntimeError("Envoy launch worker did not return a valid result") from error
        if not isinstance(result, dict):
            raise RuntimeError("Envoy launch worker result must be an object")
        return result

    def formatCommand(
        self,
        application: _models.ApplicationEntry,
        stack_state: _models.StackState,
    ) -> str:
        """Format an application as a copyable Envoy command line.

        Args:
            application: Catalog entry to format.
            stack_state: Stack mode that produced the catalog.

        Returns:
            Platform-quoted command line.

        """
        command_line = ["envoy"]
        if stack_state.mode == _models.StackMode.EXPLICIT:
            if stack_state.selection is None:
                raise RuntimeError("Explicit Stack state has no selection")
            command_line.extend(("--stack", str(stack_state.selection.path)))
        command_line.extend((application.command, *application.args))
        if sys.platform == "win32":
            return subprocess.list2cmdline(command_line)
        return shlex.join(command_line)

    def _resolveStackSelection(self, stack_value: str) -> _models.StackSelection:
        """Resolve a persisted name or path to an explicit Stack selection."""
        envoy_module = self._getEnvoyModule()
        try:
            named_stacks = self.listStacks()
            if envoy_module.isStackName(stack_value):
                resolved_path = envoy_module.resolveNamedStack(stack_value)
                if resolved_path is None:
                    raise StackSwitchError(f"Envoy Stack '{stack_value}' could not be resolved")
                canonical_path = self._canonicalStackPath(resolved_path)
                named_stack = next(
                    (entry for entry in named_stacks if entry.name == stack_value),
                    None,
                )
                version = named_stack.version if named_stack is not None else ""
                return _models.StackSelection(
                    stack_value,
                    stack_value,
                    canonical_path,
                    version,
                    False,
                )

            canonical_path = self._canonicalStackPath(stack_value)
            matching_stack = next(
                (entry for entry in named_stacks if self._pathsEqual(entry.path, canonical_path)),
                None,
            )
            if matching_stack is not None:
                return _models.StackSelection(
                    matching_stack.name,
                    matching_stack.name,
                    canonical_path,
                    matching_stack.version,
                    False,
                )
            return _models.StackSelection(
                str(canonical_path),
                canonical_path.name,
                canonical_path,
                is_custom=True,
            )
        except StackSwitchError:
            raise
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as error:
            raise StackSwitchError(
                f"Unable to resolve Envoy Stack '{stack_value}': {error}"
            ) from error

    @staticmethod
    def _canonicalPath(path: Path | str) -> Path:
        """Return an absolute normalized path without requiring it to exist."""
        return Path(path).expanduser().resolve()

    @classmethod
    def _canonicalStackPath(cls, path: Path | str) -> Path:
        """Validate an `.estack` path and return its canonical absolute form."""
        canonical_path = Path(path).expanduser().resolve(strict=True)
        if canonical_path.suffix.casefold() != ".estack":
            raise StackSwitchError("Envoy Stack files must use the .estack extension")
        return canonical_path

    @staticmethod
    def _pathsEqual(first_path: Path, second_path: Path) -> bool:
        """Compare canonical paths using platform filesystem casing rules."""
        return os.path.normcase(str(first_path)) == os.path.normcase(str(second_path))

    def _getEnvoyModule(self) -> ModuleType | Any:
        """Import Envoy lazily so pure modules remain testable."""
        if self._envoy_module is not None:
            return self._envoy_module
        try:
            self._envoy_module = importlib.import_module("envoy")
        except ImportError as error:
            raise EnvoyUnavailableError(
                "The Envoy Python API is unavailable. Start Despatch with 'envoy despatch'."
            ) from error
        return self._envoy_module

    def _recordFromBundle(self, bundle: Any) -> _models.BundleRecord:
        """Convert an Envoy Bundle into a catalog record."""
        root = Path(bundle.path)
        return _models.BundleRecord(
            bundle_id=str(bundle.bndlid),
            root=root,
            envoy_directory=Path(bundle.envoy_env),
            commands=tuple(str(command) for command in bundle.commands),
            version=str(bundle.version),
            is_production=bool(bundle.is_production),
        )

    def _recordFromBundleInfo(self, bundle_info: Any) -> _models.BundleRecord:
        """Convert an auto-discovered BundleInfo into a catalog record."""
        root = Path(bundle_info.root)
        envoy_directory = Path(bundle_info.envoy_env)
        return _models.BundleRecord(
            bundle_id=str(bundle_info.bndlid),
            root=root,
            envoy_directory=envoy_directory,
            commands=self._readCommandNames(envoy_directory / "commands.json"),
        )

    @staticmethod
    def _readCommandNames(commands_path: Path) -> tuple[str, ...]:
        """Read command names for BundleInfo, which does not expose them."""
        try:
            with commands_path.open("r", encoding="utf-8") as commands_file:
                commands_data = json.load(commands_file)
        except (OSError, json.JSONDecodeError):
            return ()
        if not isinstance(commands_data, dict):
            return ()
        return tuple(str(name) for name in commands_data)
