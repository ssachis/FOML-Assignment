"""URL validation for fetch_article. Runs BEFORE any request is made."""
import ipaddress
import socket
from urllib.parse import urlparse


class Rejected(Exception):
    """Raised when a URL fails a guardrail. Message is safe to show."""


def _bad_ip(ip: str) -> bool:
    a = ipaddress.ip_address(ip.split("%")[0])
    if getattr(a, "ipv4_mapped", None):
        a = a.ipv4_mapped
    return (a.is_loopback or a.is_private or a.is_link_local or a.is_unspecified
            or a.is_reserved or a.is_multicast)


def host_allowed(host: str, allowed: list) -> bool:
    if "*" in allowed:
        return True
    host = host.lower().rstrip(".")
    return any(host == h.lower() or host.endswith("." + h.lower()) for h in allowed)


def resolve_public(host: str, port: int) -> list:
    """Resolve and verify EVERY address. Returns the safe IPs (caller pins to one)."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise Rejected(f"host does not resolve: {host}")
    ips = sorted({i[4][0] for i in infos})
    if not ips:
        raise Rejected(f"host does not resolve: {host}")
    for ip in ips:
        if _bad_ip(ip):
            raise Rejected(f"host resolves to a non-public address ({ip})")
    return ips


def validate_url(url: str, cfg: dict):
    """Returns (parsed_url, safe_ips). Raises Rejected."""
    g = cfg["guardrails"]
    if not isinstance(url, str) or len(url) > 2048:
        raise Rejected("url must be a string under 2048 chars")
    p = urlparse(url.strip())
    if p.scheme.lower() not in [s.lower() for s in g["allowed_schemes"]] or p.scheme.lower() not in ("http", "https"):
        raise Rejected(f"scheme not allowed: {p.scheme or '(none)'}")
    if not p.hostname:
        raise Rejected("url has no host")
    if p.username or p.password:
        raise Rejected("credentials in url not allowed")
    if not host_allowed(p.hostname, g["allowed_hosts"]):
        raise Rejected(f"host not in allowed_hosts: {p.hostname}")
    port = p.port or (443 if p.scheme.lower() == "https" else 80)
    return p, resolve_public(p.hostname, port)
