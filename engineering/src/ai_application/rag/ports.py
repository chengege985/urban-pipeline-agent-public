from typing import Protocol
from src.shared_contracts.knowledge import DocumentReference
from src.shared_contracts.tool_api import ActorContext


class DocumentIndex(Protocol):
    """Explicit authorized provisioning only; never called directly from text.

    A future implementation must own registration, chunk/version replacement,
    embedding policy, index deletion and permission-revocation lifecycle. It
    must not send restricted text to an unapproved embedding/vector service.
    These methods have no implementation and are not exposed as Agent tools.
    """
    def register(self, document: DocumentReference, *, actor: ActorContext) -> None: ...
    def revoke(self, document_id: str, *, actor: ActorContext) -> None: ...
