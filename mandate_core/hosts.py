"""Exact hostname extraction for network destinations."""

from __future__ import annotations

import ipaddress
import re
import socket
from urllib.parse import urlsplit

DEFAULT_PORTS = {"http": 80, "https": 443, "ws": 80, "wss": 443, "ssh": 22, "git": 9418, "ftp": 21,
                 "sftp": 22, "git+ssh": 22, "ssh+git": 22}
_HOST_OK = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*$")
_NUMERICISH = re.compile(r"^(0x[0-9a-f]+|[0-9]+)(\.(0x[0-9a-f]+|[0-9]+)){0,3}$")


def invalid(raw: str) -> str:
    return f"<invalid-host:{raw[:60]}>"


def normalize_host(host: str) -> str | None:
    """Return the canonical ASCII host, or None if it cannot be trusted."""
    h = host.strip()
    if not h or any(c in h for c in "\\%\t\r\n /@") or any(ord(c) < 32 for c in h):
        return None
    if h.startswith("[") and h.endswith("]"):
        try:
            return "[" + ipaddress.IPv6Address(h[1:-1]).compressed + "]"
        except ValueError:
            return None
    h = h.rstrip(".").lower()
    try:
        return ipaddress.IPv6Address(h).compressed if ":" in h else str(ipaddress.IPv4Address(h))
    except ValueError:
        pass
    if _NUMERICISH.match(h):
        try:
            return socket.inet_ntoa(socket.inet_aton(h))  # 2130706433, 0x7f.1 -> 127.0.0.1
        except OSError:
            return None
    if not h.isascii():
        try:
            h = h.encode("idna").decode("ascii")
        except UnicodeError:
            return None
    if not _HOST_OK.match(h) or len(h) > 253:
        return None
    return h


def host_of(url: str) -> str:
    """Canonical ``host`` or ``host:port`` for a URL; an ``<invalid-host:...>`` marker otherwise."""
    if not isinstance(url, str) or any(c in url for c in "\\\t\r\n") or any(ord(c) < 32 for c in url):
        return invalid(str(url))
    try:
        parts = urlsplit(url.strip())
        netloc = parts.netloc
        scheme = parts.scheme.lower()
        port = parts.port
    except ValueError:
        return invalid(url)
    if "@" in netloc:
        netloc = netloc.rsplit("@", 1)[1]
    if not netloc:
        return invalid(url)
    hostpart = netloc
    if hostpart.startswith("["):
        hostpart = hostpart[: hostpart.find("]") + 1]
    elif ":" in hostpart:
        hostpart = hostpart.rsplit(":", 1)[0]
    host = normalize_host(hostpart)
    if host is None:
        return invalid(url)
    if port is not None and port != DEFAULT_PORTS.get(scheme):
        return f"{host}:{port}"
    return host


def host_match(rule: str, host: str) -> bool:
    if host.startswith("<invalid"):
        return False
    rule = rule.lower()
    if rule.startswith("*."):
        base = rule[2:]
        name = host.split(":", 1)[0] if not host.startswith("[") else host
        return name.endswith("." + base) and ":" not in host[len(name):]
    return rule == host
