from urllib.parse import quote

from app.discovery.adapters.base import JobSourceAdapter
from app.discovery.models import DiscoveredJob


class LeverAdapter(JobSourceAdapter):
    source_name = "lever"
    api_base = "https://api.lever.co/v0/postings"

    async def fetch(self, board: str) -> list[DiscoveredJob]:
        payload = []
        skip = 0
        page_size = 100
        while True:
            page = await self.get_json(
                f"{self.api_base}/{quote(board, safe='')}",
                params={"mode": "json", "skip": str(skip), "limit": str(page_size)},
            )
            payload.extend(page)
            if len(page) < page_size:
                break
            skip += page_size
        results: list[DiscoveredJob] = []
        for item in payload:
            categories = item.get("categories") or {}
            results.append(DiscoveredJob(
                employer=board,
                title=item["text"],
                original_url=item.get("hostedUrl") or item["applyUrl"],
                source_url=f"{self.api_base}/{quote(board, safe='')}",
                description=item.get("descriptionPlain") or item.get("openingPlain") or "",
                # Lever's documented public postings response does not specify a publish timestamp.
                posted_at=None,
                location=categories.get("location", ""),
                workplace_type=item.get("workplaceType", ""),
                salary_text=item.get("salaryDescriptionPlain", ""),
                source_name=self.source_name,
                source_id=str(item["id"]),
            ))
        return results

