import json
import threading
import time
import urllib.error
import urllib.request
from types import SimpleNamespace

import pytest

from despatch import __main__, _constants, _documentation


def testSourceDocumentationFallsBackToPublishedSite(monkeypatch):
    opened_urls = []
    monkeypatch.setattr(_documentation, "getDocumentationSite", lambda: None)
    monkeypatch.setattr(
        _documentation.webbrowser,
        "open",
        lambda url, new: opened_urls.append((url, new)) or True,
    )

    opened_url = _documentation.openDocumentation()

    assert opened_url == _constants.DOCUMENTATION_URL
    assert opened_urls == [(_constants.DOCUMENTATION_URL, 2)]


def testFrozenDocumentationMustBeBundled(monkeypatch):
    monkeypatch.setattr(_documentation, "getDocumentationSite", lambda: None)
    monkeypatch.setattr(_documentation.sys, "frozen", True, raising=False)

    with pytest.raises(_documentation.DocumentationError, match="does not contain"):
        _documentation.openDocumentation()


def testDocumentationServerServesFilesAndRejectsListings(monkeypatch, tmp_path):
    site_directory = tmp_path / "site"
    site_directory.mkdir()
    (site_directory / "index.html").write_text("<title>Despatch</title>", encoding="utf-8")
    (site_directory / "page.txt").write_text("documentation", encoding="utf-8")
    (site_directory / "nested").mkdir()
    ready_path = tmp_path / "ready.json"
    monkeypatch.setattr(_documentation, "getDocumentationSite", lambda: site_directory)
    result_codes = []

    server_thread = threading.Thread(
        target=lambda: result_codes.append(
            _documentation.serveDocumentation(ready_path, idle_timeout=0.5)
        )
    )
    server_thread.start()
    # A fixed, small attempt budget is flaky on a cold/loaded CI runner (the
    # server thread may simply not have started yet) -- wait against a
    # generous wall-clock deadline instead of a fixed retry count.
    deadline = time.monotonic() + 10
    while not ready_path.exists():
        if time.monotonic() >= deadline:
            raise TimeoutError("Documentation server did not report readiness in time.")
        server_thread.join(0.01)

    result = json.loads(ready_path.read_text(encoding="utf-8"))
    with urllib.request.urlopen(result["url"], timeout=2) as response:
        assert response.status == 200
        assert b"Despatch" in response.read()
    with urllib.request.urlopen(f'{result["url"]}page.txt', timeout=2) as response:
        assert response.read() == b"documentation"
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(f'{result["url"]}nested/', timeout=2)
    assert error.value.code == 404

    server_thread.join(2)
    assert not server_thread.is_alive()
    assert result_codes == [0]


def testDocumentationBrowserFailureIsActionable(monkeypatch):
    monkeypatch.setattr(_documentation.webbrowser, "open", lambda url, new: False)

    with pytest.raises(_documentation.DocumentationError, match="No system web browser"):
        _documentation._openBrowser("https://example.test/")


def testDocsCliDoesNotCreateQtApplication(monkeypatch):
    opened = []
    monkeypatch.setattr(_documentation, "openDocumentation", lambda: opened.append(True))
    monkeypatch.setattr(
        __main__,
        "_createApplication",
        lambda: pytest.fail("Qt application should not be created"),
    )

    assert __main__.main(["--docs"]) == 0
    assert opened == [True]


def testDocsCliReturnsFailureForOpenError(monkeypatch, capsys):
    def raiseError():
        raise _documentation.DocumentationError("browser unavailable")

    monkeypatch.setattr(_documentation, "openDocumentation", raiseError)

    assert __main__.main(["--docs"]) == 1
    assert "browser unavailable" in capsys.readouterr().err


def testServerCommandUsesFrozenExecutable(monkeypatch, tmp_path):
    monkeypatch.setattr(_documentation.sys, "frozen", True, raising=False)
    monkeypatch.setattr(_documentation.sys, "executable", "despatch.exe")
    ready_path = tmp_path / "ready.json"

    assert _documentation._documentationServerCommand(ready_path) == [
        "despatch.exe",
        "--docs-server",
        str(ready_path),
    ]


def testWaitForServerRejectsInvalidExit(tmp_path):
    process = SimpleNamespace(poll=lambda: 7)

    with pytest.raises(_documentation.DocumentationError, match="code 7"):
        _documentation._waitForServer(process, tmp_path / "missing.json", timeout=0.1)
