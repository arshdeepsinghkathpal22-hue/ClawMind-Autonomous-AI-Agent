"""Browser tool tests. The real-browser test is skipped when Chromium isn't installed.

Set CLAWMIND_TEST_BROWSER=/path/to/chrome to use a specific Chromium binary.
"""

import http.server
import os
import threading

import pytest

from app.tools import browser, web
from app.tools.registry import run_tool


@pytest.fixture
def browser_enabled(monkeypatch):
    monkeypatch.setattr(browser.settings, "enable_browser", True)
    path = os.environ.get("CLAWMIND_TEST_BROWSER", "")
    monkeypatch.setattr(browser.settings, "browser_executable_path", path)
    return path


@pytest.mark.parametrize("url", ["http://127.0.0.1:8000/", "http://169.254.169.254/", "file:///etc/passwd", "http://localhost/"])
def test_blocked_before_the_browser_starts(browser_enabled, url):
    result = run_tool("browser_open", {"url": url})
    assert result["success"] is False
    error = result["error"].lower()
    assert "blocked" in error or "only http" in error or "not allowed" in error


def test_missing_browser_is_explained(browser_enabled, monkeypatch):
    monkeypatch.setattr(browser.settings, "browser_executable_path", "/nonexistent/chrome")
    monkeypatch.setattr(web, "resolve_public", lambda host, port: "93.184.215.14")
    result = run_tool("browser_open", {"url": "https://example.com"})
    assert result["success"] is False
    assert "could not start" in result["error"] or "not installed" in result["error"]


class PageHandler(http.server.BaseHTTPRequestHandler):
    requested = []

    def do_GET(self):
        PageHandler.requested.append(self.path)
        if self.path == "/":
            body = (
                "<html><head><title>Browser Test</title></head><body>"
                "<h1 id='h'>Static</h1>"
                "<script>document.getElementById('h').textContent = 'Rendered by JavaScript';</script>"
                f"<img src='/ok-image'><img src='http://127.0.0.2:{self.server.server_address[1]}/leak'>"
                "</body></html>"
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
        else:
            body = b""
            self.send_response(204)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def test_real_browser_renders_and_blocks_internal_requests(browser_enabled, monkeypatch):
    pytest.importorskip("playwright")
    server = http.server.ThreadingHTTPServer(("0.0.0.0", 0), PageHandler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    PageHandler.requested = []

    # Only the test page's own address is allowed; everything else stays blocked
    real_is_public = web.is_public_ip
    monkeypatch.setattr(web, "is_public_ip", lambda ip: str(ip) == "127.0.0.1" or real_is_public(ip))
    monkeypatch.setattr(web, "ALLOWED_PORTS", web.ALLOWED_PORTS | {port})

    try:
        result = run_tool("browser_open", {"url": f"http://127.0.0.1:{port}/"})
    finally:
        server.shutdown()

    if not result["success"] and "could not start" in result["error"]:
        pytest.skip("Chromium is not installed")
    assert result["success"], result
    assert result["title"] == "Browser Test"
    assert "Rendered by JavaScript" in result["content"]
    assert "/ok-image" in PageHandler.requested
    assert "/leak" not in PageHandler.requested
