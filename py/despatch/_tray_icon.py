"""System tray entry point and compact quick-access menu."""

from __future__ import annotations

from Qt import QtCore, QtWidgets

from . import _constants, _icons, _models


def _activationReason(reason_name: str):
    """Return a tray activation enum across Qt versions."""
    direct_value = getattr(QtWidgets.QSystemTrayIcon, reason_name, None)
    if direct_value is not None:
        return direct_value
    return getattr(QtWidgets.QSystemTrayIcon.ActivationReason, reason_name)


class DespatchTrayIcon(QtWidgets.QSystemTrayIcon):
    """Expose Despatch through asymmetric left- and right-click behavior."""

    showRequested = QtCore.Signal()
    launchRequested = QtCore.Signal(str, bool)
    stackRequested = QtCore.Signal(object)
    customStackRequested = QtCore.Signal()
    refreshRequested = QtCore.Signal()
    documentationRequested = QtCore.Signal()
    settingsRequested = QtCore.Signal()
    quitRequested = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(_icons.loadProductIcon(), parent)
        self._stack_state = _models.StackState(_models.StackMode.PROMPT)
        self._snapshot = _models.CatalogSnapshot(self._stack_state, (), (), ())
        self._stacks: tuple[_models.NamedStack, ...] = ()
        self._favorites: frozenset[str] = frozenset()
        self._refreshing = False
        self.activated.connect(self._onActivated)
        self.setToolTip(_constants.PRODUCT_DESCRIPTION)
        self.rebuildMenu()

    def setRefreshing(self, refreshing: bool) -> None:
        """Swap the tray icon to a distinct visual while a refresh runs.

        Args:
            refreshing: Whether a catalog refresh is currently in progress.

        """
        if refreshing == self._refreshing:
            return
        self._refreshing = refreshing
        self.setIcon(_icons.loadRefreshingIcon() if refreshing else _icons.loadProductIcon())
        self.setToolTip(
            f"{_constants.PRODUCT_NAME} · Refreshing…"
            if refreshing
            else f"{_constants.PRODUCT_NAME} · {self._stackLabel()}"
        )

    def setState(
        self,
        snapshot: _models.CatalogSnapshot,
        stacks: tuple[_models.NamedStack, ...],
        stack_state: _models.StackState,
        favorites: frozenset[str],
    ) -> None:
        """Update tray favorites and Stack actions."""
        self._snapshot = snapshot
        self._stacks = stacks
        self._stack_state = stack_state
        self._favorites = favorites
        if not self._refreshing:
            self.setToolTip(f"{_constants.PRODUCT_NAME} · {self._stackLabel()}")
        self.rebuildMenu()

    def rebuildMenu(self) -> None:
        """Rebuild the compact native context menu from current state."""
        existing_menu = self.contextMenu()
        if existing_menu is not None:
            existing_menu.deleteLater()
        menu = QtWidgets.QMenu()

        show_action = menu.addAction("Show Despatch")
        show_action.setIcon(_icons.loadProductIcon())
        show_action.triggered.connect(self.showRequested)

        favorite_applications = [
            application
            for application in self._snapshot.applications
            if application.stable_id in self._favorites
        ]
        if favorite_applications:
            menu.addSeparator()
            for application in favorite_applications:
                action = menu.addAction(
                    _icons.loadPathIcon(application.icon_path),
                    application.name,
                )
                action.triggered.connect(
                    lambda checked=False, stable_id=application.stable_id: (
                        self.launchRequested.emit(stable_id, False)
                    )
                )

        menu.addSeparator()
        stack_menu = menu.addMenu(self._stackLabel())
        automatic_action = stack_menu.addAction("Automatic resolution")
        automatic_action.setCheckable(True)
        automatic_action.setChecked(self._stack_state.mode == _models.StackMode.AUTOMATIC)
        automatic_action.triggered.connect(lambda checked=False: self.stackRequested.emit(None))
        if self._stacks:
            stack_menu.addSeparator()
        selection = self._stack_state.selection
        for stack in self._stacks:
            action = stack_menu.addAction(stack.name)
            action.setCheckable(True)
            action.setChecked(
                selection is not None
                and not selection.is_custom
                and stack.name == selection.persisted_value
            )
            action.triggered.connect(
                lambda checked=False, stack_name=stack.name: self.stackRequested.emit(stack_name)
            )
        if selection is not None and selection.is_custom:
            stack_menu.addSeparator()
            custom_action = stack_menu.addAction(selection.display_name)
            custom_action.setCheckable(True)
            custom_action.setChecked(True)
            custom_action.setToolTip(str(selection.path))
        stack_menu.addSeparator()
        browse_action = stack_menu.addAction("Choose custom Stack…")
        browse_action.triggered.connect(self.customStackRequested)

        refresh_action = menu.addAction("Refresh")
        refresh_action.triggered.connect(self.refreshRequested)
        documentation_action = menu.addAction("Documentation")
        documentation_action.triggered.connect(self.documentationRequested)
        settings_action = menu.addAction("Settings")
        settings_action.triggered.connect(self.settingsRequested)
        menu.addSeparator()
        quit_action = menu.addAction("Quit")
        quit_action.triggered.connect(self.quitRequested)
        self.setContextMenu(menu)

    def _stackLabel(self) -> str:
        """Return the compact label for the active Stack mode."""
        if self._stack_state.mode == _models.StackMode.PROMPT:
            return "Choose a Stack…"
        if self._stack_state.mode == _models.StackMode.AUTOMATIC:
            return "Automatic resolution"
        if self._stack_state.selection is not None:
            return self._stack_state.selection.display_name
        return "Choose a Stack…"

    def _onActivated(self, reason) -> None:
        """Show the main UI only for a left-click activation."""
        if reason == _activationReason("Trigger"):
            self.showRequested.emit()
