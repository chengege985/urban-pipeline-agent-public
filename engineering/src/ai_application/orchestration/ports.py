from dataclasses import dataclass
from src.ai_application.intent import IntentRecognizer
from src.ai_application.retrieval.ports import KnowledgeRetriever
from src.shared_contracts.tool_api import ControlledToolApi

WORKFLOW_STAGES = (
    "user_input", "intent_draft", "authorization_and_parameters",
    "retrieval_or_controlled_tool", "evidence_aggregation",
    "reply_generation", "human_confirmation_when_required",
)


@dataclass(frozen=True)
class WorkflowPorts:
    """Dependency injection description, not a graph executor or grant.

    A future state machine must also gate every side effect *before* dispatch;
    final human confirmation of an explanation cannot authorize prior writes.
    """
    intent: IntentRecognizer
    retrieval: KnowledgeRetriever
    tools: ControlledToolApi
