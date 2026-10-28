import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


TRACKING_KEYS = {"fbclid", "gclid", "mc_cid", "mc_eid", "ref", "referrer"}


def normalize_url(url: str) -> str:
    """Canonicalize URLs while preserving job-identifying query parameters."""
    parts = urlsplit(url.strip())
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
        raise ValueError("Job URL must be an absolute HTTP(S) URL")
    host = parts.hostname.encode("idna").decode("ascii").lower()
    if host.startswith("www."):
        host = host[4:]
    port = parts.port
    netloc = host if port is None or (parts.scheme.lower(), port) in {("http", 80), ("https", 443)} else f"{host}:{port}"
    path = re.sub(r"/{2,}", "/", parts.path or "/").rstrip("/") or "/"
    kept = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        normalized_key = key.lower()
        if normalized_key.startswith("utm_") or normalized_key in TRACKING_KEYS or normalized_key in {"gh_src", "lever-source"}:
            continue
        kept.append((key, value))
    query = urlencode(sorted(kept, key=lambda pair: (pair[0].lower(), pair[1])))
    return urlunsplit((parts.scheme.lower(), netloc, path, query, ""))


def identity_parts(employer: str, title: str) -> tuple[str, str]:
    """Normalize employer/title strings for database-level job identity."""
    clean = lambda value: " ".join(re.sub(r"[^\w\s]", " ", value.casefold()).split())
    return clean(employer), clean(title)

