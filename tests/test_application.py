from pathlib import Path
from types import SimpleNamespace

from despatch import _application, _models


def testDevModeRequiresExactTrimmedOne(monkeypatch):
    monkeypatch.delenv("ENVOY_DEV_MODE", raising=False)
    assert _application._isDevModeEnabled() is False

    monkeypatch.setenv("ENVOY_DEV_MODE", " 1 ")
    assert _application._isDevModeEnabled() is True

    monkeypatch.setenv("ENVOY_DEV_MODE", "true")
    assert _application._isDevModeEnabled() is False


def testManualAutomaticSelectionLastsForCurrentSession():
    operations = []
    refreshes = []
    gateway = SimpleNamespace(clearStack=lambda: operations.append("clear"))
    coordinator = SimpleNamespace(
        _stack_state=_models.StackState(_models.StackMode.PROMPT),
        _window=SimpleNamespace(setLoading=lambda message: None),
        _gateway=gateway,
        _dev_mode=False,
        _automatic_resolution_requested=False,
        _state_generation=0,
        _stack_monitor=SimpleNamespace(suspend=lambda: None),
        _onStackSwitchError=lambda error: None,
        refreshCatalog=lambda: refreshes.append(True),
    )
    coordinator._submit = lambda operation, on_success, on_error: on_success(operation())

    _application.DespatchApplication._switchStack(coordinator, None)

    assert operations == ["clear"]
    assert coordinator._automatic_resolution_requested is True
    assert refreshes == [True]


def testExplicitSelectionRestoresConfiguredDefault():
    operations = []
    gateway = SimpleNamespace(switchStack=lambda value: operations.append(value))
    coordinator = SimpleNamespace(
        _stack_state=_models.StackState(_models.StackMode.AUTOMATIC),
        _window=SimpleNamespace(setLoading=lambda message: None),
        _gateway=gateway,
        _dev_mode=False,
        _automatic_resolution_requested=True,
        _state_generation=0,
        _stack_monitor=SimpleNamespace(suspend=lambda: None),
        _onStackSwitchError=lambda error: None,
        refreshCatalog=lambda: None,
    )
    coordinator._submit = lambda operation, on_success, on_error: on_success(operation())

    _application.DespatchApplication._switchStack(coordinator, "studio")

    assert operations == ["studio"]
    assert coordinator._automatic_resolution_requested is False


def testCancelingCustomStackPickerPreservesSelection(monkeypatch):
    switch_requests = []
    coordinator = SimpleNamespace(
        _stack_state=_models.StackState(_models.StackMode.PROMPT),
        _window=None,
        _switchStack=switch_requests.append,
    )
    monkeypatch.setattr(
        _application.QtWidgets.QFileDialog,
        "getOpenFileName",
        lambda *args: ("", "Envoy Stack (*.estack)"),
    )

    _application.DespatchApplication._chooseCustomStack(coordinator)

    assert switch_requests == []


def testAutomaticStateDisablesStackMonitoring():
    calls = []
    coordinator = SimpleNamespace(
        _stack_state=_models.StackState(_models.StackMode.AUTOMATIC),
        _stack_monitor=SimpleNamespace(disable=lambda: calls.append("disabled")),
    )

    _application.DespatchApplication._configureStackMonitor(coordinator, None)

    assert calls == ["disabled"]


def testExplicitStateConfiguresStackMonitoring():
    calls = []
    selection = _models.StackSelection("studio", "studio", Path("studio.estack"))
    file_state = _models.StackFileState(Path("studio.estack"), 12, 34, 56)
    coordinator = SimpleNamespace(
        _stack_state=_models.StackState(_models.StackMode.EXPLICIT, selection),
        _stack_monitor=SimpleNamespace(
            configure=lambda *arguments: calls.append(arguments),
            disable=lambda: None,
        ),
        _settings=SimpleNamespace(stack_refresh_interval_seconds=300),
    )

    _application.DespatchApplication._configureStackMonitor(coordinator, file_state)

    assert calls == [(selection, file_state, 300)]


def testAutomaticReloadFailureUsesInlineHealthState():
    events = []
    coordinator = SimpleNamespace(
        _state_generation=4,
        _stack_monitor=SimpleNamespace(
            resumeAfterReloadFailure=lambda message: events.append(("warning", message))
        ),
        _window=SimpleNamespace(setReady=lambda: events.append(("ready", None))),
        _onCatalogError=lambda error: events.append(("catalog-error", error)),
        _finishCatalogRefresh=lambda: events.append(("finished", None)),
    )
    error = RuntimeError("invalid updated Stack")

    _application.DespatchApplication._onCatalogRefreshError(
        coordinator,
        error,
        "monitor",
        4,
    )

    assert events == [
        ("warning", "invalid updated Stack"),
        ("ready", None),
        ("finished", None),
    ]


def testRequestCatalogRefreshMarksTrayIconRefreshing():
    refreshing_calls = []
    submitted = []
    coordinator = SimpleNamespace(
        _catalog_refresh_active=False,
        _catalog_refresh_queued=False,
        _state_generation=0,
        _tray_icon=SimpleNamespace(setRefreshing=refreshing_calls.append),
        _window=SimpleNamespace(setLoading=lambda message: None),
        _stack_monitor=SimpleNamespace(suspend=lambda: None),
        _loadState=lambda: None,
        _submit=lambda operation, on_success, on_error: submitted.append(operation),
    )

    _application.DespatchApplication._requestCatalogRefresh(coordinator, "manual")

    assert refreshing_calls == [True]
    assert coordinator._catalog_refresh_active is True
    assert len(submitted) == 1


