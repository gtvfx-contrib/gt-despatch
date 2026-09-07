import time
from pathlib import Path

from despatch import _main_window, _models


def makeSnapshot():
    application = _models.ApplicationEntry(
        stable_id="gt:test:app",
        application_id="app",
        bundle_id="gt:test",
        name="Test App",
        command="test_app",
        args=(),
        description="A test application",
        icon_path=None,
        group_id="tools",
        keywords=(),
        in_terminal=False,
        order=0,
        source_path=Path("despatch.json"),
    )
    group = _models.CatalogGroup("tools", "Tools", 0, "gt:test")
    stack_state = _models.StackState(
        _models.StackMode.EXPLICIT,
        _models.StackSelection("studio", "studio", Path("studio.estack")),
    )
    return _models.CatalogSnapshot(stack_state, (application,), (group,), ())


def makeApplication(stable_id="gt:test:app", **overrides):
    fields = {
        "stable_id": stable_id,
        "application_id": stable_id.rsplit(":", 1)[-1],
        "bundle_id": "gt:test",
        "name": "Test App",
        "command": "test_app",
        "args": (),
        "description": "A test application",
        "icon_path": None,
        "group_id": "tools",
        "keywords": (),
        "in_terminal": False,
        "order": 0,
        "source_path": Path("despatch.json"),
    }
    fields.update(overrides)
    return _models.ApplicationEntry(**fields)


def testCustomStackSelectorShowsFilenameAndPath(qapp, tmp_path):
    window = _main_window.MainWindow()
    stack_path = (tmp_path / "my_stack.estack").resolve()
    stack_state = _models.StackState(
        _models.StackMode.EXPLICIT,
        _models.StackSelection(
            str(stack_path),
            stack_path.name,
            stack_path,
            is_custom=True,
        ),
    )

    window.setStacks((), stack_state)

    assert window._stack_combo.currentText() == "my_stack.estack"
    assert window._stack_combo.toolTip() == str(stack_path)
    assert window._stack_combo.property("customStack") is True
    window.allowClose()
    window.close()


def testAutomaticStackSelectionIsRequestable(qapp):
    window = _main_window.MainWindow()
    stack_state = _models.StackState(_models.StackMode.PROMPT)
    received = []
    window.stackRequested.connect(received.append)
    window.setStacks((), stack_state)

    window._stack_combo.setCurrentIndex(1)
    qapp.processEvents()

    assert received == [None]
    window.allowClose()
    window.close()


def testCustomStackPickerRestoresActiveSelection(qapp):
    window = _main_window.MainWindow()
    stack = _models.NamedStack("studio", "2026-01-01", Path("studio.estack"))
    stack_state = _models.StackState(
        _models.StackMode.EXPLICIT,
        _models.StackSelection("studio", "studio", stack.path),
    )
    received = []
    window.customStackRequested.connect(lambda: received.append(True))
    window.setStacks((stack,), stack_state)
    active_index = window._stack_combo.currentIndex()

    window._stack_combo.setCurrentIndex(window._custom_picker_index)
    qapp.processEvents()

    assert received == [True]
    assert window._stack_combo.currentIndex() == active_index
    window.allowClose()
    window.close()


def testSingleClickRequestsLaunch(qapp):
    window = _main_window.MainWindow()
    window.setCatalog(makeSnapshot(), frozenset(), ())
    application_item = window._application_list.item(1)
    received = []
    window.launchRequested.connect(
        lambda stable_id, in_terminal: received.append((stable_id, in_terminal))
    )

    window._onItemClicked(application_item)
    qapp.processEvents()

    assert received == [("gt:test:app", False)]
    window.allowClose()
    window.close()


def testCloseHidesToTray(qapp):
    window = _main_window.MainWindow()
    window.show()
    qapp.processEvents()

    window.close()
    qapp.processEvents()

    assert not window.isVisible()
    window.allowClose()
    window.close()


def testSearchEnterLaunchesFirstMatch(qapp):
    window = _main_window.MainWindow()
    window.setCatalog(makeSnapshot(), frozenset(), ())
    window._search_input.setText("test")
    received = []
    window.launchRequested.connect(
        lambda stable_id, in_terminal: received.append((stable_id, in_terminal))
    )

    window._search_input.returnPressed.emit()
    qapp.processEvents()

    assert received == [("gt:test:app", False)]
    window.allowClose()
    window.close()


def testDocumentationButtonRequestsHelp(qapp):
    window = _main_window.MainWindow()
    received = []
    window.documentationRequested.connect(lambda: received.append(True))

    window._documentation_button.click()
    qapp.processEvents()

    assert received == [True]
    assert window._documentation_button.accessibleName() == "Documentation"
    window.allowClose()
    window.close()


