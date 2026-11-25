from typing import Protocol
from src.shared_contracts.knowledge import RetrievedChunk, RetrievalRequest


class KnowledgeRetriever(Protocol):
    """Reauthorize each corpus/version before search or returning text.

    The request carries server-associated metadata, not a trust credential. An
    implementation must resolve it using trusted injection. Empty permission
    sets must return no text, and arbitrary question text remains unknown until
    classified independently from the corpus. Reranking is not implemented.
    """
    def search(self, request: RetrievalRequest) -> list[RetrievedChunk]: ...
