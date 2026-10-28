from urllib.parse import quote

from app.discovery.adapters.base import JobSourceAdapter
from app.discovery.models import DiscoveredJob


class GreenhouseAdapter(JobSourceAdapter):
    source_name = "greenhouse"
    api_base = "https://boards-api.greenhouse.io/v1/boards"

    async def fetch(self, board: str) -> list[DiscoveredJob]:
        payload = await self.get_json(f"{self.api_base}/{quote(board, safe='')}/jobs", params={"content": "true"})
        results: list[DiscoveredJob] = []
        for item in payload.get("jobs", []):
            if item.get("internal_job_id") is None:  # Ignore prospect posts, not published openings.
                continue
            location = (item.get("location") or {}).get("name", "")
            results.append(DiscoveredJob(
                employer=item.get("company_name") or board,
                title=item["title"],
                original_url=item["absolute_url"],
                source_url=f"{self.api_base}/{quote(board, safe='')}/jobs",
                description=item.get("content") or "",
                # Prefer explicit first_published where the board exposes it; updated_at is not equivalent.
                posted_at=item.get("first_published"),
                location=location,
                source_name=self.source_name,
                source_id=str(item["id"]),
                raw_metadata={"updated_at": item.get("updated_at"), "requisition_id": item.get("requisition_id")},
            ))
        return results

