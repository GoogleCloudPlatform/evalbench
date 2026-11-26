import http.server
import io
import os
import socket
import sys
import threading
import urllib.error
import urllib.request
from unittest.mock import MagicMock, patch

import pytest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from generators.models.tools.fetch_url import (  # noqa: E402
    _CheckedRedirectHandler,
    _connect,
    _open,
    fetch_url,
)


def _public_addrinfo():
    """getaddrinfo response for a routable public IP. Avoids documentation
    ranges (198.51.100/24, 203.0.113/24) which Python's ipaddress module
    classifies as private."""
    return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0))]


def _private_addrinfo():
    return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("10.0.0.1", 0))]


def _loopback_addrinfo():
    return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", 0))]


def _mock_response(body: bytes, content_type: str = "text/plain"):
    resp = MagicMock()
    resp.read.return_value = body
    resp.headers = {"Content-Type": content_type}
    resp.__enter__ = lambda self: self
    resp.__exit__ = lambda *a: False
    return resp


def test_missing_url_returns_error():
    result = fetch_url({})
    assert result.startswith("Error: missing required arg 'url'")


def test_non_https_scheme_rejected():
    result = fetch_url({"url": "http://example.com/foo"})
    assert "only https URLs are allowed" in result


def test_file_scheme_rejected():
    result = fetch_url({"url": "file:///etc/passwd"})
    assert "only https URLs are allowed" in result


@patch("generators.models.tools.fetch_url.socket.getaddrinfo")
def test_private_ip_rejected(mock_getaddrinfo):
    mock_getaddrinfo.return_value = _private_addrinfo()
    result = fetch_url({"url": "https://internal.corp/secret"})
    assert "private, loopback" in result


@patch("generators.models.tools.fetch_url.socket.getaddrinfo")
def test_loopback_rejected(mock_getaddrinfo):
    mock_getaddrinfo.return_value = _loopback_addrinfo()
    result = fetch_url({"url": "https://localhost/admin"})
    assert "private, loopback" in result


@patch("generators.models.tools.fetch_url.socket.getaddrinfo")
def test_unresolvable_host_rejected(mock_getaddrinfo):
    mock_getaddrinfo.side_effect = socket.gaierror("nope")
    result = fetch_url({"url": "https://does-not-exist.invalid/"})
    assert "private, loopback, link-local, or unresolvable" in result


@patch("generators.models.tools.fetch_url._open")
@patch("generators.models.tools.fetch_url.socket.getaddrinfo")
def test_plain_text_response_returned(mock_getaddrinfo, mock_urlopen):
    mock_getaddrinfo.return_value = _public_addrinfo()
    mock_urlopen.return_value = _mock_response(
        b"Apache Beam 2.99.0 released",
        content_type="text/plain",
    )
    result = fetch_url({"url": "https://beam.apache.org/version"})
    assert result == "Apache Beam 2.99.0 released"


@patch("generators.models.tools.fetch_url._open")
@patch("generators.models.tools.fetch_url.socket.getaddrinfo")
def test_html_response_is_stripped(mock_getaddrinfo, mock_urlopen):
    mock_getaddrinfo.return_value = _public_addrinfo()
    html = (
        b"<html><head><style>body{}</style></head><body>"
        b"<h1>Downloads</h1><p>Latest: <b>2.99.0</b></p>"
        b"<script>alert('hi')</script></body></html>"
    )
    mock_urlopen.return_value = _mock_response(html, content_type="text/html")
    result = fetch_url({"url": "https://beam.apache.org/get-started/downloads/"})
    assert "Downloads" in result
    assert "2.99.0" in result
    assert "alert" not in result
    assert "<h1>" not in result


@patch("generators.models.tools.fetch_url._open")
@patch("generators.models.tools.fetch_url.socket.getaddrinfo")
def test_oversized_body_is_truncated(mock_getaddrinfo, mock_urlopen):
    mock_getaddrinfo.return_value = _public_addrinfo()
    big_body = b"x" * 60_000
    mock_urlopen.return_value = _mock_response(big_body, content_type="text/plain")
    result = fetch_url({"url": "https://example.com/big"})
    assert "[truncated at 50000 bytes]" in result
    assert len(result) <= 50_000 + len("\n\n[truncated at 50000 bytes]")


@patch("generators.models.tools.fetch_url._open")
@patch("generators.models.tools.fetch_url.socket.getaddrinfo")
def test_http_error_returned_as_string(mock_getaddrinfo, mock_urlopen):
    mock_getaddrinfo.return_value = _public_addrinfo()
    mock_urlopen.side_effect = urllib.error.HTTPError(
        "https://example.com/missing", 404, "Not Found", {}, io.BytesIO()
    )
    result = fetch_url({"url": "https://example.com/missing"})
    assert "HTTP 404" in result


@patch("generators.models.tools.fetch_url._open")
@patch("generators.models.tools.fetch_url.socket.getaddrinfo")
def test_timeout_returned_as_string(mock_getaddrinfo, mock_urlopen):
    mock_getaddrinfo.return_value = _public_addrinfo()
    mock_urlopen.side_effect = socket.timeout()
    result = fetch_url({"url": "https://example.com/slow"})
    assert "timeout after" in result


