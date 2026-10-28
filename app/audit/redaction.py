import re
from typing import Any


SENSITIVE_KEY = re.compile(r"(password|secret|token|cookie|authorization|api[_-]?key|session)", re.I)


def redact(value: Any) -> Any:
    """Return a JSON-safe copy with secret-like values and unnecessary contact data masked."""
    if isinstance(value, dict):
        return {key: "[REDACTED]" if SENSITIVE_KEY.search(str(key)) else redact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        value = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[EMAIL]", value)
        value = re.sub(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]", value)
        return value
    return value

