from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import Any

from app.discovery.models import DiscoveredJob

JsonGetter = Callable[..., Awaitable[Any]]


class JobSourceAdapter(ABC):
    source_name: str

    def __init__(self, get_json: JsonGetter):
        self.get_json = get_json

    @abstractmethod
    async def fetch(self, board: str) -> list[DiscoveredJob]:
        """Fetch published jobs from one configured employer board."""

