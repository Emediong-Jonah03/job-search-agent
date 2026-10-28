from abc import ABC, abstractmethod

from app.llm.models import ProviderResult


class LLMProvider(ABC):
    provider_name: str

    @abstractmethod
    async def analyze_job_description(self, description: str) -> ProviderResult:
        """Extract a validated, structured summary from an untrusted job description."""

