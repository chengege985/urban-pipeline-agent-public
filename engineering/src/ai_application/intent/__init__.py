"""Intent recognition port; all outputs are untrusted intent hypotheses."""
from typing import Protocol
from src.shared_contracts.intent import IntentDraft


class IntentRecognizer(Protocol):
    def recognize(self, question: str, *, request_id: str) -> IntentDraft: ...
