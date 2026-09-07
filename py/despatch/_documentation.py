"""Open and serve Despatch's generated documentation site."""

from __future__ import annotations

import functools
import http.server
import json
import os
import subprocess
import sys
import tempfile
import time
import webbrowser
from http import HTTPStatus
from pathlib import Path
from typing import Any

from . import _constants


class DocumentationError(RuntimeError):
    """Raised when the documentation cannot be served or opened."""


class _DocumentationRequestHandler(http.server.SimpleHTTPRequestHandler):
    """Serve static files quietly and record the latest request time."""

    server_version = "DespatchDocs/1"

    def do_GET(self) -> None:
        """Record and serve one GET request."""
        self.server.last_request_time = time.monotonic()  # type: ignore[attr-defined]
        super().do_GET()

    def do_HEAD(self) -> None:
        """Record and serve one HEAD request."""
        self.server.last_request_time = time.monotonic()  # type: ignore[attr-defined]
        super().do_HEAD()

    def list_directory(self, path: str):
        """Disable directory listings for the packaged static site."""
        self.send_error(HTTPStatus.NOT_FOUND, "File not found")
        return None

    def log_message(self, message_format: str, *args: Any) -> None:
        """Suppress standard-library request logging."""


class _DocumentationServer(http.server.ThreadingHTTPServer):
    """Loopback HTTP server carrying its last request timestamp."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, server_address, request_handler_class):
        self.last_request_time = time.monotonic()
        super().__init__(server_address, request_handler_class)

    def server_bind(self) -> None:
        """Bind without the base class's reverse-DNS lookup.

        Note:
            ``http.server.HTTPServer.server_bind`` calls
            ``socket.getfqdn(host)`` to populate ``server_name``, which is
            never used for this loopback-only, handshake-driven server. That
            lookup can take many seconds -- or hang outright -- on hosts with
            unusual DNS/mDNS resolver behavior (observed on a GitHub-hosted
            macOS runner); skip it entirely.

        """
        http.server.socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = host
        self.server_port = port


def getDocumentationSite() -> Path | None:
    """Return the generated documentation site available to this process.

    Returns:
        Directory containing ``index.html``, or None when source execution has
        no locally built site.

    """
    if getattr(sys, "frozen", False):
        extraction_root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        site_directory = extraction_root / "despatch" / "docs"
    else:
        site_directory = Path(__file__).resolve().parents[2] / "site"
    if (site_directory / "index.html").is_file():
        return site_directory
    return None


def openDocumentation() -> str:
    """Open local packaged documentation or the published source fallback.

    Returns:
        URL passed to the system browser.

    Raises:
        DocumentationError: If the browser or local server cannot be started.

    """
    site_directory = getDocumentationSite()
    if site_directory is None:
        if getattr(sys, "frozen", False):
            raise DocumentationError("The executable does not contain its documentation site")
        return _openBrowser(_constants.DOCUMENTATION_URL)

    with tempfile.TemporaryDirectory(prefix="despatch-docs-") as temporary_directory:
        ready_path = Path(temporary_directory) / "ready.json"
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        process = subprocess.Popen(
            _documentationServerCommand(ready_path),
            close_fds=True,
            creationflags=creation_flags,
        )
        try:
            result = _waitForServer(process, ready_path)
            error_message = result.get("error")
            if error_message:
                raise DocumentationError(str(error_message))
            url = result.get("url")
            if not isinstance(url, str) or not url.startswith("http://127.0.0.1:"):
                raise DocumentationError("Documentation server returned an invalid URL")
            return _openBrowser(url)
        except BaseException:
            if process.poll() is None:
                process.terminate()
            raise


def serveDocumentation(
    ready_path: Path | str,
    idle_timeout: float = _constants.DOCUMENTATION_IDLE_TIMEOUT_SECONDS,
) -> int:
    """Serve the generated site until it has been idle for the timeout.

    Args:
        ready_path: JSON handshake path written after binding the server.
        idle_timeout: Seconds without requests before the helper exits.

    Returns:
        Process exit code.

    """
    handshake_path = Path(ready_path)
    site_directory = getDocumentationSite()
    if site_directory is None:
        _writeHandshake(handshake_path, {"error": "Documentation site is unavailable"})
        return 2

    handler = functools.partial(_DocumentationRequestHandler, directory=str(site_directory))
    try:
        with _DocumentationServer(("127.0.0.1", 0), handler) as server:
            server.timeout = min(1.0, max(0.05, idle_timeout))
            port = int(server.server_address[1])
            _writeHandshake(handshake_path, {"url": f"http://127.0.0.1:{port}/"})
            while time.monotonic() - server.last_request_time < idle_timeout:
                server.handle_request()
    except OSError as error:
        _writeHandshake(handshake_path, {"error": f"Documentation server failed: {error}"})
        return 2
    return 0


def _documentationServerCommand(ready_path: Path) -> list[str]:
    """Build the hidden server command for source or frozen execution."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--docs-server", str(ready_path)]
    return [sys.executable, "-m", "despatch", "--docs-server", str(ready_path)]


def _waitForServer(
    process: subprocess.Popen,
    ready_path: Path,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """Wait for the server handshake and return its JSON object."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if ready_path.is_file():
            try:
                result = json.loads(ready_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                time.sleep(0.05)
                continue
            if isinstance(result, dict):
                return result
        return_code = process.poll()
        if return_code is not None:
            raise DocumentationError(
                f"Documentation server exited before startup (code {return_code})"
            )
        time.sleep(0.05)
    raise DocumentationError("Timed out while starting the documentation server")


def _writeHandshake(ready_path: Path, result: dict[str, str]) -> None:
    """Write a server handshake atomically."""
    ready_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = ready_path.with_suffix(f"{ready_path.suffix}.tmp")
    temporary_path.write_text(json.dumps(result), encoding="utf-8")
    os.replace(temporary_path, ready_path)


def _openBrowser(url: str) -> str:
    """Open a URL in the default browser and return it."""
    if not webbrowser.open(url, new=2):
        raise DocumentationError("No system web browser is available")
    return url
