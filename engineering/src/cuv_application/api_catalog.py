"""Pure descriptions of existing endpoints and explicitly missing adapters.

This is NOT an execution registry. No algorithm object or URL client escapes
this module, and no tool is registered with the frozen Agent or FastAPI app.
"""
from src.shared_contracts.tool_api import SchemaReference, ToolDefinition

TOOL_VERSION = "module-boundaries/1.0.0"


def _ref(name: str, *, shared=False) -> SchemaReference:
    return SchemaReference(document="module_contracts" if shared else "platform_openapi",
                           pointer=("#/$defs/" if shared else "#/components/schemas/") + name)


def list_tool_definitions() -> list[ToolDefinition]:
    # New instances prevent consumers from mutating a process-global registry.
    specs = [
        ("get_dataset_view", "GET", "/api/v1/datasets/{dataset_id}/view", "DatasetQuery", True,
         "DatasetView", False, "read_only", "existing_api_unbound",
         "Water snapshot topology and assessment coverage; schematic coordinates are not GIS."),
        ("get_health_assessment", "GET", "/api/v1/assessments", "AssessmentQuery", True,
         "AssessmentEnvelope", False, "read_only", "existing_api_unbound",
         "Legacy demo 0.1.1 Hs/Rs only; not formal H or failure probability."),
        ("get_cuv_capabilities", "GET", "/api/v1/capabilities", "NoInput", True,
         "CapabilitiesResponse", False, "read_only", "existing_api_unbound",
         "Returns current declared capabilities; formal CUV remains unavailable."),
        ("get_run", "GET", "/api/v1/runs/{run_id}", "RunQuery", True,
         "RunView", False, "read_only", "existing_api_unbound",
         "Run ownership and current associated dataset authorization are required."),
        ("get_run_report", "GET", "/api/v1/runs/{run_id}/report", "RunQuery", True,
         "ReportDocument", False, "read_only", "existing_api_unbound",
         "Read stored results only; identity conflict and incomplete Runs keep HTTP 409."),
        ("get_agent_run_evidence", "GET", "/api/v1/agent/runs/{run_id}/evidence", "RunQuery", True,
         "UnspecifiedApiResult", True, "read_only", "adapter_required",
         "Agent execution archive only; response has no declared stable OpenAPI DTO."),
        ("submit_scenario_analysis", "POST", "/api/v1/simulation-runs", "ScenarioRequest", False,
         "RunView", False, "creates_run", "existing_api_unbound",
         "Creates an async Run; given-isolation topology is not actual supply recovery."),
        ("submit_plan_comparison", "POST", "/api/v1/decision-runs", "DecisionRequest", False,
         "RunView", False, "creates_run", "existing_api_unbound",
         "Creates an async Run; compares given candidates, no dispatch or global optimum."),
        ("submit_health_assessment", "POST", "/api/v1/assessment-runs", "RecomputeRequest", False,
         "RunView", False, "creates_run", "existing_api_unbound",
         "Creates an async Run using water legacy 0.1.1, authorized synthetic inputs only."),
        ("check_timeline", "POST", "/api/v1/timeline/check", "TimelineRequest", False,
         "TimelineCheckResponse", False, "read_only", "existing_api_unbound",
         "No stored Run creation; platform run permission and snapshot authorization still apply."),
        ("evaluate_timeline", "POST", "/api/v1/timeline/evaluate", "TimelineRequest", False,
         "TimelineEvaluationResponse", False, "read_only", "existing_api_unbound",
         "Declared planned graph timeline; missing completion/recovery values remain partial/null."),
        ("get_asset", None, None, "DatasetQuery", True,
         "UnspecifiedApiResult", True, "read_only", "adapter_required",
         "No dedicated asset Tool API; needs an audited projection from DatasetView."),
        ("get_data_completeness", None, None, "DatasetQuery", True,
         "UnspecifiedApiResult", True, "read_only", "adapter_required",
         "No dedicated tool; coverage fields require an audited semantic projection."),
        ("get_gas_assessment", None, None, "DatasetQuery", True,
         "UnspecifiedApiResult", True, "read_only", "unavailable",
         "GAS-M0 candidate has no registered FastAPI adapter and is pending independent acceptance."),
    ]
    return [ToolDefinition(tool_name=name, tool_version=TOOL_VERSION,
                           domains=["gas"] if name == "get_gas_assessment" else ["water"],
                           input_schema=_ref(inp, shared=shared_in),
                           result_schema=_ref(out, shared=shared_out),
                           effect=effect, requires_confirmation=effect != "read_only",
                           binding_status=status, http_method=method, http_path=path,
                           limitations=[limitation, "Collaboration ToolApi adapter is not registered."])
            for name, method, path, inp, shared_in, out, shared_out, effect, status, limitation in specs]
