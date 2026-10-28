import asyncio
import random
from typing import Any

class SourceRequestError(RuntimeError):
    pass


async def get_json(url: str, *, params: dict[str, str] | None = None) -> Any:
    """GET public ATS data with a timeout and bounded retry/backoff."""
    import httpx

    timeout = httpx.Timeout(15.0, connect=5.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False, headers={"Accept": "application/json", "User-Agent": "JobApplicationAgent/0.1 (public ATS job-board reader)"}) as client:
        for attempt in range(4):
            try:
                response = await client.get(url, params=params)
                if response.status_code == 429 or response.status_code >= 500:
                    if attempt < 3:
                        await asyncio.sleep(min(8.0, 0.5 * (2**attempt) + random.random() * 0.2))
                        continue
                response.raise_for_status()
                return response.json()
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
                response = await client.get(url)
                if response.status_code == 429 or response.status_code >= 500:
                    if attempt < 3:
                        await asyncio.sleep(min(8.0, 0.5 * (2**attempt) + random.random() * 0.2))
                        continue
                response.raise_for_status()
                return response.text
            except httpx.HTTPStatusError as exc:
                raise SourceRequestError(f"Public RSS feed returned HTTP {exc.response.status_code}") from exc
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                if attempt == 3:
                    raise SourceRequestError("Public RSS feed request failed after retries") from exc
                await asyncio.sleep(min(8.0, 0.5 * (2**attempt) + random.random() * 0.2))
    raise SourceRequestError("Public RSS feed request failed")

