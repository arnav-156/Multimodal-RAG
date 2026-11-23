"""Local API and inference trust-boundary primitives."""
import ipaddress
import os
from pathlib import Path
import secrets
import socket
import threading
from urllib.parse import urlsplit

from config_loader import get_config, get_db_path

_token_lock = threading.Lock()


def get_api_token():
    """Read an environment/private-file secret; never return it in config HTML."""
    token = os.environ.get("RAG_API_TOKEN", "")
    if token:
        if len(token) < 32:
            raise ValueError("RAG_API_TOKEN must contain at least 32 characters")
        return token
    with _token_lock:
        return _file_api_token()


def _file_api_token():
    p = Path(get_db_path()).parent / ".api_token"
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(p, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        token = p.read_text(encoding="utf-8").strip()
    else:
        token = secrets.token_urlsafe(32)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(token + "\n")
    if len(token) < 32:
        raise ValueError("Stored API token is invalid; set RAG_API_TOKEN or repair the token file")
    return token


def validate_host_origin(host, origin=None, scheme="http", cfg=None):
    cfg = cfg if cfg is not None else get_config()
    parsed = urlsplit("//" + host)
    try:
        parsed.port
    except ValueError:
        raise ValueError("Invalid Host header") from None
    if not parsed.hostname or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment or any(c.isspace() for c in host) or parsed.hostname not in cfg["security"]["allowed_hosts"]:
        raise ValueError("Untrusted Host header")
    if origin:
        if origin == "null":
            raise ValueError("Opaque origins are not authorized")
        if origin != f"{scheme}://{host}" and origin not in cfg["server"]["cors_origins"]:
            raise ValueError("Cross-origin request is not authorized")


def validate_endpoint(url, cfg=None, cloud_authorized=False, *, live_policy=False):
    """Guard each HTTP call before requests can send any document bytes.

    Local-only mode accepts loopback literals and localhost, not arbitrary DNS
    aliases that could be rebound between validation and the HTTP lookup.
    """
    cfg = cfg if cfg is not None else get_config()
    endpoint = urlsplit(url)
    try:
        port = endpoint.port or (443 if endpoint.scheme == "https" else 80)
    except ValueError:
        raise ValueError("Invalid inference endpoint port") from None
    if endpoint.scheme not in ("http", "https") or not endpoint.hostname or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment:
        raise ValueError("Inference endpoints require HTTP(S), no embedded credentials/query/fragment")
    hostname = endpoint.hostname
    try:
        local = ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        local = hostname == "localhost"
        if local:
            try:
                addresses = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
                local = bool(addresses) and all(ipaddress.ip_address(row[4][0]).is_loopback for row in addresses)
            except OSError:
                raise ValueError("Cannot resolve local inference endpoint") from None
    if not local and (cfg["security"]["local_only"] or not cfg["security"]["allow_cloud"] or not cloud_authorized):
        raise ValueError("Non-loopback inference requires local_only=false and explicit cloud authorization")
    if not local and live_policy:
        from config_loader import get_live_config
        live = get_live_config()
        embedding = live['embedding']
        embedding_url = (embedding['api_url'] or embedding['vllm']['api_url']).rstrip('/')
        authorized = (embedding_url == url.rstrip('/') and embedding['cloud_authorized']) or any(
            endpoint['enabled'] and endpoint['cloud_authorized'] and endpoint['api_base'].rstrip('/') == url.rstrip('/')
            for endpoint in live['vision']['endpoints'])
        if live['security']['local_only'] or not live['security']['allow_cloud'] or not authorized:
            raise ValueError("Current settings revoked non-loopback inference authorization")
    return url
