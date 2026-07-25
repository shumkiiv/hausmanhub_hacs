from __future__ import annotations

from dataclasses import dataclass

from .ai_assistant_advisory import AiAdvisory
from .ai_assistant_types import AiAssistantViolation
from .ai_assistant_usage import AiUsageStats


@dataclass(frozen=True, slots=True)
class AiAssistantState:
    last_advisory: AiAdvisory | None = None
    stats: AiUsageStats = AiUsageStats()

    def __post_init__(self) -> None:
        if self.last_advisory is not None and not isinstance(
            self.last_advisory, AiAdvisory
        ):
            raise AiAssistantViolation("invalid_ai_assistant_state")
        if not isinstance(self.stats, AiUsageStats):
            raise AiAssistantViolation("invalid_ai_assistant_state")
