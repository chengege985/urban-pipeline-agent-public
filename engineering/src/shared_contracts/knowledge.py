"""Knowledge metadata and exact excerpt/hash contracts; no index or model I/O."""
from __future__ import annotations

import hashlib
from typing import Annotated, Literal
from pydantic import Field, model_validator
from .tool_api import ActorContext, DataMode, ID, StrictContract, Text

Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Sensitivity = Literal["public", "synthetic", "internal", "restricted", "unknown"]


class DocumentReference(StrictContract):
    document_id: ID
    document_version: Text
    corpus_id: ID
    source_uri: Text
    source_category: Literal["public_standard", "authorized_internal", "synthetic", "unconfirmed_draft"]
    data_mode: DataMode
    sensitivity: Sensitivity
    content_sha256: Sha256

    @model_validator(mode="after")
    def classification_conflicts(self):
        if self.source_category == "authorized_internal" and self.sensitivity not in {"internal", "restricted"}:
            raise ValueError("internal documents cannot be downgraded by a public/synthetic label")
        if self.source_category == "synthetic" and self.data_mode != "synthetic":
            raise ValueError("synthetic source category requires synthetic data mode")
        return self


class RetrievalRequest(StrictContract):
    request_id: ID
    actor_context: ActorContext
    question: Annotated[str, Field(min_length=1, max_length=4000)]
    question_sensitivity: Sensitivity
    allowed_corpus_ids: list[ID] = Field(max_length=64)
    limit: Annotated[int, Field(ge=1, le=50)]


class RetrievedChunk(StrictContract):
    """Validated text integrity is separate from authorization and entailment.

    A future adapter must authorize each source/version before returning it.
    Hash validation alone cannot establish trusted origin or prove an answer.
    """
    chunk_id: ID
    citation_id: ID
    evidence_id: Text
    document: DocumentReference
    original_text: str
    original_sha256: Sha256
    sent_text: str
    sent_sha256: Sha256
    truncated: bool

    @model_validator(mode="after")
    def exact_text_integrity(self):
        digest = lambda text: hashlib.sha256(text.encode("utf-8")).hexdigest()
        if digest(self.original_text) != self.original_sha256 or digest(self.sent_text) != self.sent_sha256:
            raise ValueError("content hashes must match the actual original and sent text")
        if not self.original_text.startswith(self.sent_text):
            raise ValueError("sent_text must be a literal prefix excerpt")
        if self.truncated != (self.sent_text != self.original_text):
            raise ValueError("truncated must match the actual excerpt")
        return self
