import asyncio
import json
import random
from typing import Any

class SourceRequestError(RuntimeError):
    pass


async def _read_bounded(response, *, limit: int) -> bytes:
    chunks = []
    total = 0
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total > limit:
            raise SourceRequestError("Public job source response exceeded the configured size limit")
        chunks.append(chunk)
    return b"".join(chunks)


async def get_json(url: str, *, params: dict[str, str] | None = None) -> Any:
    """GET public ATS data with a timeout and bounded retry/backoff."""
    import httpx

    timeout = httpx.Timeout(15.0, connect=5.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False, headers={"Accept": "application/json", "User-Agent": "JobApplicationAgent/0.1 (public ATS job-board reader)"}) as client:
        for attempt in range(4):
            try:
                async with client.stream("GET", url, params=params) as response:
                    if response.status_code == 429 or response.status_code >= 500:
                        if attempt < 3:
                            await response.aclose()
                            await asyncio.sleep(min(8.0, 0.5 * (2**attempt) + random.random() * 0.2))
                            continue
                    response.raise_for_status()
                    content = await _read_bounded(response, limit=10 * 1024 * 1024)
                    return json.loads(content)
            except httpx.HTTPStatusError as exc:
                raise SourceRequestError(f"ATS source returned HTTP {exc.response.status_code}") from exc
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                if attempt == 3:
                    raise SourceRequestError("ATS source request failed after retries") from exc
                await asyncio.sleep(min(8.0, 0.5 * (2**attempt) + random.random() * 0.2))
    raise SourceRequestError("ATS source request failed")


async def get_text(url: str) -> str:
    """GET a configured public RSS feed with a timeout and bounded retry/backoff."""
    import httpx

    timeout = httpx.Timeout(15.0, connect=5.0)
    headers = {"Accept": "application/rss+xml, application/xml, text/xml", "User-Agent": "JobApplicationAgent/0.1 (public job-feed reader)"}
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False, headers=headers) as client:
        for attempt in range(4):
            try:
                async with client.stream("GET", url) as response:
                    if response.status_code == 429 or response.status_code >= 500:
                        if attempt < 3:
                            await response.aclose()
                            await asyncio.sleep(min(8.0, 0.5 * (2**attempt) + random.random() * 0.2))
                            continue
                    response.raise_for_status()
                    content = await _read_bounded(response, limit=5 * 1024 * 1024)
                    return content.decode(response.encoding or "utf-8", errors="replace")
            except httpx.HTTPStatusError as exc:
                raise SourceRequestError(f"Public RSS feed returned HTTP {exc.response.status_code}") from exc
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                if attempt == 3:
                    raise SourceRequestError("Public RSS feed request failed after retries") from exc
                await asyncio.sleep(min(8.0, 0.5 * (2**attempt) + random.random() * 0.2))
    raise SourceRequestError("Public RSS feed request failed")