def testRequestCatalogRefreshCoalescedDoesNotReenterRefreshing():
    refreshing_calls = []
    coordinator = SimpleNamespace(
        _catalog_refresh_active=True,
        _catalog_refresh_queued=False,
        _tray_icon=SimpleNamespace(setRefreshing=refreshing_calls.append),
    )

    _application.DespatchApplication._requestCatalogRefresh(coordinator, "manual")

    assert refreshing_calls == []
    assert coordinator._catalog_refresh_queued is True


def testFinishCatalogRefreshClearsTrayIconWhenNotQueued():
    refreshing_calls = []
    coordinator = SimpleNamespace(
        _catalog_refresh_active=True,
        _catalog_refresh_queued=False,
        _tray_icon=SimpleNamespace(setRefreshing=refreshing_calls.append),
    )

    _application.DespatchApplication._finishCatalogRefresh(coordinator)

    assert refreshing_calls == [False]
    assert coordinator._catalog_refresh_active is False


def testFinishCatalogRefreshKeepsTrayIconRefreshingWhenRequeuing(monkeypatch):
    refreshing_calls = []
    timers = []
    monkeypatch.setattr(
        _application.QtCore.QTimer,
        "singleShot",
        staticmethod(lambda delay, callback: timers.append((delay, callback))),
    )
    coordinator = SimpleNamespace(
        _catalog_refresh_active=True,
        _catalog_refresh_queued=True,
        _tray_icon=SimpleNamespace(setRefreshing=refreshing_calls.append),
        refreshCatalog=lambda: None,
    )

    _application.DespatchApplication._finishCatalogRefresh(coordinator)

    assert refreshing_calls == []
    assert coordinator._catalog_refresh_queued is False
    assert len(timers) == 1


def _makeApplication(stable_id="gt:test:app", homepage=""):
    return _models.ApplicationEntry(
        stable_id=stable_id,
        application_id=stable_id.rsplit(":", 1)[-1],
        bundle_id="gt:test",
        name="Test App",
        command="test_app",
        args=(),
        description="",
        icon_path=None,
        group_id="",
        keywords=(),
        in_terminal=False,
        order=0,
        source_path=Path("despatch.json"),
        homepage=homepage,
    )


def testClearApplicationHistoryRefreshesViews():
    calls = []
    coordinator = SimpleNamespace(
        _applications={"gt:test:app": _makeApplication()},
        _settings=SimpleNamespace(clearLaunchHistory=calls.append),
        _refreshViews=lambda: calls.append("refreshed"),
    )

    _application.DespatchApplication._clearApplicationHistory(coordinator, "gt:test:app")

    assert calls == ["gt:test:app", "refreshed"]


def testClearApplicationHistoryIgnoresUnknownApplication():
    calls = []
    coordinator = SimpleNamespace(
        _applications={},
        _settings=SimpleNamespace(clearLaunchHistory=lambda key: calls.append(key)),
        _refreshViews=lambda: calls.append("refreshed"),
    )

    _application.DespatchApplication._clearApplicationHistory(coordinator, "gt:test:unknown")

    assert calls == []


def testSetHideUnusedApplicationsPersistsChange():
    calls = []
    coordinator = SimpleNamespace(
        _settings=SimpleNamespace(
            hide_unused_applications=False,
            setHideUnusedApplications=calls.append,
        ),
        _refreshViews=lambda: calls.append("refreshed"),
    )

    _application.DespatchApplication._setHideUnusedApplications(coordinator, True)

    assert calls == [True, "refreshed"]


def testSetHideUnusedApplicationsIsANoOpWhenUnchanged():
    calls = []
    coordinator = SimpleNamespace(
        _settings=SimpleNamespace(
            hide_unused_applications=True,
            setHideUnusedApplications=lambda value: calls.append(value),
        ),
        _refreshViews=lambda: calls.append("refreshed"),
    )

    _application.DespatchApplication._setHideUnusedApplications(coordinator, True)

    assert calls == []


def testOpenApplicationHomepageIgnoresApplicationWithoutOne():
    submitted = []
    coordinator = SimpleNamespace(
        _applications={"gt:test:app": _makeApplication()},
        _submit=lambda operation, on_success, on_error: submitted.append(operation),
    )

    _application.DespatchApplication._openApplicationHomepage(coordinator, "gt:test:app")

    assert submitted == []


def testOpenApplicationHomepageOpensBrowserAndReportsSuccess(monkeypatch):
    opened = []
    monkeypatch.setattr(
        _application.webbrowser,
        "open",
        lambda url, new=0: opened.append((url, new)) or True,
    )
    status_messages = []
    coordinator = SimpleNamespace(
        _applications={"gt:test:app": _makeApplication(homepage="https://example.com")},
        _window=SimpleNamespace(
            setReady=lambda message: status_messages.append(("ready", message)),
            setError=lambda message: status_messages.append(("error", message)),
        ),
        _submit=lambda operation, on_success, on_error: on_success(operation()),
    )

    _application.DespatchApplication._openApplicationHomepage(coordinator, "gt:test:app")

    assert opened == [("https://example.com", 2)]
    assert status_messages == [("ready", "Opened Test App homepage in your browser")]


def testOpenApplicationHomepageReportsFailure():
    coordinator = SimpleNamespace(
        _applications={"gt:test:app": _makeApplication(homepage="https://example.com")},
        _window=SimpleNamespace(setError=lambda message: None),
        _showErrorDialog=lambda title, message: None,
        _submit=lambda operation, on_success, on_error: on_error(RuntimeError("no browser")),
    )

    # Must not raise even though on_error is invoked synchronously.
    _application.DespatchApplication._openApplicationHomepage(coordinator, "gt:test:app")
