"""Compatibility exports from the single collaboration contract package."""
from src.shared_contracts.intent import IntentDraft, TargetObject, TimeConstraint
from src.shared_contracts.knowledge import DocumentReference, RetrievedChunk, RetrievalRequest
from src.shared_contracts.tool_api import ActorContext, ToolCall, ToolResult

__all__ = ["ActorContext", "DocumentReference", "IntentDraft", "RetrievedChunk",
           "RetrievalRequest", "TargetObject", "TimeConstraint", "ToolCall", "ToolResult"]
