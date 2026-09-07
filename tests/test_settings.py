import json

import pytest

from despatch import _settings, _settings_dialog


def testDefaultSettingsPathUsesEnvoyConfigRoot(monkeypatch, tmp_path):
    config_root = tmp_path / "envoy-root"
    monkeypatch.delenv("DESPATCH_CONFIG_ROOT", raising=False)
    monkeypatch.setattr(_settings, "_getEnvoyConfigRoot", lambda: config_root)

    assert _settings.getDefaultSettingsPath() == config_root / "despatch" / "settings.json"


def testDespatchConfigRootOverridesEnvoyRoot(monkeypatch, tmp_path):
    despatch_root = tmp_path / "despatch-root"
    monkeypatch.setenv("DESPATCH_CONFIG_ROOT", str(despatch_root))
    monkeypatch.setattr(
        _settings,
        "_getEnvoyConfigRoot",
        lambda: pytest.fail("Envoy root should not be queried for a Despatch override"),
    )

    assert _settings.getDefaultSettingsPath() == despatch_root / "settings.json"


def testBlankDespatchConfigRootIsIgnored(monkeypatch, tmp_path):
    config_root = tmp_path / "envoy-root"
    monkeypatch.setenv("DESPATCH_CONFIG_ROOT", "   ")
    monkeypatch.setattr(_settings, "_getEnvoyConfigRoot", lambda: config_root)

    assert _settings.getDefaultSettingsPath() == config_root / "despatch" / "settings.json"


def testEnvoyConfigRootFallbackUsesEnvironment(monkeypatch, tmp_path):
    config_root = tmp_path / "fallback-root"

    def raiseImportError(module_name):
        raise ImportError(module_name)

    monkeypatch.setenv("ENVOY_CONFIG_ROOT", str(config_root))
    monkeypatch.setattr(_settings.importlib, "import_module", raiseImportError)

    assert _settings._getEnvoyConfigRoot() == config_root


def testExplicitSettingsPathOverridesConfigRoots(monkeypatch, tmp_path):
    explicit_path = tmp_path / "explicit" / "settings.json"
    monkeypatch.setenv("DESPATCH_CONFIG_ROOT", str(tmp_path / "despatch-root"))

    store = _settings.SettingsStore(explicit_path)

    assert store.settings_path == explicit_path


def testSettingsRoundTrip(tmp_path):
    settings_path = tmp_path / "settings.json"
    store = _settings.SettingsStore(settings_path)

    assert store.toggleFavorite("gt:test:app") is True
    store.recordLaunch("gt:test:app")
    store.updatePreferences(
        theme="dark",
        keep_open_after_launch=True,
        autostart=False,
        global_shortcut_enabled=True,
        global_shortcut="Ctrl+Alt+K",
        stack_refresh_interval_seconds=600,
    )

    reloaded = _settings.SettingsStore(settings_path)
    assert reloaded.favorites == frozenset({"gt:test:app"})
    assert reloaded.recent_applications == ("gt:test:app",)
    assert reloaded.theme == "dark"
    assert reloaded.keep_open_after_launch is True
    assert reloaded.global_shortcut == "Ctrl+Alt+K"
    assert reloaded.stack_refresh_interval_seconds == 600


def testClearLaunchHistoryRemovesOnlyOneEntry(tmp_path):
    settings_path = tmp_path / "settings.json"
    store = _settings.SettingsStore(settings_path)
    store.recordLaunch("gt:test:one")
    store.recordLaunch("gt:test:two")

    store.clearLaunchHistory("gt:test:one")

    assert store.recent_applications == ("gt:test:two",)
    reloaded = _settings.SettingsStore(settings_path)
    assert reloaded.recent_applications == ("gt:test:two",)


def testClearLaunchHistoryIsANoOpForUnknownApplication(tmp_path):
    settings_path = tmp_path / "settings.json"
    store = _settings.SettingsStore(settings_path)
    store.recordLaunch("gt:test:one")

    store.clearLaunchHistory("gt:test:unknown")

    assert store.recent_applications == ("gt:test:one",)


def testHideUnusedApplicationsRoundTrip(tmp_path):
    settings_path = tmp_path / "settings.json"
    store = _settings.SettingsStore(settings_path)

    assert store.hide_unused_applications is False
    store.setHideUnusedApplications(True)
    assert store.hide_unused_applications is True

    reloaded = _settings.SettingsStore(settings_path)
    assert reloaded.hide_unused_applications is True


def testInvalidSettingsFallBackToDefaults(tmp_path):
    settings_path = tmp_path / "settings.json"
    settings_path.write_text("{not json", encoding="utf-8")

    store = _settings.SettingsStore(settings_path)

    assert store.theme == "system"
    assert not store.favorites
    assert store.stack_refresh_interval_seconds == 300


def testLoadedListsAreDeduplicated(tmp_path):
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(
        json.dumps(
            {
                "favorites": ["one", "one", "two"],
                "recentApplications": ["two", "two", "one"],
            }
        ),
        encoding="utf-8",
    )

    store = _settings.SettingsStore(settings_path)

    assert store.favorites == frozenset({"one", "two"})
    assert store.recent_applications == ("two", "one")


@pytest.mark.parametrize("interval_seconds", [30, 90, 3601, True])
def testInvalidStackRefreshIntervalsAreRejected(tmp_path, interval_seconds):
    store = _settings.SettingsStore(tmp_path / "settings.json")

    with pytest.raises(ValueError, match="whole number from 1 to 60 minutes"):
        store.updatePreferences(
            theme="system",
            keep_open_after_launch=False,
            autostart=False,
            global_shortcut_enabled=False,
            global_shortcut="Ctrl+Alt+Space",
            stack_refresh_interval_seconds=interval_seconds,
        )


def testSettingsDialogReturnsStackRefreshInterval(qapp, tmp_path):
    store = _settings.SettingsStore(tmp_path / "settings.json")
    dialog = _settings_dialog.SettingsDialog(store, True, True)

    dialog._stack_refresh_spin.setValue(15)

    assert dialog.values()["stack_refresh_interval_seconds"] == 900
    dialog.close()
