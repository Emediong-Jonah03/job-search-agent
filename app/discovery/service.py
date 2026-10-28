from datetime import datetime, timedelta, timezone
from typing import Iterable
from urllib.parse import urlsplit

from app.discovery.models import BoardConfig, DiscoveredJob, LinkCheck
from app.discovery.normalization import normalize_url
from app.matching.scorer import MatchResult, score_job
from app.domain.models import Profile


TRACKED_ATS_HOSTS = {"boards.greenhouse.io", "job-boards.greenhouse.io", "jobs.lever.co", "jobs.eu.lever.co", "jobs.ashbyhq.com"}


def parse_board_config(raw: str) -> list[BoardConfig]:
    """Parse semicolon-delimited source:board entries from DISCOVERY_BOARDS."""
    boards = []
    for entry in filter(None, (piece.strip() for piece in raw.split(";"))):
        source, separator, board = entry.partition(":")
        source = source.strip().casefold()
        if not separator or source not in {"greenhouse", "lever", "ashby"} or not board.strip():
            raise ValueError(f"Invalid discovery board entry: {entry!r}")
        boards.append(BoardConfig(source=source, board=board.strip()))
    return boards


def fresh_jobs(jobs: Iterable[DiscoveredJob], *, now: datetime | None = None, days: int = 30) -> tuple[list[DiscoveredJob], list[DiscoveredJob]]:
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    eligible, excluded = [], []
    for job in jobs:
        posted_at = job.posted_at
        if posted_at is not None and cutoff <= posted_at <= now:
            eligible.append(job)
        else:
            excluded.append(job)
    return eligible, excluded


async def check_live_link(job: DiscoveredJob) -> LinkCheck:
    """Check a supported ATS-hosted original posting. Unknown hosts stay unverified."""
    url = str(job.original_url)
    parts = urlsplit(url)
    host = (parts.hostname or "").casefold()
    if parts.scheme != "https" or host not in TRACKED_ATS_HOSTS or parts.port not in (None, 443):
        return LinkCheck(url=url, accessible=False, reason="Original link host is not in the supported ATS link allowlist")
    import httpx

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0), follow_redirects=False) as client:
            response = await client.get(url, headers={"User-Agent": "JobApplicationAgent/0.1 (public posting check)"})
        return LinkCheck(url=url, accessible=200 <= response.status_code < 300, status_code=response.status_code, reason="ATS posting returned a successful HTTP status" if 200 <= response.status_code < 300 else "ATS posting is unavailable, redirecting, or rejected the request")
    except httpx.HTTPError:
        return LinkCheck(url=url, accessible=False, reason="ATS posting request failed")


def rank_job(job: DiscoveredJob, profile: Profile) -> MatchResult:
    return score_job(job, profile)


def dedupe_key(job: DiscoveredJob) -> tuple[str, str, str]:
    from app.discovery.normalization import identity_parts

    employer_key, title_key = identity_parts(job.employer, job.title)
    return normalize_url(str(job.original_url)), employer_key, title_key