def testLauncherDoesNotExposeRefreshControl(qapp):
    window = _main_window.MainWindow()

    tooltips = {
        button.toolTip() for button in window.findChildren(_main_window.QtWidgets.QToolButton)
    }

    assert "Refresh catalog" not in tooltips
    assert not hasattr(window, "_refresh_button")
    window.allowClose()
    window.close()


def testStackMonitorWarningIsIndependentFromCatalogStatus(qapp):
    window = _main_window.MainWindow()

    window.setReady("Catalog ready")
    window.setStackMonitorWarning("Can’t check Stack updates", "network unavailable")

    assert window._status_label.text() == "Catalog ready"
    assert not window._stack_health_label.isHidden()
    assert "Can’t check" in window._stack_health_label.text()
    assert window._stack_health_label.toolTip() == "network unavailable"

    window.setStackMonitorWarning()

    assert window._stack_health_label.isHidden()
    window.allowClose()
    window.close()


def testTransientStatusDoesNotClearNewerError(qapp):
    window = _main_window.MainWindow()

    window.showTransientStatus("Stack updated automatically", 0.01)
    window.setError("Newer error")
    deadline = time.monotonic() + 0.1
    while time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.005)

    assert window._status_label.text() == "Newer error"
    assert not window._status_label.isHidden()
    window.allowClose()
    window.close()


def testApplicationMenuOmitsHomepageWhenUnset(qapp):
    window = _main_window.MainWindow()
    application = makeApplication()

    menu = window._buildApplicationMenu(application.stable_id, application)

    assert "Open homepage" not in {action.text() for action in menu.actions()}
    window.allowClose()
    window.close()


def testApplicationMenuOffersHomepageWhenSet(qapp):
    window = _main_window.MainWindow()
    application = makeApplication(homepage="https://example.com")
    received = []
    window.homepageRequested.connect(received.append)

    menu = window._buildApplicationMenu(application.stable_id, application)
    homepage_action = next(action for action in menu.actions() if action.text() == "Open homepage")
    homepage_action.trigger()
    qapp.processEvents()

    assert received == [application.stable_id]
    window.allowClose()
    window.close()


def testApplicationMenuOmitsClearHistoryWhenNeverLaunched(qapp):
    window = _main_window.MainWindow()
    application = makeApplication()

    menu = window._buildApplicationMenu(application.stable_id, application)

    assert "Clear launch history" not in {action.text() for action in menu.actions()}
    window.allowClose()
    window.close()


def testApplicationMenuOffersClearHistoryWhenRecent(qapp):
    window = _main_window.MainWindow()
    application = makeApplication()
    window._recent_applications = (application.stable_id,)
    received = []
    window.historyClearRequested.connect(received.append)

    menu = window._buildApplicationMenu(application.stable_id, application)
    clear_action = next(
        action for action in menu.actions() if action.text() == "Clear launch history"
    )
    clear_action.trigger()
    qapp.processEvents()

    assert received == [application.stable_id]
    window.allowClose()
    window.close()


def testHideUnusedFiltersApplicationsWithNoFavoriteOrHistory(qapp):
    used = makeApplication("gt:test:used")
    unused = makeApplication("gt:test:unused")
    stack_state = _models.StackState(
        _models.StackMode.EXPLICIT,
        _models.StackSelection("studio", "studio", Path("studio.estack")),
    )
    snapshot = _models.CatalogSnapshot(stack_state, (used, unused), (), ())
    window = _main_window.MainWindow()
    window.setCatalog(snapshot, frozenset({"gt:test:used"}), ())

    assert {app.stable_id for app in window._visibleApplications()} == {
        "gt:test:used",
        "gt:test:unused",
    }

    window._hide_unused = True
    assert {app.stable_id for app in window._visibleApplications()} == {"gt:test:used"}
    window.allowClose()
    window.close()


def testHideUnusedButtonPersistsAndAppliesFilter(qapp):
    window = _main_window.MainWindow()
    favored = makeApplication("gt:test:favored")
    unused = makeApplication("gt:test:unused")
    stack_state = _models.StackState(
        _models.StackMode.EXPLICIT,
        _models.StackSelection("studio", "studio", Path("studio.estack")),
    )
    snapshot = _models.CatalogSnapshot(stack_state, (favored, unused), (), ())
    window.setCatalog(snapshot, frozenset({"gt:test:favored"}), ())
    received = []
    window.hideUnusedToggled.connect(received.append)

    window._hide_unused_button.click()
    qapp.processEvents()

    assert received == [True]
    # MainWindow only requests the change; the coordinator persists it and
    # calls setCatalog() back with the filter applied.
    window.setCatalog(snapshot, frozenset({"gt:test:favored"}), (), True)
    shown_ids = {
        window._application_list.item(index).data(_main_window._APPLICATION_ROLE)
        for index in range(window._application_list.count())
    }
    shown_ids.discard(None)
    assert shown_ids == {"gt:test:favored"}
    assert window._hide_unused_button.isChecked() is True
    window.allowClose()
    window.close()