@patch("generators.models.tools.fetch_url._open")
@patch("generators.models.tools.fetch_url.socket.getaddrinfo")
def test_url_error_returned_as_string(mock_getaddrinfo, mock_urlopen):
    mock_getaddrinfo.return_value = _public_addrinfo()
    mock_urlopen.side_effect = urllib.error.URLError("connection refused")
    result = fetch_url({"url": "https://example.com/down"})
    assert "URL error" in result
    assert "connection refused" in result


# --- Redirect targets get the same checks as the first URL ---

def _redirect(newurl):
    request = urllib.request.Request("https://example.com/start")
    return _CheckedRedirectHandler().redirect_request(
        request, None, 302, "Found", {}, newurl)


@patch("generators.models.tools.fetch_url.socket.getaddrinfo")
def test_redirect_to_public_https_host_is_followed(mock_getaddrinfo):
    mock_getaddrinfo.return_value = _public_addrinfo()

    new_request = _redirect("https://example.org/next")

    assert new_request.full_url == "https://example.org/next"


def test_redirect_to_http_is_refused():
    with pytest.raises(urllib.error.URLError) as error:
        _redirect("http://example.org/next")

    assert "only https URLs are allowed" in str(error.value.reason)


@patch("generators.models.tools.fetch_url.socket.getaddrinfo")
def test_redirect_to_private_host_is_refused(mock_getaddrinfo):
    for addrinfo in (_private_addrinfo(), _loopback_addrinfo()):
        mock_getaddrinfo.return_value = addrinfo

        with pytest.raises(urllib.error.URLError) as error:
            _redirect("https://internal.example/secret")

        assert "refusing to fetch 'internal.example'" in str(error.value.reason)


def test_opener_refuses_a_redirect_before_the_second_request():
    """Runs the real opener against a local server that redirects."""
    paths = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            paths.append(self.path)
            if self.path == "/start":
                self.send_response(302)
                self.send_header(
                    "Location", f"http://127.0.0.1:{self.server.server_port}/target")
                self.end_headers()
            else:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"secret")

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/start")
        with pytest.raises(urllib.error.URLError) as error:
            _open(request)
    finally:
        server.shutdown()
        server.server_close()

    assert "redirect refused" in str(error.value.reason)
    assert paths == ["/start"]


@patch("generators.models.tools.fetch_url._open")
@patch("generators.models.tools.fetch_url.socket.getaddrinfo")
def test_refused_redirect_is_returned_as_string(mock_getaddrinfo, mock_open):
    mock_getaddrinfo.return_value = _public_addrinfo()
    mock_open.side_effect = urllib.error.URLError(
        "redirect refused: URL has no hostname")

    result = fetch_url({"url": "https://example.com/start"})

    assert result.startswith("Error: URL error fetching")
    assert "redirect refused" in result


# --- The connection uses an address that the check saw ---

def _addrinfo(ip, port=443):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]


@patch("generators.models.tools.fetch_url.socket.getaddrinfo")
def test_connect_refuses_a_blocked_address(mock_getaddrinfo):
    for ip in ("127.0.0.1", "10.0.0.1", "169.254.169.254"):
        mock_getaddrinfo.return_value = _addrinfo(ip)

        with pytest.raises(OSError) as error:
            _connect(("rebind.example", 443), 1)

        assert "refusing to connect to 'rebind.example'" in str(error.value)


@patch("generators.models.tools.fetch_url.socket.getaddrinfo")
def test_connect_refuses_if_one_of_the_addresses_is_blocked(mock_getaddrinfo):
    mock_getaddrinfo.return_value = (
        _addrinfo("93.184.216.34") + _addrinfo("127.0.0.1"))

    with pytest.raises(OSError) as error:
        _connect(("rebind.example", 443), 1)

    assert "refusing to connect" in str(error.value)


def test_connect_resolves_the_name_one_time():
    """Connects to a local listener. The test allows the loopback address."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    allow = patch(
        "generators.models.tools.fetch_url._is_blocked_ip", return_value=False)
    lookup = patch(
        "generators.models.tools.fetch_url.socket.getaddrinfo",
        return_value=_addrinfo("127.0.0.1", port))
    try:
        with allow, lookup as mock_getaddrinfo:
            sock = _connect(("public.example", port), 2)
        try:
            assert sock.getpeername() == ("127.0.0.1", port)
        finally:
            sock.close()
    finally:
        listener.close()

    assert mock_getaddrinfo.call_count == 1


@patch("generators.models.tools.fetch_url.socket.getaddrinfo")
def test_dns_answer_that_changes_after_the_check_is_refused(mock_getaddrinfo):
    # The first lookup is the URL check. The second lookup is the connection.
    mock_getaddrinfo.side_effect = [_public_addrinfo(), _addrinfo("127.0.0.1")]

    result = fetch_url({"url": "https://rebind.example/"})

    assert "refusing to connect to 'rebind.example'" in result
    assert mock_getaddrinfo.call_count == 2


def test_opener_connects_with_the_check_and_ignores_a_proxy(monkeypatch):
    monkeypatch.setenv("https_proxy", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    addresses = []

    def fake_connect(address, *args, **kwargs):
        addresses.append(address)
        raise OSError("stop")

    with patch("generators.models.tools.fetch_url._connect", fake_connect):
        with pytest.raises(urllib.error.URLError):
            _open(urllib.request.Request("https://example.com/"))

    assert addresses == [("example.com", 443)]
