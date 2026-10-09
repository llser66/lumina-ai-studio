"""Validate user-supplied OpenAI-compatible endpoints before credential dispatch.

URL/DNS checks alone do not prevent DNS rebinding. Call ``resolve_custom_endpoint``
again for each operation, then dispatch with the returned addresses pinned by the
HTTPS transport (original hostname for TLS/SNI and Host). Never follow redirects.
"""

import ipaddress
import socket
from urllib.parse import urlsplit, urlunsplit

from fastapi import HTTPException

_TRANSITION_NETWORKS = (
    ipaddress.ip_network("64:ff9b::/96"),
    ipaddress.ip_network("64:ff9b:1::/48"),
    ipaddress.ip_network("2002::/16"),
    ipaddress.ip_network("2001::/32"),
)


def _invalid(message="自定义接口仅支持可公开访问的 HTTPS 地址"):
    # Do not include the submitted URL: it may contain accidentally pasted secrets.
    return HTTPException(422, message)


def _public_address(value):
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        raise _invalid() from None
    if (
        not address.is_global
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
        or address.is_loopback
        or address.is_link_local
    ):
        raise _invalid()
    if isinstance(address, ipaddress.IPv6Address) and (
        address.ipv4_mapped or any(address in network for network in _TRANSITION_NETWORKS)
    ):
        raise _invalid()
    return address.compressed


def resolve_custom_endpoint(url: str) -> tuple[str, list[str]]:
    """Return the normalized URL and all verified public IPs; fail closed.

    The caller must pin one of these IPs for each HTTPS connection, keep certificate
    validation enabled, and preserve the normalized URL hostname for TLS/SNI/Host.
    Validation should run outside the event loop because DNS resolution is blocking.
    """
    if (
        not isinstance(url, str)
        or not url
        or len(url) > 2048
        or any(character.isspace() or ord(character) < 32 for character in url)
        or any(character in url for character in ("\\", "?", "#"))
    ):
        raise _invalid()
    try:
        parsed = urlsplit(url)
        hostname = (parsed.hostname or "").rstrip(".").lower()
        port = parsed.port if parsed.port is not None else 443
    except ValueError:
        raise _invalid() from None
    if (
        parsed.scheme != "https"
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or "%" in hostname
        or not 1 <= port <= 65535
        or hostname == "localhost"
        or hostname.endswith((".localhost", ".local", ".home.arpa"))
    ):
        raise _invalid()
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    if address is not None:
        addresses = [_public_address(str(address))]
        hostname = address.compressed
        authority = f"[{hostname}]" if address.version == 6 else hostname
    else:
        try:
            hostname = hostname.encode("idna").decode("ascii")
        except UnicodeError:
            raise _invalid() from None
        # No percent-encoding or empty DNS labels; avoid parser disagreements.
        if any(not label for label in hostname.split(".")) or any(
            character not in "abcdefghijklmnopqrstuvwxyz0123456789-." for character in hostname
        ):
            raise _invalid()
        try:
            answers = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
        except OSError:
            raise _invalid("无法解析自定义接口域名，请检查地址或网络") from None
        addresses = list(dict.fromkeys(_public_address(answer[4][0]) for answer in answers))
        if not addresses:
            raise _invalid("无法解析自定义接口域名，请检查地址或网络")
        authority = hostname
    if port != 443:
        authority += f":{port}"
    normalized = urlunsplit(("https", authority, parsed.path.rstrip("/"), "", ""))
    return normalized, addresses


def validate_custom_endpoint(url: str) -> str:
    """Validate the URL and its DNS answers, returning the normalized base URL."""
    return resolve_custom_endpoint(url)[0]
