"""Frameless search-first Despatch launcher window."""

from __future__ import annotations

from Qt import QtCore, QtGui, QtWidgets

from . import _constants, _icons, _models, _search

_APPLICATION_ROLE = QtCore.Qt.UserRole  # type: ignore
_SECTION_ROLE = QtCore.Qt.UserRole + 1  # type: ignore


def _globalPoint(event) -> QtCore.QPoint:
    """Return a mouse event's global point across supported Qt versions."""
    if hasattr(event, "globalPosition"):
        return event.globalPosition().toPoint()
    return event.globalPos()


class TitleBar(QtWidgets.QWidget):
    """Custom title bar for the frameless launcher."""

    closeRequested = QtCore.Signal()
    minimizeRequested = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._drag_offset: QtCore.QPoint | None = None
        self.setFixedHeight(44)

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(14, 6, 8, 4)
        layout.setSpacing(6)

        icon_label = QtWidgets.QLabel()
        icon_label.setPixmap(_icons.loadProductIcon().pixmap(22, 22))
        layout.addWidget(icon_label)

        title_label = QtWidgets.QLabel(_constants.PRODUCT_NAME)
        title_label.setObjectName("productTitle")
        layout.addWidget(title_label)
        layout.addStretch(1)

        minimize_button = QtWidgets.QToolButton()
        minimize_button.setObjectName("windowButton")
        minimize_button.setText("—")
        minimize_button.setToolTip("Minimize")
        minimize_button.clicked.connect(self.minimizeRequested)
        layout.addWidget(minimize_button)

        close_button = QtWidgets.QToolButton()
        close_button.setObjectName("windowButton")
        close_button.setText("×")
        close_button.setToolTip("Hide to tray")
        close_button.clicked.connect(self.closeRequested)
        layout.addWidget(close_button)

    def mousePressEvent(self, event) -> None:
        """Begin a window drag from the custom title bar."""
        if event.button() == QtCore.Qt.LeftButton:  # type: ignore
            window_handle = self.window().windowHandle()
            if (
                window_handle is not None
                and hasattr(window_handle, "startSystemMove")
                and window_handle.startSystemMove()
            ):
                event.accept()
                return
            self._drag_offset = _globalPoint(event) - self.window().frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        """Move the window during a fallback title-bar drag."""
        if self._drag_offset is not None and event.buttons() & QtCore.Qt.LeftButton:  # type: ignore
            self.window().move(_globalPoint(event) - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        """Finish a fallback title-bar drag."""
        self._drag_offset = None
        super().mouseReleaseEvent(event)


class MainWindow(QtWidgets.QMainWindow):
    """Primary application browser and launcher."""

    launchRequested = QtCore.Signal(str, bool)
    favoriteToggleRequested = QtCore.Signal(str)
    copyRequested = QtCore.Signal(str)
    homepageRequested = QtCore.Signal(str)
    historyClearRequested = QtCore.Signal(str)
    hideUnusedToggled = QtCore.Signal(bool)
    stackRequested = QtCore.Signal(object)
    customStackRequested = QtCore.Signal()
    documentationRequested = QtCore.Signal()
    settingsRequested = QtCore.Signal()

    def __init__(self):
        super().__init__()
        stack_state = _models.StackState(_models.StackMode.PROMPT)
        self._snapshot = _models.CatalogSnapshot(stack_state, (), (), ())
        self._application_map: dict[str, _models.ApplicationEntry] = {}
        self._favorites: frozenset[str] = frozenset()
        self._recent_applications: tuple[str, ...] = ()
        self._hide_unused = False
        self._allow_close = False
        self._populating_stacks = False
        self._active_stack_index = 0
        self._custom_picker_index = -1
        self._status_generation = 0
        self._transient_status_generation: int | None = None
        self._transient_status_timer = QtCore.QTimer(self)
        self._transient_status_timer.setSingleShot(True)
        self._transient_status_timer.timeout.connect(self._clearTransientStatus)

        window_flags = QtCore.Qt.Window | QtCore.Qt.FramelessWindowHint  # type: ignore
        self.setWindowFlags(window_flags)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)  # type: ignore
        self.setWindowTitle(_constants.PRODUCT_NAME)
        self.setWindowIcon(_icons.loadProductIcon())
        self.setMinimumSize(
            _constants.MINIMUM_WINDOW_WIDTH,
            _constants.MINIMUM_WINDOW_HEIGHT,
        )
        self.resize(_constants.DEFAULT_WINDOW_WIDTH, _constants.DEFAULT_WINDOW_HEIGHT)
        self._setupUi()
        self._connectSignals()

    def _setupUi(self) -> None:
        """Build the launcher interface."""
        outer_widget = QtWidgets.QWidget()
        outer_layout = QtWidgets.QVBoxLayout(outer_widget)
        outer_layout.setContentsMargins(14, 14, 14, 14)

        shell = QtWidgets.QFrame()
        shell.setObjectName("windowShell")
        shadow = QtWidgets.QGraphicsDropShadowEffect(shell)
        shadow.setBlurRadius(32)
        shadow.setOffset(0, 8)
        shadow.setColor(QtGui.QColor(0, 0, 0, 90))
        shell.setGraphicsEffect(shadow)
        outer_layout.addWidget(shell)
        self.setCentralWidget(outer_widget)

        shell_layout = QtWidgets.QVBoxLayout(shell)
        shell_layout.setContentsMargins(0, 0, 0, 10)
        shell_layout.setSpacing(8)

        self._title_bar = TitleBar()
        shell_layout.addWidget(self._title_bar)

        content = QtWidgets.QWidget()
        content_layout = QtWidgets.QVBoxLayout(content)
        content_layout.setContentsMargins(16, 0, 16, 8)
        content_layout.setSpacing(10)
        shell_layout.addWidget(content, 1)

        toolbar = QtWidgets.QHBoxLayout()
        self._stack_combo = QtWidgets.QComboBox()
        self._stack_combo.setMinimumWidth(220)
        self._stack_combo.setToolTip("Active Envoy Stack")
        toolbar.addWidget(self._stack_combo, 1)

        self._hide_unused_button = QtWidgets.QToolButton()
        self._hide_unused_button.setText("Hide unused")
        self._hide_unused_button.setToolTip("Hide applications with no favorite or launch history")
        self._hide_unused_button.setCheckable(True)
        toolbar.addWidget(self._hide_unused_button)

        self._documentation_button = QtWidgets.QToolButton()
        self._documentation_button.setText("?")
        self._documentation_button.setToolTip("Documentation")
        self._documentation_button.setAccessibleName("Documentation")
        toolbar.addWidget(self._documentation_button)

        self._settings_button = QtWidgets.QToolButton()
        self._settings_button.setText("⚙")
        self._settings_button.setToolTip("Settings")
        toolbar.addWidget(self._settings_button)
        content_layout.addLayout(toolbar)

        self._stack_health_label = QtWidgets.QLabel()
        self._stack_health_label.setObjectName("warningLabel")
        self._stack_health_label.setWordWrap(True)
        self._stack_health_label.setVisible(False)
        content_layout.addWidget(self._stack_health_label)

        self._search_input = QtWidgets.QLineEdit()
        self._search_input.setPlaceholderText("Search applications…")
        self._search_input.setClearButtonEnabled(True)
        content_layout.addWidget(self._search_input)

        self._status_label = QtWidgets.QLabel("Loading Envoy catalog…")
        self._status_label.setObjectName("mutedLabel")
        self._status_label.setWordWrap(True)
        content_layout.addWidget(self._status_label)

        self._application_list = QtWidgets.QListWidget()
        self._application_list.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)  # type: ignore
        self._application_list.setIconSize(QtCore.QSize(34, 34))
        self._application_list.setSpacing(2)
        self._application_list.setUniformItemSizes(False)
        content_layout.addWidget(self._application_list, 1)

        size_grip_layout = QtWidgets.QHBoxLayout()
        size_grip_layout.addStretch(1)
        size_grip_layout.addWidget(QtWidgets.QSizeGrip(self))
        content_layout.addLayout(size_grip_layout)

    def _connectSignals(self) -> None:
        """Connect all UI events."""
        self._title_bar.closeRequested.connect(self.hide)
        self._title_bar.minimizeRequested.connect(self.showMinimized)
        self._documentation_button.clicked.connect(self.documentationRequested)
        self._settings_button.clicked.connect(self.settingsRequested)
        self._hide_unused_button.toggled.connect(self.hideUnusedToggled)
        self._stack_combo.currentIndexChanged.connect(self._onStackChanged)
        self._search_input.textChanged.connect(self._populateApplications)
        self._search_input.returnPressed.connect(self._launchFirstVisible)
        self._application_list.itemClicked.connect(self._onItemClicked)
        self._application_list.itemActivated.connect(self._onItemClicked)
        self._application_list.customContextMenuRequested.connect(self._showApplicationMenu)

        focus_shortcut = QtGui.QShortcut(QtGui.QKeySequence("Ctrl+F"), self)
        focus_shortcut.activated.connect(self.focusSearch)
        escape_shortcut = QtGui.QShortcut(QtGui.QKeySequence("Escape"), self)
        escape_shortcut.activated.connect(self.hide)

    def setStacks(
        self,
        stacks: tuple[_models.NamedStack, ...],
        stack_state: _models.StackState,
    ) -> None:
        """Populate the Envoy Stack selector.

        Args:
            stacks: Available published Stacks.
            stack_state: Current prompt, Automatic, or explicit Stack state.

        """
        self._populating_stacks = True
        self._stack_combo.clear()
        self._stack_combo.addItem("Choose a Stack…", "")
        prompt_item = self._stack_combo.model().item(0)
        if prompt_item is not None:
            prompt_item.setEnabled(False)
        self._stack_combo.addItem("Automatic resolution", None)
        for stack in stacks:
            label = stack.name
            if stack.version:
                label = f"{label}  ·  {stack.version}"
            self._stack_combo.addItem(label, stack.name)

        selection = stack_state.selection
        if stack_state.mode == _models.StackMode.PROMPT:
            active_index = 0
        elif stack_state.mode == _models.StackMode.AUTOMATIC:
            active_index = 1
        elif selection is not None and selection.is_custom:
            self._stack_combo.addItem(selection.display_name, selection.persisted_value)
            active_index = self._stack_combo.count() - 1
            self._stack_combo.setItemData(
                active_index,
                str(selection.path),
                QtCore.Qt.ToolTipRole,  # type: ignore
            )
        elif selection is not None:
            active_index = self._stack_combo.findData(selection.persisted_value)
            active_index = max(active_index, 0)
        else:
            active_index = 0

        self._custom_picker_index = self._stack_combo.count()
        self._stack_combo.addItem("Choose custom Stack…")
        self._active_stack_index = active_index
        self._stack_combo.setCurrentIndex(active_index)
        is_custom = bool(selection is not None and selection.is_custom)
        self._stack_combo.setProperty("customStack", is_custom)
        self._stack_combo.setToolTip(
            str(selection.path) if is_custom and selection is not None else "Active Envoy Stack"
        )
        self._stack_combo.style().unpolish(self._stack_combo)
        self._stack_combo.style().polish(self._stack_combo)
        self._populating_stacks = False

    def setCatalog(
        self,
        snapshot: _models.CatalogSnapshot,
        favorites: frozenset[str],
        recent_applications: tuple[str, ...],
        hide_unused: bool = False,
    ) -> None:
        """Display a new catalog snapshot and user ranking state."""
        self._snapshot = snapshot
        self._application_map = snapshot.applicationMap()
        self._favorites = favorites
        self._recent_applications = recent_applications
        self._hide_unused = hide_unused
        self._hide_unused_button.blockSignals(True)
        self._hide_unused_button.setChecked(hide_unused)
        self._hide_unused_button.blockSignals(False)
        self._populateApplications()

    def setLoading(self, message: str) -> None:
        """Show a loading state and disable mutation controls."""
        self._status_generation += 1
        self._status_label.setObjectName("mutedLabel")
        self._status_label.setText(message)
        self._status_label.setVisible(True)
        self._status_label.style().unpolish(self._status_label)
        self._status_label.style().polish(self._status_label)
        self._stack_combo.setEnabled(False)

    def setReady(self, message: str = "") -> None:
        """Restore interactive controls and display a status message."""
        self._status_generation += 1
        self._stack_combo.setEnabled(True)
        self._status_label.setObjectName("mutedLabel")
        self._status_label.setText(message)
        self._status_label.setVisible(bool(message))

    def setError(self, message: str) -> None:
        """Display a recoverable catalog or launch error."""
        self._status_generation += 1
        self._stack_combo.setEnabled(True)
        self._status_label.setObjectName("errorLabel")
        self._status_label.setText(message)
        self._status_label.setVisible(True)
        self._status_label.style().unpolish(self._status_label)
        self._status_label.style().polish(self._status_label)

    def showTransientStatus(self, message: str, duration_seconds: float) -> None:
        """Display a status message that cannot clear newer UI state.

        Args:
            message: Status text to display.
            duration_seconds: Time before hiding the unchanged message.

        """
        self.setReady(message)
        self._transient_status_generation = self._status_generation
        self._transient_status_timer.start(max(1, round(duration_seconds * 1000)))

    def setStackMonitorWarning(self, message: str = "", details: str = "") -> None:
        """Show or clear the dedicated Stack-monitor health warning.

        Args:
            message: Concise visible warning, or an empty string to clear it.
            details: Optional diagnostic tooltip text.

        """
        self._stack_health_label.setText(f"⚠ {message}" if message else "")
        self._stack_health_label.setToolTip(details)
        self._stack_health_label.setVisible(bool(message))

    def _clearTransientStatus(self) -> None:
        """Clear a transient status only when no newer status replaced it."""
        if self._transient_status_generation == self._status_generation:
            self.setReady()

    def showLauncher(self) -> None:
        """Show, raise, and focus the launcher."""
        if self.isMinimized():
            self.showNormal()
        else:
            self.show()
        self.raise_()
        self.activateWindow()
        self.focusSearch()

    def focusSearch(self) -> None:
        """Focus and select the search field."""
        self._search_input.setFocus(QtCore.Qt.ShortcutFocusReason)  # type: ignore
        self._search_input.selectAll()

    def allowClose(self) -> None:
        """Allow the next close event to destroy the window."""
        self._allow_close = True

    def closeEvent(self, event) -> None:
        """Hide to tray unless application shutdown is in progress."""
        if self._allow_close:
            event.accept()
            return
        self.hide()
        event.ignore()

    def _populateApplications(self) -> None:
        """Rebuild the visible catalog for the current query."""
        query = self._search_input.text().strip()
        self._application_list.clear()
        applications = self._visibleApplications()
        if query:
            ranked = _search.rankApplications(
                applications,
                query,
                self._favorites,
                self._recent_applications,
            )
            if ranked:
                for application in ranked:
                    self._addApplicationItem(application)
            else:
                self._addEmptyItem("No applications match your search")
            return

        displayed: set[str] = set()
        favorites = [
            application for application in applications if application.stable_id in self._favorites
        ]
        if favorites:
            self._addSection("Favorites")
            for application in favorites:
                self._addApplicationItem(application)
                displayed.add(application.stable_id)

        recent = [
            self._application_map[stable_id]
            for stable_id in self._recent_applications
            if stable_id in self._application_map and stable_id not in displayed
        ]
        if recent:
            self._addSection("Recent")
            for application in recent:
                self._addApplicationItem(application)
                displayed.add(application.stable_id)

        for group in self._snapshot.groups:
            grouped = [
                application
                for application in applications
                if application.group_id == group.stable_id
                and application.stable_id not in displayed
            ]
            if not grouped:
                continue
            self._addSection(group.name)
            for application in grouped:
                self._addApplicationItem(application)
                displayed.add(application.stable_id)

        ungrouped = [
            application for application in applications if application.stable_id not in displayed
        ]
        if ungrouped:
            if favorites or recent or self._snapshot.groups:
                self._addSection("Other")
            for application in ungrouped:
                self._addApplicationItem(application)

        if not self._snapshot.applications:
            if self._snapshot.stack_state.mode == _models.StackMode.PROMPT:
                self._addEmptyItem("Choose a Stack to view applications")
            else:
                self._addEmptyItem("No applications are declared for the active Envoy Stack")
        elif not displayed:
            self._addEmptyItem("No applications have a favorite or launch history to show")

    def _visibleApplications(self) -> tuple[_models.ApplicationEntry, ...]:
        """Return catalog applications after the hide-unused-applications filter."""
        if not self._hide_unused:
            return self._snapshot.applications
        return tuple(
            application
            for application in self._snapshot.applications
            if application.stable_id in self._favorites
            or application.stable_id in self._recent_applications
        )

    def _addSection(self, section_name: str) -> None:
        """Append a non-interactive section label."""
        item = QtWidgets.QListWidgetItem(section_name.upper())
        item.setData(_SECTION_ROLE, True)
        item.setFlags(QtCore.Qt.NoItemFlags)  # type: ignore
        item.setSizeHint(QtCore.QSize(0, 28))
        font = item.font()
        font.setPointSizeF(8.0)
        font.setWeight(QtGui.QFont.DemiBold)  # type: ignore
        item.setFont(font)
        self._application_list.addItem(item)

    def _addApplicationItem(self, application: _models.ApplicationEntry) -> None:
        """Append one interactive application row."""
        detail = application.description or f"envoy {application.command}"
        item = QtWidgets.QListWidgetItem(f"{application.name}\n{detail}")
        item.setData(_APPLICATION_ROLE, application.stable_id)
        item.setIcon(_icons.loadPathIcon(application.icon_path))
        item.setSizeHint(QtCore.QSize(0, 58))
        tooltip = self._formatTooltip(application)
        item.setToolTip(tooltip)
        self._application_list.addItem(item)

    def _addEmptyItem(self, message: str) -> None:
        """Append a centered non-interactive empty-state row."""
        item = QtWidgets.QListWidgetItem(message)
        item.setFlags(QtCore.Qt.NoItemFlags)  # type: ignore
        item.setTextAlignment(QtCore.Qt.AlignCenter)  # type: ignore
        item.setSizeHint(QtCore.QSize(0, 90))
        self._application_list.addItem(item)

    def _onItemClicked(self, item: QtWidgets.QListWidgetItem) -> None:
        """Launch an application from a clicked or activated row."""
        stable_id = item.data(_APPLICATION_ROLE)
        if stable_id:
            self.launchRequested.emit(stable_id, False)

    def _launchFirstVisible(self) -> None:
        """Launch the first visible application result."""
        for item_index in range(self._application_list.count()):
            item = self._application_list.item(item_index)
            stable_id = item.data(_APPLICATION_ROLE)
            if stable_id:
                self.launchRequested.emit(stable_id, False)
                return

    def _showApplicationMenu(self, point: QtCore.QPoint) -> None:
        """Show contextual actions for an application row."""
        item = self._application_list.itemAt(point)
        if item is None:
            return
        stable_id = item.data(_APPLICATION_ROLE)
        application = self._application_map.get(stable_id)
        if application is None:
            return
        menu = self._buildApplicationMenu(stable_id, application)
        menu.exec_(self._application_list.viewport().mapToGlobal(point))

    def _buildApplicationMenu(
        self,
        stable_id: str,
        application: _models.ApplicationEntry,
    ) -> QtWidgets.QMenu:
        """Construct the contextual actions menu for one application.

        Args:
            stable_id: Stable identity of the target application.
            application: Resolved application entry.

        Returns:
            An unshown menu, ready to exec_() or inspect in tests.

        """
        menu = QtWidgets.QMenu(self)
        terminal_action = menu.addAction("Run in terminal")
        terminal_action.triggered.connect(
            lambda checked=False, key=stable_id: self.launchRequested.emit(key, True)
        )
        favorite_label = (
            "Remove from favorites" if stable_id in self._favorites else "Add to favorites"
        )
        favorite_action = menu.addAction(favorite_label)
        favorite_action.triggered.connect(
            lambda checked=False, key=stable_id: self.favoriteToggleRequested.emit(key)
        )
        menu.addSeparator()
        copy_action = menu.addAction("Copy Envoy command")
        copy_action.triggered.connect(
            lambda checked=False, key=stable_id: self.copyRequested.emit(key)
        )
        if application.homepage:
            homepage_action = menu.addAction("Open homepage")
            homepage_action.triggered.connect(
                lambda checked=False, key=stable_id: self.homepageRequested.emit(key)
            )
        if stable_id in self._recent_applications:
            menu.addSeparator()
            clear_history_action = menu.addAction("Clear launch history")
            clear_history_action.triggered.connect(
                lambda checked=False, key=stable_id: self.historyClearRequested.emit(key)
            )
        return menu

    def _onStackChanged(self, item_index: int) -> None:
        """Request an Envoy Stack selection change."""
        if self._populating_stacks or item_index < 0:
            return
        if item_index == self._custom_picker_index:
            self._populating_stacks = True
            self._stack_combo.setCurrentIndex(self._active_stack_index)
            self._populating_stacks = False
            self.customStackRequested.emit()
            return
        self.stackRequested.emit(self._stack_combo.itemData(item_index))

    @staticmethod
    def _formatTooltip(application: _models.ApplicationEntry) -> str:
        """Build a concise HTML application tooltip."""
        command_line = " ".join(application.command_line)
        description = f"<br>{application.description}" if application.description else ""
        return f"<b>{application.name}</b>{description}<br><code>envoy {command_line}</code>"
