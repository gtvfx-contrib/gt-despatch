"""Qt application coordinator for Despatch."""

from __future__ import annotations

import dataclasses
import logging
import os
import sys
import webbrowser
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any

from Qt import QtCore, QtWidgets

from . import (
    _catalog,
    _constants,
    _documentation,
    _envoy_gateway,
    _error_dialog,
    _main_window,
    _models,
    _platform,
    _settings,
    _settings_dialog,
    _single_instance,
    _stack_monitor,
    _theme,
    _tray_icon,
)

_LOG = logging.getLogger("despatch.application")


@dataclasses.dataclass(frozen=True, slots=True)
class _CatalogLoadResult:
    """Complete catalog state plus its explicit Stack-file baseline."""

    stacks: tuple[_models.NamedStack, ...]
    stack_state: _models.StackState
    snapshot: _models.CatalogSnapshot
    stack_file_state: _models.StackFileState | None


def _isDevModeEnabled() -> bool:
    """Return whether Despatch should default to Automatic resolution."""
    return os.environ.get("ENVOY_DEV_MODE", "").strip() == "1"


class DespatchApplication(QtCore.QObject):
    """Coordinate the window, tray, Envoy services, and background work."""

    def __init__(
        self,
        application: QtWidgets.QApplication,
        single_instance: _single_instance.SingleInstance,
        settings_path: Path | str | None = None,
        gateway: _envoy_gateway.EnvoyGateway | None = None,
    ):
        super().__init__()
        self._application = application
        self._single_instance = single_instance
        self._settings = _settings.SettingsStore(settings_path)
        self._gateway = gateway or _envoy_gateway.EnvoyGateway()
        self._catalog_loader = _catalog.CatalogLoader(self._gateway)
        self._window = _main_window.MainWindow()
        self._tray_icon = _tray_icon.DespatchTrayIcon(self)
        self._stack_monitor = _stack_monitor.StackMonitor(
            self._gateway.getStackFileState,
            self._settings.stack_refresh_interval_seconds,
            parent=self,
        )
        self._autostart = _platform.AutostartService(self._settings.settings_path.parent)
        self._global_shortcut = _platform.WindowsGlobalShortcut(application)
        self._executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="despatch")
        self._pending: list[
            tuple[Future, Callable[[Any], None], Callable[[BaseException], None]]
        ] = []
        self._poll_timer = QtCore.QTimer(self)
        self._poll_timer.setInterval(50)
        self._poll_timer.timeout.connect(self._pollFutures)
        self._poll_timer.start()
        self._stack_state = _models.StackState(_models.StackMode.PROMPT)
        self._snapshot = _models.CatalogSnapshot(self._stack_state, (), (), ())
        self._applications: dict[str, _models.ApplicationEntry] = {}
        self._stacks: tuple[_models.NamedStack, ...] = ()
        self._dev_mode = _isDevModeEnabled()
        self._automatic_resolution_requested = self._dev_mode
        self._launching: set[str] = set()
        self._error_dialogs: list[_error_dialog.ErrorDialog] = []
        self._catalog_refresh_active = False
        self._catalog_refresh_queued = False
        self._state_generation = 0
        self._quitting = False
        self._connectSignals()

    @property
    def window(self) -> _main_window.MainWindow:
        """Primary launcher window."""
        return self._window

    def start(self, popup: bool = False) -> None:
        """Start the tray application and load its first catalog.

        Args:
            popup: Show the main launcher immediately.

        """
        _theme.applyTheme(self._application, self._settings.theme)
        self._restoreGeometry()
        try:
            envoy_version = self._gateway.preflight()
            _LOG.info("Using Envoy %s with Python %s", envoy_version, sys.version.split()[0])
        except _envoy_gateway.EnvoyUnavailableError as error:
            self._window.setError(str(error))
            _LOG.error("Envoy preflight failed: %s", error)
        self._configureGlobalShortcut(
            self._settings.global_shortcut_enabled,
            self._settings.global_shortcut,
            report_error=False,
        )
        self._tray_icon.show()
        self.refreshCatalog()
        if popup:
            self.showWindow()

    def showWindow(self) -> None:
        """Show and focus the main launcher."""
        self._window.showLauncher()

    def refreshCatalog(self) -> None:
        """Refresh Stacks and application manifests asynchronously."""
        self._requestCatalogRefresh("manual")

    def quit(self) -> None:
        """Persist window state and stop the tray process."""
        if self._quitting:
            return
        self._quitting = True
        try:
            geometry = bytes(self._window.saveGeometry().toBase64()).decode("ascii")
            self._settings.setWindowGeometry(geometry)
        except (OSError, TypeError, ValueError) as error:
            _LOG.warning("Unable to save window geometry: %s", error)
        self._global_shortcut.unregister()
        self._stack_monitor.stop()
        self._single_instance.close()
        self._tray_icon.hide()
        self._window.allowClose()
        self._window.close()
        self._poll_timer.stop()
        self._executor.shutdown(wait=False, cancel_futures=True)
        self._application.quit()

    def _connectSignals(self) -> None:
        """Connect view requests to coordinator operations."""
        self._single_instance.activationRequested.connect(self.showWindow)
        self._global_shortcut.activated.connect(self.showWindow)

        self._window.launchRequested.connect(self._launchApplication)
        self._window.favoriteToggleRequested.connect(self._toggleFavorite)
        self._window.copyRequested.connect(self._copyCommand)
        self._window.homepageRequested.connect(self._openApplicationHomepage)
        self._window.historyClearRequested.connect(self._clearApplicationHistory)
        self._window.hideUnusedToggled.connect(self._setHideUnusedApplications)
        self._window.stackRequested.connect(self._switchStack)
        self._window.customStackRequested.connect(self._chooseCustomStack)
        self._window.documentationRequested.connect(self._openDocumentation)
        self._window.settingsRequested.connect(self._showSettings)

        self._tray_icon.showRequested.connect(self.showWindow)
        self._tray_icon.launchRequested.connect(self._launchApplication)
        self._tray_icon.stackRequested.connect(self._switchStack)
        self._tray_icon.customStackRequested.connect(self._chooseCustomStack)
        self._tray_icon.refreshRequested.connect(self.refreshCatalog)
        self._tray_icon.documentationRequested.connect(self._openDocumentation)
        self._tray_icon.settingsRequested.connect(self._showSettings)
        self._tray_icon.quitRequested.connect(self.quit)
        self._stack_monitor.changeDetected.connect(self._onStackFileChanged)
        self._stack_monitor.warningChanged.connect(self._onStackMonitorWarning)

    def _requestCatalogRefresh(self, trigger: str) -> None:
        """Start or coalesce a background catalog refresh.

        Args:
            trigger: ``manual`` for startup/tray refreshes or ``monitor`` for
                an automatically detected Stack update.

        """
        if self._catalog_refresh_active:
            if trigger != "monitor":
                self._catalog_refresh_queued = True
            return
        self._catalog_refresh_active = True
        self._tray_icon.setRefreshing(True)
        request_generation = self._state_generation
        if trigger == "monitor":
            self._window.setReady("Stack update detected; refreshing…")
        else:
            self._stack_monitor.suspend()
            self._window.setLoading("Refreshing Envoy catalog…")
        self._submit(
            self._loadState,
            lambda result: self._onStateLoaded(result, trigger, request_generation),
            lambda error: self._onCatalogRefreshError(error, trigger, request_generation),
        )

    def _loadState(self) -> _CatalogLoadResult:
        """Load all state required for an atomic UI refresh."""
        stacks = self._gateway.listStacks()
        stack_state = self._gateway.getStackState(self._automatic_resolution_requested)
        stack_file_state = None
        if stack_state.mode == _models.StackMode.EXPLICIT and stack_state.selection is not None:
            try:
                stack_file_state = self._gateway.getStackFileState(
                    stack_state.selection,
                    resolve_registered=False,
                )
            except _envoy_gateway.EnvoyUnavailableError as error:
                _LOG.warning("Unable to capture initial Stack file state: %s", error)
        snapshot = self._catalog_loader.loadCatalog(stack_state)
        return _CatalogLoadResult(stacks, stack_state, snapshot, stack_file_state)

    def _onStateLoaded(
        self,
        result: _CatalogLoadResult,
        trigger: str,
        request_generation: int,
    ) -> None:
        """Atomically apply refreshed Envoy state."""
        if request_generation != self._state_generation:
            self._finishCatalogRefresh()
            return
        self._stacks = result.stacks
        self._stack_state = result.stack_state
        self._snapshot = result.snapshot
        self._applications = self._snapshot.applicationMap()
        self._window.setStacks(self._stacks, self._stack_state)
        self._refreshViews()
        self._configureStackMonitor(result.stack_file_state)
        diagnostic_count = len(self._snapshot.diagnostics)
        if diagnostic_count:
            suffix = "issue" if diagnostic_count == 1 else "issues"
            self._window.setReady(f"Loaded with {diagnostic_count} manifest {suffix}")
            for diagnostic in self._snapshot.diagnostics:
                _LOG.warning("%s: %s", diagnostic.source_path or "catalog", diagnostic.message)
        elif trigger == "monitor":
            self._window.showTransientStatus(
                "Stack updated automatically",
                _constants.STACK_UPDATE_STATUS_SECONDS,
            )
        else:
            self._window.setReady()
        self._finishCatalogRefresh()

    def _onCatalogRefreshError(
        self,
        error: BaseException,
        trigger: str,
        request_generation: int,
    ) -> None:
        """Handle catalog failures according to their initiating surface."""
        if request_generation != self._state_generation:
            self._finishCatalogRefresh()
            return
        if trigger == "monitor":
            message = str(error) or error.__class__.__name__
            self._stack_monitor.resumeAfterReloadFailure(message)
            self._window.setReady()
            _LOG.error(
                "Automatic Stack reingestion failed",
                exc_info=(type(error), error, error.__traceback__),
            )
        else:
            self._stack_monitor.resume()
            self._onCatalogError(error)
        self._finishCatalogRefresh()

    def _finishCatalogRefresh(self) -> None:
        """Release the refresh guard and run one coalesced manual refresh."""
        self._catalog_refresh_active = False
        if not self._catalog_refresh_queued:
            self._tray_icon.setRefreshing(False)
            return
        self._catalog_refresh_queued = False
        QtCore.QTimer.singleShot(0, self.refreshCatalog)

    def _configureStackMonitor(
        self,
        stack_file_state: _models.StackFileState | None,
    ) -> None:
        """Enable monitoring only for an explicit named or custom Stack."""
        selection = self._stack_state.selection
        if self._stack_state.mode != _models.StackMode.EXPLICIT or selection is None:
            self._stack_monitor.disable()
            return
        self._stack_monitor.configure(
            selection,
            stack_file_state,
            self._settings.stack_refresh_interval_seconds,
        )

    def _onStackFileChanged(self, stack_file_state: _models.StackFileState) -> None:
        """Reingest a confirmed changed Stack without blocking interaction."""
        _LOG.info("Stack update detected at %s", stack_file_state.path)
        self._requestCatalogRefresh("monitor")

    def _onStackMonitorWarning(
        self,
        warning: _stack_monitor.StackMonitorWarning | None,
    ) -> None:
        """Reflect Stack-monitor health independently from catalog status."""
        if warning is None:
            self._window.setStackMonitorWarning()
            return
        self._window.setStackMonitorWarning(warning.message, warning.details)

    def _onCatalogError(self, error: BaseException) -> None:
        """Preserve prior state and surface a refresh failure."""
        message = str(error) or error.__class__.__name__
        self._window.setError(message)
        self._tray_icon.showMessage("Despatch could not refresh", message)
        _LOG.error("Catalog refresh failed", exc_info=(type(error), error, error.__traceback__))

    def _switchStack(self, stack_value: str | None) -> None:
        """Persist an explicit Stack or enable Automatic resolution."""
        if stack_value is None and self._stack_state.mode == _models.StackMode.AUTOMATIC:
            return
        selection = self._stack_state.selection
        if (
            stack_value is not None
            and selection is not None
            and stack_value == selection.persisted_value
        ):
            return
        label = stack_value or "Automatic resolution"
        self._state_generation += 1
        self._stack_monitor.suspend()
        self._window.setLoading(f"Switching to {label}…")

        def on_success(unused_result: Any) -> None:
            self._automatic_resolution_requested = True if stack_value is None else self._dev_mode
            self.refreshCatalog()

        operation = (
            self._gateway.clearStack
            if stack_value is None
            else lambda: self._gateway.switchStack(stack_value)
        )
        self._submit(
            operation,
            on_success,
            self._onStackSwitchError,
        )

    def _chooseCustomStack(self) -> None:
        """Prompt for a custom `.estack` file and request its activation."""
        selection = self._stack_state.selection
        initial_directory = ""
        if selection is not None:
            initial_directory = str(selection.path.parent)
        stack_path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self._window,
            "Choose an Envoy Stack",
            initial_directory,
            "Envoy Stack (*.estack)",
        )
        if stack_path:
            self._switchStack(stack_path)

    def _onStackSwitchError(self, error: BaseException) -> None:
        """Restore the prior Stack selector state after a failed switch."""
        self._stack_monitor.resume()
        self._window.setStacks(self._stacks, self._stack_state)
        self._refreshViews()
        self._onCatalogError(error)

    def _launchApplication(self, stable_id: str, force_terminal: bool) -> None:
        """Prepare and spawn a catalog application asynchronously."""
        application = self._applications.get(stable_id)
        if application is None or stable_id in self._launching:
            return
        if force_terminal and not application.in_terminal:
            application = dataclasses.replace(application, in_terminal=True)
        self._launching.add(stable_id)
        self._window.setReady(f"Starting {application.name}…")

        def on_spawned(unused_process: Any) -> None:
            self._launching.discard(stable_id)
            self._settings.recordLaunch(stable_id)
            self._refreshViews()
            self._window.setReady(f"Started {application.name}")
            if not self._settings.keep_open_after_launch:
                self._window.hide()

        def on_error(error: BaseException) -> None:
            self._launching.discard(stable_id)
            self._window.setError(f"Could not start {application.name}: {error}")
            self.showWindow()
            self._showErrorDialog(
                f"Could not start {application.name}",
                str(error) or error.__class__.__name__,
            )

        stack_state = self._stack_state
        self._submit(
            lambda: self._gateway.spawnApplication(application, stack_state),
            on_spawned,
            on_error,
        )

    def _toggleFavorite(self, stable_id: str) -> None:
        """Toggle a favorite and refresh both launch surfaces."""
        if stable_id not in self._applications:
            return
        self._settings.toggleFavorite(stable_id)
        self._refreshViews()

    def _clearApplicationHistory(self, stable_id: str) -> None:
        """Remove one application's launch history and refresh both surfaces."""
        if stable_id not in self._applications:
            return
        self._settings.clearLaunchHistory(stable_id)
        self._refreshViews()

    def _setHideUnusedApplications(self, hidden: bool) -> None:
        """Persist the hide-unused-applications filter and refresh the catalog."""
        if hidden == self._settings.hide_unused_applications:
            return
        self._settings.setHideUnusedApplications(hidden)
        self._refreshViews()

    def _copyCommand(self, stable_id: str) -> None:
        """Copy a platform-quoted Envoy command to the clipboard."""
        application = self._applications.get(stable_id)
        if application is None:
            return
        self._application.clipboard().setText(
            self._gateway.formatCommand(application, self._stack_state)
        )
        self._window.setReady("Envoy command copied")

    def _openApplicationHomepage(self, stable_id: str) -> None:
        """Open an application's homepage without blocking the Qt thread."""
        application = self._applications.get(stable_id)
        if application is None or not application.homepage:
            return

        def on_success(unused_result: bool) -> None:
            self._window.setReady(f"Opened {application.name} homepage in your browser")

        def on_error(error: BaseException) -> None:
            message = str(error) or error.__class__.__name__
            self._window.setError(f"Could not open homepage: {message}")
            self._showErrorDialog("Homepage could not be opened", message)

        self._submit(
            lambda: webbrowser.open(application.homepage, new=2),
            on_success,
            on_error,
        )

    def _showSettings(self) -> None:
        """Show settings and apply accepted changes transactionally."""
        dialog = _settings_dialog.SettingsDialog(
            self._settings,
            autostart_supported=self._autostart.is_supported,
            shortcut_supported=sys.platform == "win32",
            parent=self._window,
        )
        if dialog.exec_() != QtWidgets.QDialog.Accepted:
            return
        values = dialog.values()
        old_autostart = self._settings.autostart
        old_shortcut_enabled = self._settings.global_shortcut_enabled
        old_shortcut = self._settings.global_shortcut
        old_stack_refresh_interval = self._settings.stack_refresh_interval_seconds
        try:
            requested_autostart = bool(values["autostart"])
            if requested_autostart != old_autostart:
                self._autostart.setEnabled(requested_autostart)
            self._configureGlobalShortcut(
                bool(values["global_shortcut_enabled"]),
                str(values["global_shortcut"]),
                report_error=True,
            )
            self._settings.updatePreferences(**values)
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            if bool(values["autostart"]) != old_autostart:
                try:
                    self._autostart.setEnabled(old_autostart)
                except (OSError, RuntimeError):
                    _LOG.exception("Unable to roll back autostart state")
            self._configureGlobalShortcut(
                old_shortcut_enabled,
                old_shortcut,
                report_error=False,
            )
            self._showErrorDialog("Settings could not be saved", str(error))
            return
        _theme.applyTheme(self._application, self._settings.theme)
        if self._settings.stack_refresh_interval_seconds != old_stack_refresh_interval:
            self._stack_monitor.setInterval(self._settings.stack_refresh_interval_seconds)
        self._refreshViews()

    def _openDocumentation(self) -> None:
        """Open the Despatch documentation without blocking the Qt thread."""

        def on_success(unused_url: str) -> None:
            self._window.setReady("Documentation opened in your browser")

        def on_error(error: BaseException) -> None:
            message = str(error) or error.__class__.__name__
            self._window.setError(f"Could not open documentation: {message}")
            self._showErrorDialog("Documentation could not be opened", message)

        self._submit(_documentation.openDocumentation, on_success, on_error)

    def _configureGlobalShortcut(
        self,
        enabled: bool,
        shortcut: str,
        *,
        report_error: bool,
    ) -> bool:
        """Apply the requested global shortcut registration."""
        try:
            self._global_shortcut.unregister()
            if enabled:
                self._global_shortcut.register(shortcut)
            return True
        except RuntimeError as error:
            if report_error:
                raise
            _LOG.warning("Global shortcut unavailable: %s", error)
            return False

    def _refreshViews(self) -> None:
        """Refresh main and tray views from one state snapshot."""
        self._window.setCatalog(
            self._snapshot,
            self._settings.favorites,
            self._settings.recent_applications,
            self._settings.hide_unused_applications,
        )
        self._tray_icon.setState(
            self._snapshot,
            self._stacks,
            self._stack_state,
            self._settings.favorites,
        )

    def _showErrorDialog(self, title: str, message: str, output: str = "") -> None:
        """Create a retained non-modal error dialog."""
        dialog = _error_dialog.ErrorDialog(title, message, output, self._window)
        self._error_dialogs.append(dialog)
        dialog.destroyed.connect(
            lambda unused=None, error_dialog=dialog: self._forgetDialog(error_dialog)
        )
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _forgetDialog(self, dialog: _error_dialog.ErrorDialog) -> None:
        """Release a deleted non-modal error dialog."""
        if dialog in self._error_dialogs:
            self._error_dialogs.remove(dialog)

    def _restoreGeometry(self) -> None:
        """Restore persisted Qt geometry when it is valid."""
        if not self._settings.window_geometry:
            return
        try:
            encoded = self._settings.window_geometry.encode("ascii")
            geometry = QtCore.QByteArray.fromBase64(encoded)
            self._window.restoreGeometry(geometry)
        except (TypeError, ValueError):
            _LOG.warning("Ignoring invalid persisted window geometry")

    def _submit(
        self,
        function: Callable[[], Any],
        on_success: Callable[[Any], None],
        on_error: Callable[[BaseException], None],
    ) -> None:
        """Submit background work for polling on the Qt thread."""
        future = self._executor.submit(function)
        self._pending.append((future, on_success, on_error))

    def _pollFutures(self) -> None:
        """Dispatch completed future callbacks on the Qt main thread."""
        for pending in tuple(self._pending):
            future, on_success, on_error = pending
            if not future.done():
                continue
            self._pending.remove(pending)
            try:
                result = future.result()
            except BaseException as error:
                on_error(error)
            else:
                on_success(result)
