from urllib.parse import quote

from app.discovery.adapters.base import JobSourceAdapter
from app.discovery.models import DiscoveredJob


class AshbyAdapter(JobSourceAdapter):
    source_name = "ashby"
    api_base = "https://api.ashbyhq.com/posting-api/job-board"

    async def fetch(self, board: str) -> list[DiscoveredJob]:
        payload = await self.get_json(f"{self.api_base}/{quote(board, safe='')}")
        results: list[DiscoveredJob] = []
        for item in payload.get("jobs", []):
            if item.get("isListed") is False:
                continue
            results.append(DiscoveredJob(
                employer=board,
                title=item["title"],
                original_url=item.get("jobUrl") or item["applyUrl"],
                source_url=f"{self.api_base}/{quote(board, safe='')}",
                description=item.get("descriptionPlain") or "",
                posted_at=item.get("publishedAt"),
                location=item.get("location", ""),
                workplace_type=item.get("workplaceType", ""),
                salary_text=(item.get("compensation") or {}).get("scrapeableCompensationSalarySummary", ""),
                source_name=self.source_name,
                source_id=item.get("jobUrl", item.get("applyUrl", "")),
            ))
        return results

