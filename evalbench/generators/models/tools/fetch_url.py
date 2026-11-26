"""URL-fetching tool for the LLM judge.

Fetches an HTTPS URL, strips HTML to text, and returns the result so the
judge can ground rubric questions in external state.

Defense in depth: HTTPS only, SSRF guard against private/loopback/link-local
hosts (also for each redirect target, and for the address of each
connection), 10s timeout, 50KB body cap.
"""

from html.parser import HTMLParser
from typing import Any, Dict, Optional
from urllib.parse import urlparse
import http.client
import ipaddress
import logging
import socket
import urllib.error
import urllib.request

from .base import Tool

_TIMEOUT_SECONDS = 10
_MAX_BYTES = 50_000
_USER_AGENT = "evalbench-judge/1.0"


class _TextExtractor(HTMLParser):
    """Strips tags and collapses whitespace. Skips script/style content."""

    _SKIP_TAGS = frozenset({"script", "style", "noscript", "svg"})

    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag in self._SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data):
        if self._skip_depth == 0:
            self._chunks.append(data)

    def text(self) -> str:
        return " ".join("".join(self._chunks).split())


def _is_blocked_ip(ip_str: str) -> bool:
    """True if ip_str is a private/loopback/link-local address, or no IP."""
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True
    return (ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_multicast or ip.is_reserved)


def _is_blocked_host(host: str) -> bool:
    """True if host resolves to a private/loopback/link-local address."""
    try:
        addr_info = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return True
    return any(_is_blocked_ip(sockaddr[0]) for _, _, _, _, sockaddr in addr_info)


def _url_error(url: str) -> Optional[str]:
    """Returns the reason to refuse url, or None if the URL is allowed."""
    parsed = urlparse(url)
    if parsed.scheme != "https":
        return f"only https URLs are allowed (got scheme '{parsed.scheme}')"
    if not parsed.hostname:
        return "URL has no hostname"
    if _is_blocked_host(parsed.hostname):
        return (
            f"refusing to fetch '{parsed.hostname}' "
            "(private, loopback, link-local, or unresolvable)"
        )
    return None


class _CheckedRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Applies the URL checks to each redirect target."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        error = _url_error(newurl)
        if error:
            raise urllib.error.URLError(f"redirect refused: {error}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _connect(address, timeout=None, source_address=None):
    """Connects to an address of the host that passed the check.

    http.client resolves the host name again for the connection, and the DNS
    answer can change after the URL check. This function resolves the name
    one time, checks each address, and connects to one of those addresses.
    """
    host, port = address
    addr_info = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    for _, _, _, _, sockaddr in addr_info:
        if _is_blocked_ip(sockaddr[0]):
            raise OSError(
                f"refusing to connect to '{host}' "
                "(private, loopback, or link-local address)")
    error = None
    for family, socktype, proto, _, sockaddr in addr_info:
        sock = None
        try:
            sock = socket.socket(family, socktype, proto)
            if isinstance(timeout, (int, float)):
                sock.settimeout(timeout)
            if source_address:
                sock.bind(source_address)
            sock.connect(sockaddr)
            return sock
        except OSError as e:
            error = e
            if sock is not None:
                sock.close()
    raise error or OSError(f"no address for '{host}'")


class _CheckedHTTPSConnection(http.client.HTTPSConnection):
    """Connects with _connect. TLS still verifies the host name."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_connection = _connect


class _CheckedHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(
            _CheckedHTTPSConnection, req, context=self._context)


def _open(request):
    """Opens request. Checks each redirect target and each connection."""
    opener = urllib.request.build_opener(
        # A proxy resolves the host name itself, so the address check cannot
        # apply. This tool does not use a proxy.
        urllib.request.ProxyHandler({}),
        _CheckedHTTPSHandler,
        _CheckedRedirectHandler,
    )
    return opener.open(request, timeout=_TIMEOUT_SECONDS)


def fetch_url(args: Dict[str, Any]) -> str:
    url = args.get("url", "")
    if not isinstance(url, str) or not url:
        return "Error: missing required arg 'url' (string)"

    error = _url_error(url)
    if error:
        return f"Error: {error}"

    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with _open(request) as response:
            raw = response.read(_MAX_BYTES + 1)
            content_type = response.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        return f"Error: HTTP {e.code} fetching {url}"
    except urllib.error.URLError as e:
        return f"Error: URL error fetching {url}: {e.reason}"
    except (TimeoutError, socket.timeout):
        return f"Error: timeout after {_TIMEOUT_SECONDS}s fetching {url}"
    except Exception as e:
        logging.exception("fetch_url unexpected failure for %s", url)
        return f"Error: {type(e).__name__}: {e}"

    truncated = len(raw) > _MAX_BYTES
    body = raw[:_MAX_BYTES]
    try:
        text = body.decode("utf-8", errors="replace")
    except Exception:
        text = body.decode("latin-1", errors="replace")

    if "html" in content_type.lower() or "<html" in text[:200].lower():
        extractor = _TextExtractor()
        try:
            extractor.feed(text)
            text = extractor.text()
        except Exception:
            # HTML stripping is best-effort: on a malformed document we
            # fall back to the raw decoded body so the judge still has
            # *something* to reason about.
            logging.debug(
                "fetch_url HTML extraction failed; falling back to raw text",
                exc_info=True,
            )

    if truncated:
        text += f"\n\n[truncated at {_MAX_BYTES} bytes]"
    return text


FETCH_URL_TOOL = Tool(
    name="fetch_url",
    description=(
        "Fetch the contents of an HTTPS URL and return it as text. "
        "HTML is stripped to plain text. Only public HTTPS URLs are "
        "allowed; private, loopback, and non-HTTPS hosts are rejected. "
        "Response is capped at 50000 bytes."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "The fully-qualified HTTPS URL to fetch.",
            },
        },
        "required": ["url"],
    },
    fn=fetch_url,
)
