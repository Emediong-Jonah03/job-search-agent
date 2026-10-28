from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
import re
from xml.etree import ElementTree

from app.discovery.adapters.base import JobSourceAdapter
from app.discovery.models import DiscoveredJob


FEED_URLS = {
    "remoteok": "https://remoteok.com/api",
    "remotive": "https://remotive.com/api/remote-jobs?limit=50",
    "jobicy": "https://jobicy.com/api/v2/remote-jobs?count=200",
    "weworkremotely": "https://weworkremotely.com/remote-jobs.rss",
}


def _text(value: object, limit: int | None = None) -> str:
    text = unescape(re.sub(r"<[^>]+>", " ", str(value or "")))
    text = " ".join(text.split())
    return text[:limit] if limit else text


def _date(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(str(value))
        except (TypeError, ValueError, OverflowError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class RemoteOKAdapter(JobSourceAdapter):
    source_name = "remoteok"

    async def fetch(self, board: str = "") -> list[DiscoveredJob]:
        payload = await self.get_json(FEED_URLS[self.source_name])
        results = []
        for item in payload if isinstance(payload, list) else []:
            if not item.get("position") or not item.get("company") or not item.get("url"):
                continue  # The first API item contains feed terms, not a vacancy.
            results.append(DiscoveredJob(
                employer=_text(item.get("company"), 200) or "Unknown employer",
                title=_text(item.get("position"), 200),
                original_url=item["url"],
                source_url="https://remoteok.com/api",
                description=_text(item.get("description")),
                posted_at=_date(item.get("date")),
                location=_text(item.get("location"), 300),
                workplace_type="remote",
                salary_text=" ".join(str(item.get(key) or "") for key in ("salary_min", "salary_max")).strip(),
                source_name=self.source_name,
                source_id=str(item.get("id") or item.get("slug") or item["url"]),
            ))
        return results


class RemotiveAdapter(JobSourceAdapter):
    source_name = "remotive"

    async def fetch(self, board: str = "") -> list[DiscoveredJob]:
        payload = await self.get_json(FEED_URLS[self.source_name])
        results = []
        for item in payload.get("jobs", []) if isinstance(payload, dict) else []:
            url = item.get("url")
            if not url or not item.get("title"):
                continue
            results.append(DiscoveredJob(
                employer=_text(item.get("company_name"), 200) or "Unknown employer",
                title=_text(item.get("title"), 200),
                original_url=url,
                source_url="https://remotive.com/remote-jobs/api",
                description=_text(item.get("description")),
                posted_at=_date(item.get("publication_date")),
                location=_text(item.get("candidate_required_location"), 300),
                workplace_type="remote",
                salary_text=_text(item.get("salary"), 200),
                source_name=self.source_name,
                source_id=str(item.get("id") or url),
            ))
        return results


class JobicyAdapter(JobSourceAdapter):
    source_name = "jobicy"

    async def fetch(self, board: str = "") -> list[DiscoveredJob]:
        payload = await self.get_json(FEED_URLS[self.source_name])
        results = []
        for item in payload.get("jobs", []) if isinstance(payload, dict) else []:
            url = item.get("url")
            title = item.get("jobTitle")
            if not url or not title:
                continue
            salary = " ".join(str(item.get(key) or "") for key in ("salaryMin", "salaryMax", "salaryCurrency", "salaryPeriod"))
            results.append(DiscoveredJob(
                employer=_text(item.get("companyName"), 200) or "Unknown employer",
                title=_text(title, 200),
                original_url=url,
                source_url="https://jobicy.com/api/v2/remote-jobs",
                description=_text(item.get("jobDescription") or item.get("jobExcerpt")),
                posted_at=_date(item.get("pubDate")),
                location=_text(item.get("jobGeo"), 300),
                workplace_type="remote",
                salary_text=_text(salary, 200),
                source_name=self.source_name,
                source_id=str(item.get("id") or url),
            ))
        return results


class WeWorkRemotelyAdapter(JobSourceAdapter):
    source_name = "weworkremotely"

    def __init__(self, get_json, get_text=None):
        super().__init__(get_json)
        self.get_text = get_text

    async def fetch(self, board: str = "") -> list[DiscoveredJob]:
        if self.get_text is None:
            raise RuntimeError("RSS text reader is not configured")
        xml = await self.get_text(FEED_URLS[self.source_name])
        root = ElementTree.fromstring(xml)
        results = []
        for item in root.findall(".//item"):
            title = _text(item.findtext("title"), 200)
            url = _text(item.findtext("link"), 2048)
            description = _text(item.findtext("description"))
            if not title or not url:
                continue
            employer, role = "Employer not specified", title
            match = re.match(r"^(.*?)\s+at\s+(.+)$", title, re.I)
            if match:
                role, employer = match.group(1).strip(), match.group(2).strip()
            else:
                match = re.match(r"^(.+?)\s*[:|—-]\s*(.+)$", title)
                if match:
                    employer, role = match.group(1).strip(), match.group(2).strip()
            results.append(DiscoveredJob(
                employer=_text(employer, 200),
                title=_text(role, 200),
                original_url=url,
                source_url="https://weworkremotely.com/remote-jobs.rss",
                description=description,
                posted_at=_date(item.findtext("pubDate")),
                location="Remote; confirm eligible countries in the listing",
                workplace_type="remote",
                source_name=self.source_name,
                source_id=_text(item.findtext("guid"), 500) or url,
            ))
        return results
