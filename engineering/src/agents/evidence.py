"""工具事实与回答引用校验。

模型回答必须引用真实工具/检索证据；缺失或篡改时回退确定性事实摘要。
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import math
import re

NUMBER_RE = re.compile(r"(?<![\w.:-])-?\d+(?:,\d{3})*(?:\.\d+)?")
NUMBER_UNIT_RE = re.compile(
    r"(-?\d+(?:,\d{3})*(?:\.\d+)?)\s*(%|MPa|mg/L|NTU|m3/h|m³/h|元|户|个|处|分|小时|h|吨|人|点)"
)
UNSUPPORTED_CLAIMS = re.compile(
    r"(失效概率|爆管概率|漏损概率|概率\s*(?:为|约|达到)|剩余寿命|还能使用|使用寿命|RUL|"
    r"正式健康度|健康度\s*H(?![sS])|水力达标|水质达标|压力达标|流量达标)",
    re.I,
)
CHINESE_NUMERIC_RE = re.compile(
    r"(?:[零一二三四五六七八九十百千万两]+(?:分|年|%|小时|天|个月|成|到|至|以上|以下)|"
    r"(?:为|约|达到|是)\s*[零一二三四五六七八九十百千万两]+)")
DOC_VERSION_RE = re.compile(r"\bv\d+\b", re.I)
NEGATION = re.compile(
    r"(?:不能|不得|禁止|不要|不应|不是|未|没有|无法|尚未|并未|不)"
    r"(?:提供|给出|计算|预测|声称|宣称|代表|等于|是|能|会|支持|接入|包含|使用)?"
)
VERSION_RE = re.compile(r"(?<![\d.])\d+\.\d+\.\d+(?![\d.])")
UNIT_ALIASES = {"h": "小时", "m3/h": "m3/h", "m³/h": "m3/h"}
METRIC_ALIASES = {"健康分": "Hs", "健康度": "Hs", "Hs": "Hs", "风险指数": "Rs", "Rs": "Rs"}
METRIC_PATTERN = r"(Hs|Rs|健康分|健康度|风险指数)"
TOOL_EVIDENCE_RE = re.compile(r"^tool:[0-9a-f]{64}$")
VERIFIED_FACT_TOOLS = {
    "calculate_health": {"Hs": "分", "Rs": "无量纲"},
    "query_assessment": {"Hs": "分", "Rs": "无量纲"},
    "compare_action_plans": {"total_cost_fen": "分", "remaining_disconnected_services": "个",
                             "backup_covered_critical_services": "个", "restoration_hours": "小时"},
    "simulate_outage": {"household": "户", "person": "人", "facility": "处"},
}
_VERIFIED_FACT_TOKEN = object()
_RECEIPT_TOKEN = object()
RECEIPT_ISSUER = "domain-tools"


def _canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


@dataclass(frozen=True)
class ExecutionReceipt:
    """当前 run 的可信工具执行回执。

    不能直接构造：只能由受控执行器（``DomainTools.execute``）通过
    ``ExecutionLedger._register`` 登记后产生。回执绑定运行主体、run、快照、
    工具与工具版本、参数身份和结果摘要，仅表示“本次确实执行过该工具”。
    """

    run_id: str
    tool_name: str
    evidence_id: str
    tool_version: str
    status: str
    snapshot_id: str
    result_hash: str
    principal_id: str = ""
    arguments_hash: str = ""
    issuer: str = RECEIPT_ISSUER
    _token: object = field(default=None, repr=False, compare=False)

    def __post_init__(self):
        if self._token is not _RECEIPT_TOKEN:
            raise ValueError("执行回执只能由受控执行器登记")
        if not self.run_id or not self.evidence_id or not self.tool_version:
            raise ValueError("执行回执缺少 run/证据/工具版本")
        if not self.snapshot_id or not self.principal_id:
            raise ValueError("执行回执缺少快照或主体绑定")
        if self.tool_name not in VERIFIED_FACT_TOOLS:
            raise ValueError("执行回执的工具不在白名单")
        if not TOOL_EVIDENCE_RE.fullmatch(self.evidence_id):
            raise ValueError("执行回执的证据ID格式非法")
        if not re.fullmatch(r"[0-9a-f]{64}", self.result_hash or ""):
            raise ValueError("执行回执缺少结果哈希")
        if not re.fullmatch(r"[0-9a-f]{64}", self.arguments_hash or ""):
            raise ValueError("执行回执缺少参数身份哈希")
        if self.issuer != RECEIPT_ISSUER:
            raise ValueError("执行回执来源不可信")

    def as_dict(self) -> dict:
        return {"run_id": self.run_id, "tool_name": self.tool_name,
                "evidence_id": self.evidence_id, "tool_version": self.tool_version,
                "status": self.status, "snapshot_id": self.snapshot_id,
                "result_hash": self.result_hash, "principal_id": self.principal_id,
                "arguments_hash": self.arguments_hash, "issuer": self.issuer}


class ExecutionLedger:
    """本次图执行的工具执行账本（进程内）。

    没有公开签发入口：登记只由受控执行器在真实调用返回后通过 ``_register`` 完成。
    认证只查账本记录，不把可序列化字典、checkpoint 副本或调用方传入的回执当作
    执行证明。进程内任意代码仍可直接调用私有方法，Python 无法提供进程级隔离；
    本模块只保证公开 API 上没有“提交成功字典即获得可信签发”的路径。
    """

    def __init__(self):
        self._records: dict[tuple[str, str, str], ExecutionReceipt] = {}

    def _register(self, *, run_id: str, snapshot_id: str | None, principal_id: str | None,
                  tool_result: dict, arguments: dict | None = None) -> ExecutionReceipt:
        """仅由受控执行器在工具实际返回后调用。"""
        result = tool_result.get("result")
        if not isinstance(result, dict):
            raise ValueError("工具结果缺少 result，不能登记回执")
        receipt = ExecutionReceipt(
            run_id=str(run_id or ""), tool_name=str(tool_result.get("tool_name") or ""),
            evidence_id=str(tool_result.get("evidence_id") or ""),
            tool_version=str(tool_result.get("tool_version") or ""),
            status=str(tool_result.get("status") or ""),
            snapshot_id=str(snapshot_id or ""),
            principal_id=str(principal_id or ""),
            result_hash=hashlib.sha256(_canonical(result).encode("utf-8")).hexdigest(),
            arguments_hash=hashlib.sha256(_canonical(arguments or {}).encode("utf-8")).hexdigest(),
            _token=_RECEIPT_TOKEN)
        self._records[(receipt.run_id, receipt.tool_name, receipt.evidence_id)] = receipt
        return receipt

    def lookup(self, *, run_id: str, tool_name: str, evidence_id: str) -> ExecutionReceipt | None:
        return self._records.get((str(run_id or ""), str(tool_name or ""), str(evidence_id or "")))

    def records(self, run_id: str) -> list[ExecutionReceipt]:
        return [item for item in self._records.values() if item.run_id == run_id]


def _mask_ids(text: str) -> str:
    text = re.sub(r"\[(?:tool|doc):[^\]]+\]", " ", text)
    text = re.sub(r"\b(?:WP|SV|SC|ISO|PLAN|KD|V)-?\d+[A-Za-z0-9-]*\b", " ", text, flags=re.I)
    text = re.sub(r"\bsyn-[A-Za-z0-9-]+\b", " ", text, flags=re.I)
    text = re.sub(r"\b\d{4}-\d{2}-\d{2}(?:[T ][0-9:+.-]+)?\b", " ", text)
    text = re.sub(r"\b\d+\.\d+\.\d+\b", " ", text)
    text = re.sub(r"\[(?:P|T)\d{3,4}(?:R\d{3})?\]", " ", text)
    return text


def _normalize_number(token: str) -> str | None:
    try:
        value = float(token.replace(",", ""))
    except ValueError:
        return None
    return f"{value:.10g}"


def _numbers(text: str, *, critical: bool = False) -> set[str]:
    text = _mask_ids(text)
    values: set[str] = set()
    for match in NUMBER_RE.finditer(text):
        token = match.group()
        value = _normalize_number(token)
        if value is None:
            continue
        number = float(token.replace(",", ""))
        if critical and "." not in token and abs(number) < 1000:
            if 1900 <= number <= 2100:
                continue
        if critical and "." not in token and abs(number) < 1000:
            continue
        values.add(value)
    if critical:
        for match in NUMBER_UNIT_RE.finditer(text):
            value = _normalize_number(match.group(1))
            if value is not None:
                values.add(value)
    return values


def _canonical_unit(unit: str | None) -> str:
    if not unit:
        return ""
    return UNIT_ALIASES.get(unit, unit)


def _unit_claims(text: str) -> set[tuple[str, str]]:
    claims = set()
    for match in NUMBER_UNIT_RE.finditer(text):
        value = _normalize_number(match.group(1))
        if value is not None:
            claims.add((value, _canonical_unit(match.group(2))))
    return claims


def _version_tokens(text: str) -> set[str]:
    return set(VERSION_RE.findall(text))


@dataclass(frozen=True)
class VerifiedFact:
    fact_id: str
    subject_id: str
    metric: str
    value: float | None
    unit: str
    version: str
    evidence_id: str
    source_type: str
    verification_status: str
    tool_name: str = ""
    _trusted_token: object = field(default=None, repr=False, compare=False)

    def __post_init__(self):
        if self.verification_status != "verified":
            return
        if self._trusted_token is not _VERIFIED_FACT_TOKEN:
            raise ValueError("VerifiedFact 只能由受控工具结果构建")
        if not TOOL_EVIDENCE_RE.fullmatch(self.evidence_id) or not self.subject_id or not self.metric:
            raise ValueError("VerifiedFact 缺少可信工具来源、对象或指标")
        if not self.version or self.version == "unknown":
            raise ValueError("VerifiedFact 缺少明确版本")
        if self.value is None or isinstance(self.value, bool) or not math.isfinite(self.value):
            raise ValueError("VerifiedFact 数值缺失、为bool或非有限")
        if self.tool_name not in VERIFIED_FACT_TOOLS:
            raise ValueError("VerifiedFact 工具类型不在白名单")
        if VERIFIED_FACT_TOOLS[self.tool_name].get(self.metric) != self.unit:
            raise ValueError("VerifiedFact 指标与单位不匹配")
        if self.source_type != "tool":
            raise ValueError("VerifiedFact 只能来自已验证工具事实")
        expected = f"fact:{self.evidence_id}:{self.version}:{self.subject_id}:{self.metric}"
        if self.fact_id != expected:
            raise ValueError("VerifiedFact 的 fact_id 未绑定来源、版本、对象和指标")

    def as_dict(self) -> dict:
        return {"fact_id": self.fact_id, "subject_id": self.subject_id, "metric": self.metric,
                "value": self.value, "unit": self.unit, "version": self.version,
                "evidence_id": self.evidence_id, "source_type": self.source_type,
                "verification_status": self.verification_status, "tool_name": self.tool_name}


def verified_facts(facts: list[dict], *, run_id: str = "", snapshot_id: str = "",
                   principal_id: str = "", ledger: "ExecutionLedger | None" = None) -> list[VerifiedFact]:
    """Only current succeeded tool results with explicit versions become verified.

    认证只信受控执行器登记的账本记录，且必须同时满足：
    1. 记录属于当前 run、当前 snapshost 与当前主体；
    2. 工具名、工具版本与事实声明一致；
    3. 事实结果哈希与登记时的 result_hash 一致；
    4. 不接收任何外部审计副本——不存在可被忽略的字典或对象入参。
    """
    if not isinstance(ledger, ExecutionLedger) or not run_id or not snapshot_id or not principal_id:
        return []
    rows: list[VerifiedFact] = []
    for fact in facts:
        tool_name = str(fact.get("tool_name") or "")
        if tool_name not in VERIFIED_FACT_TOOLS:
            continue
        if fact.get("status") != "succeeded":
            continue
        evidence_id = str(fact.get("evidence_id") or "")
        result = fact.get("result", {})
        if not TOOL_EVIDENCE_RE.fullmatch(evidence_id) or result.get("status") != "succeeded":
            continue
        record = ledger.lookup(run_id=run_id, tool_name=tool_name, evidence_id=evidence_id)
        if record is None or record.status != "succeeded":
            continue
        if record.snapshot_id != snapshot_id or record.principal_id != principal_id:
            continue
        if str(fact.get("tool_version") or "") != record.tool_version:
            continue
        try:
            if hashlib.sha256(_canonical(result).encode("utf-8")).hexdigest() != record.result_hash:
                continue
        except (TypeError, ValueError):
            continue
        version = str(result.get("algorithm_version") or result.get("decision_algorithm_version")
                      or result.get("model_version") or "unknown")
        if tool_name in {"calculate_health", "query_assessment"}:
            assessments = list(result.get("assessments", []))
            if "assessment" in result:
                assessments.append(result["assessment"])
            for assessment in assessments:
                if not isinstance(assessment, dict) or not isinstance(assessment.get("pipe_id"), str):
                    continue
                legacy = assessment.get("legacy_result") or {}
                item_version = str(assessment.get("model_version") or version)
                if item_version == "unknown":
                    continue
                for metric, unit in VERIFIED_FACT_TOOLS[tool_name].items():
                    value = legacy.get(metric)
                    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)):
                        rows.append(VerifiedFact(
                            fact_id=f"fact:{evidence_id}:{item_version}:{assessment['pipe_id']}:{metric}",
                            subject_id=assessment["pipe_id"], metric=metric, value=float(value),
                            unit=unit, version=item_version, evidence_id=evidence_id,
                            source_type="tool", verification_status="verified", tool_name=tool_name,
                            _trusted_token=_VERIFIED_FACT_TOKEN))
        elif tool_name == "compare_action_plans":
            if version == "unknown":
                continue
            for plan in result.get("plans", []):
                plan_id = str(plan.get("plan_id") or "")
                if not plan_id:
                    continue
                for metric, unit in VERIFIED_FACT_TOOLS[tool_name].items():
                    value = (plan.get("metrics") or {}).get(metric)
                    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)):
                        rows.append(VerifiedFact(
                            fact_id=f"fact:{evidence_id}:{version}:{plan_id}:{metric}",
                            subject_id=plan_id, metric=metric, value=float(value), unit=unit,
                            version=version, evidence_id=evidence_id, source_type="tool",
                            verification_status="verified", tool_name=tool_name,
                            _trusted_token=_VERIFIED_FACT_TOKEN))
        elif tool_name == "simulate_outage":
            if version == "unknown":
                continue
            for metric, unit in VERIFIED_FACT_TOOLS[tool_name].items():
                value = (result.get("potentially_disconnected_counts") or {}).get(metric)
                if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)):
                    rows.append(VerifiedFact(
                        fact_id=f"fact:{evidence_id}:{version}:affected:{metric}",
                        subject_id="affected", metric=metric, value=float(value), unit=unit,
                        version=version, evidence_id=evidence_id, source_type="tool",
                        verification_status="verified", tool_name=tool_name,
                        _trusted_token=_VERIFIED_FACT_TOKEN))
    return rows

def _rag_claim_problems(answer: str, citations: list[dict], facts: list[VerifiedFact]) -> list[str]:
    """RAG prose may quote verified excerpts but may not author business numbers."""
    problems: list[str] = []
    if _numbers(answer, critical=True) or NUMBER_UNIT_RE.search(answer) or CHINESE_NUMERIC_RE.search(answer):
        problems.append("RAG回答包含未结构化绑定的关键数值；只能使用确定性事实或明确摘录")
    cited_versions = {str(c.get("document_version")) for c in citations if c.get("document_version")}
    cited_versions.update(item.version for item in facts if item.version != "unknown")
    for token in _version_tokens(answer):
        if token not in cited_versions:
            problems.append("RAG回答引用了来源中没有的算法版本：" + token)
    for token in DOC_VERSION_RE.findall(answer):
        if token.lower() not in {value.lower() for value in cited_versions}:
            problems.append("RAG回答引用了未提供的文档版本：" + token)
    return problems


def _structured_facts(facts: list[dict]) -> tuple[dict[str, dict], set[tuple[str, str]], set[str]]:
    """Index metrics by entity, value+unit, and algorithm version."""
    entities: dict[str, dict] = {}
    allowed_pairs: set[tuple[str, str]] = set()
    versions: set[str] = set()
    for fact in facts:
        result = fact.get("result", {})
        assessments = list(result.get("assessments", []))
        if "assessment" in result:
            assessments.append(result["assessment"])
        for assessment in assessments:
            if not isinstance(assessment, dict) or not isinstance(assessment.get("pipe_id"), str):
                continue
            legacy = assessment.get("legacy_result") or {}
            values = {}
            for metric, unit in (("Hs", "分"), ("Rs", "")):
                value = legacy.get(metric)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    normalized = _normalize_number(str(value))
                    values[metric] = (normalized, unit)
                    allowed_pairs.add((normalized, unit))
            entities[assessment["pipe_id"]] = {
                "metrics": values,
                "model_version": assessment.get("model_version"),
            }
            if assessment.get("model_version"):
                versions.add(str(assessment["model_version"]))
        for plan in result.get("plans", []):
            metrics = plan.get("metrics", {})
            for key, unit in (("total_cost_fen", "分"), ("remaining_disconnected_services", "个"),
                              ("backup_covered_critical_services", "个"), ("restoration_hours", "小时")):
                value = metrics.get(key)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    allowed_pairs.add((_normalize_number(str(value)), unit))
        for key, unit in (("household", "户"), ("person", "人"), ("facility", "处")):
            value = (result.get("potentially_disconnected_counts") or {}).get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                allowed_pairs.add((_normalize_number(str(value)), unit))
        for key in ("algorithm_version", "model_version", "version"):
            if result.get(key):
                versions.add(str(result[key]))
    return entities, allowed_pairs, versions


def _unsupported_claim_problems(answer: str) -> list[str]:
    problems = []
    for clause in re.split(r"[。！？;；，,\n]|但是|但本|然而", answer):
        for match in UNSUPPORTED_CLAIMS.finditer(clause):
            before = clause[:match.start()]
            after = clause[match.end():match.end() + 20]
            if NEGATION.search(before) or NEGATION.search(after):
                continue
            problems.append("回答宣称了未交付的专业能力：" + match.group())
    return problems


def _business_numeric_problems(answer: str, facts: list[dict]) -> list[str]:
    entities, allowed_pairs, versions = _structured_facts(facts)
    if not entities and not allowed_pairs:
        return []
    problems = []
    for pipe_id in re.findall(r"管段\s*([A-Za-z0-9_-]+)", answer):
        if pipe_id not in entities:
            problems.append("回答引用了工具事实中不存在的管段：" + pipe_id)
    claimed_spans: list[tuple[int, int]] = []
    claim_re = re.compile(
        r"管段\s*([A-Za-z0-9_-]+)[^。\n]{0,24}?" + METRIC_PATTERN +
        r"\s*[=:：]?\s*(-?\d+(?:\.\d+)?)\s*(分|MPa|%|小时|h)?")
    for match in claim_re.finditer(answer):
        claimed_spans.append((match.start(), match.end()))
        pipe_id, raw_metric, value, unit = match.groups()
        metric = METRIC_ALIASES[raw_metric]
        entity = entities.get(pipe_id)
        if entity is None:
            problems.append("回答把指标绑定到了不存在的管段：" + pipe_id)
            continue
        expected = entity["metrics"].get(metric)
        if expected is None:
            problems.append(f"管段{pipe_id}没有可核验的{metric}")
            continue
        normalized, expected_unit = expected
        if _normalize_number(value) != normalized:
            problems.append(f"管段{pipe_id}的{metric}值与工具事实不一致")
        if _canonical_unit(unit) != expected_unit:
            problems.append(f"管段{pipe_id}的{metric}单位与工具事实不一致")
    bare_re = re.compile(r"(?<![A-Za-z])" + METRIC_PATTERN +
                         r"\s*[=:：]\s*(-?\d+(?:\.\d+)?)\s*(分|MPa|%|小时|h)?")
    for match in bare_re.finditer(answer):
        if any(start <= match.start() < end for start, end in claimed_spans):
            continue
        raw_metric, value, unit = match.groups()
        metric = METRIC_ALIASES[raw_metric]
        normalized = _normalize_number(value)
        candidates = [pid for pid, entity in entities.items()
                      if entity["metrics"].get(metric, (None, ""))[0] == normalized]
        if len(candidates) != 1:
            problems.append(f"{metric}数值未唯一绑定到管段")
            continue
        expected_unit = entities[candidates[0]]["metrics"][metric][1]
        if _canonical_unit(unit) != expected_unit:
            problems.append(f"{metric}单位与工具事实不一致")
    if re.search(r"(?:Hs|Rs|健康分|健康度|风险指数)", answer) and not claim_re.search(answer) and not bare_re.search(answer):
        problems.append("回答包含未绑定数值的Hs/Rs")
    allowed_values = {value for value, _ in allowed_pairs}
    for value, unit in _unit_claims(answer):
        if value in allowed_values and (value, unit) not in allowed_pairs:
            problems.append(f"数值{value}的单位{unit or '无'}与工具事实不一致")
    for token in _version_tokens(answer):
        if token not in versions:
            problems.append("回答引用了工具事实中没有的算法版本：" + token)
    return problems


def fact_cards(tool_results: list[dict], citations: list[dict]) -> list[dict]:
    cards = []
    for item in tool_results:
        result = item.get("result", {})
        cards.append({"tool_name": item["tool_name"], "status": item.get("status", result.get("status", "unknown")),
                      "evidence_id": item["evidence_id"], "result": result,
                      # 工具版本是执行身份的一部分，必须随事实卡片传递。
                      "tool_version": item.get("tool_version"),
                      "scope": item.get("scope"), "execution_status": item.get("execution_status")})
    for citation in citations:
        cards.append({"tool_name": "knowledge_retrieval", "status": "succeeded",
                      "evidence_id": citation["evidence_id"],
                      "citation_id": citation.get("citation_id"),
                      "sensitivity": citation.get("sensitivity"),
                      "integrity_status": citation.get("integrity_status"),
                      "verification_status": citation.get("verification_status"),
                      "result": {"citation": citation}})
    return cards


def limitations_for(tool_results: list[dict]) -> list[str]:
    limits = ["当前为简化评分/演示策略，不是正式P/C/R/H、失效概率或业务验收。",
              "所有结论仅供人工复核，不控制实际阀门、不派发工单。"]
    for item in tool_results:
        result = item.get("result", {})
        limits.extend(result.get("limitations", []))
        vector_status = result.get("vector_status") or {}
        if vector_status and not vector_status.get("available") and vector_status.get("reason"):
            limits.append("向量索引不可用，已降级BM25：" + str(vector_status["reason"]))
        if result.get("status") in {"partial", "insufficient_data"}:
            limits.extend(str(x) for x in result.get("missing_items", [])[:8])
    return list(dict.fromkeys(limits))


def has_business_facts(facts: list[dict]) -> bool:
    """Business outputs use a bound renderer; RAG prose has a separate trust boundary."""
    return any(f.get("tool_name") not in {"knowledge_retrieval", None}
               or any(key in f.get("result", {}) for key in
                      ("assessment", "assessments", "plans", "Hs", "Rs", "affected_service_ids"))
               for f in facts)


def validate_answer(answer: str, facts: list[dict], citations: list[dict], *,
                   run_id: str = "", snapshot_id: str = "", principal_id: str = "",
                   ledger=None) -> tuple[bool, list[str]]:
    """至少引用一个真实证据ID，并核对关键数值；伪造ID或数值时拒绝。"""
    # This assistant has no dispatch/control tool. Reject affirmative execution claims,
    # while permitting quoted prohibitions and negative capability explanations.
    clauses = re.split(r"[。！？;；，,\n]|但是|但本|然而", answer)
    claimed = re.compile(r"(?:已经|已|完成了|成功)(?:成功)?(?:为您|为你|为用户|帮您|替您)?(?:执行|实施|完成)?(?:了)?(?:关阀|开阀|关闭阀门|打开阀门|派单|下发工单|下发指令)|(?:阀门|工单|控制指令).{0,12}(?:已经|已|成功)(?:被)?(?:关闭|打开|执行|下发|派发)|(?:关阀|开阀|派单|下发工单)(?:已)?(?:成功|完成)|(?:valve.{0,25}(?:has been|was) (?:closed|opened)|(?:have|has) (?:dispatched|issued))", re.I)
    prohibition = re.compile(r"(?:不能|不得|禁止|不要|不应)(?:声称|宣称|说|表示)|(?:尚未|并未|没有|未曾)(?:实际)?(?:关阀|开阀|派单|下发)")
    for clause in clauses:
        previous_end = 0
        for match in claimed.finditer(clause):
            # A prohibition only qualifies its own claim, not later affirmative
            # claims in the same sentence (e.g. "不得声称已关阀且系统已派单").
            if not prohibition.search(clause[previous_end:match.start()]):
                return False, ["回答宣称了当前工具并未执行的阀门操作或派单"]
            previous_end = match.end()
    unsupported = _unsupported_claim_problems(answer)
    if unsupported:
        return False, unsupported
    if any(c.get("integrity_status") in {"invalid_content_hash", "invalid_provenance"} for c in citations):
        return False, ["引用内容哈希或来源关系校验失败，不能作为可信证据"]
    valid_ids = {f["evidence_id"] for f in facts} | {c["evidence_id"] for c in citations}
    valid_ids.update(str(c["citation_id"]) for c in citations if c.get("citation_id"))
    if not valid_ids:
        return False, ["没有可用于回答的工具或检索证据"]
    referenced = re.findall(r"\[((?:tool|doc):[^\]\s]+)\]", answer)
    if not referenced:
        # 兼容不带方括号的旧模板/第三方输出，仍要求完整ID。
        referenced = [part.strip("[]，。；,.;:：") for part in answer.split()
                      if part.strip("[]，。；,.;:：").startswith(("tool:", "doc:"))]
    if not referenced:
        return False, ["回答没有引用证据ID"]
    unknown = [x for x in referenced if x not in valid_ids]
    if unknown:
        return False, ["回答引用了不存在的证据ID"]
    if has_business_facts(facts):
        binding = _business_numeric_problems(answer, facts)
        if binding:
            return False, binding
    else:
        rag_problems = _rag_claim_problems(answer, citations, verified_facts(
            facts, run_id=run_id, snapshot_id=snapshot_id, principal_id=principal_id, ledger=ledger))
        if rag_problems:
            return False, rag_problems
    source = json.dumps({"facts": facts, "citations": citations}, ensure_ascii=False)
    valid_numbers = _numbers(source)
    bad_numbers = sorted(_numbers(answer, critical=True) - valid_numbers)
    if bad_numbers:
        return False, ["回答包含工具事实中不存在的关键数值：" + "、".join(bad_numbers[:8])]
    return True, []


def _citation_identity_problems(citation: dict) -> list[str]:
    """复核引用身份本身：sent 片段、完整哈希、文档版本与 citation_id 必须互相对应。

    不能只相信 ``integrity_status`` / ``verification_status`` 标志：这些字段来自可变字典，
    引用身份、发送哈希和文档版本必须能重新算出来。
    """
    problems: list[str] = []
    content = citation.get("content")
    if not isinstance(content, str) or not content:
        return ["引用缺少发送正文"]
    sent_hash = citation.get("sent_hash")
    if not isinstance(sent_hash, str) or hashlib.sha256(content.encode("utf-8")).hexdigest() != sent_hash:
        problems.append("引用发送片段哈希缺失或不一致")
    sent_length = citation.get("sent_length")
    if isinstance(sent_length, int) and sent_length != len(content):
        problems.append("引用发送片段长度不一致")
    content_hash = str(citation.get("content_hash") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", content_hash):
        problems.append("引用内容哈希格式非法")
    truncated = bool(citation.get("content_truncated") or citation.get("sent_truncated"))
    if truncated:
        # 截断引用的 content_hash 是原文哈希；只能要求原文哈希与存储长度互相对应。
        source_hash = citation.get("source_hash")
        if isinstance(source_hash, str) and source_hash and source_hash != content_hash:
            problems.append("截断引用的来源哈希与内容哈希不一致")
        stored_length = citation.get("stored_length")
        if isinstance(stored_length, int) and stored_length <= len(content):
            problems.append("截断引用的存储长度不大于发送长度")
    elif hashlib.sha256(content.encode("utf-8")).hexdigest() != content_hash:
        problems.append("引用内容哈希与发送正文不一致")
    version = str(citation.get("document_version") or "")
    if not version:
        problems.append("引用缺少文档版本")
    location = str(citation.get("location") or "")
    document_id = str(citation.get("document_id") or "")
    if not document_id:
        evidence_id = str(citation.get("evidence_id") or "")
        parts = evidence_id.split(":")
        if len(parts) == 3 and parts[0] == "doc":
            document_id, location = parts[1], parts[2]
    citation_id = str(citation.get("citation_id") or "")
    expected = f"doc:{document_id}:{version}:{location}:{content_hash[:12]}"
    if not document_id or not location:
        problems.append("引用缺少文档或定位标识")
    elif citation_id != expected:
        problems.append("引用标识与文档版本/定位/哈希不一致")
    return problems


def answer_verification_status(answer: str, facts: list[dict], citations: list[dict]) -> str:
    """Classify RAG prose: direct excerpt vs unverified paraphrase.

    This is deliberately not claimed as semantic entailment. Unverified prose is
    always paired with a deterministic source excerpt before being shown.
    """
    if has_business_facts(facts):
        return "deterministic_bound"
    if any(citation.get("integrity_status") in {"invalid_content_hash", "invalid_provenance"}
           or citation.get("provenance_status") == "invalid_provenance" for citation in citations):
        return "invalid_evidence"
    if not citations:
        return "unverified_prose"
    refs = set(re.findall(r"\[((?:tool|doc):[^\]\s]+)\]", answer))
    if len(refs) != 1:
        return "unverified_prose"
    reference = next(iter(refs))
    matched = [c for c in citations
               if reference in {str(c.get("evidence_id") or ""), str(c.get("citation_id") or "")}]
    if len(matched) != 1:
        return "unverified_prose"
    citation = matched[0]
    if citation.get("integrity_status") != "verified":
        return "unverified_prose"
    if _citation_identity_problems(citation):
        return "invalid_evidence"
    content = str(citation.get("content") or "")
    clean = re.sub(r"\[(?:tool|doc):[^\]]+\]", "", answer).strip()
    for excerpt in citation.get("excerpts") or []:
        if not isinstance(excerpt, dict):
            continue
        start, end = excerpt.get("start"), excerpt.get("end")
        # 偏移必须是真实整数且在发送正文范围内；bool/浮点/负数/越界一律不采信。
        if isinstance(start, bool) or isinstance(end, bool):
            continue
        if not isinstance(start, int) or not isinstance(end, int):
            continue
        if not 0 <= start < end <= len(content):
            continue
        text = str(excerpt.get("text", "")).strip()
        if not text or content[start:end].strip() != text:
            continue
        if hashlib.sha256(text.encode("utf-8")).hexdigest() != excerpt.get("hash"):
            continue
        if clean == text:
            return "verified_excerpt"
    return "unverified_prose"


def deterministic_answer(question: str, facts: list[dict], citations: list[dict], limitations: list[str]) -> str:
    """Render each entity's metrics only from its own fact; no model-authored number relabeling.

    Missing values remain unknown. Units follow the tool contract, including cost in fen,
    graph reachability rather than hydraulic service, and legacy restoration time semantics.
    """
    def shown(value):
        return "未知" if value is None else value

    lines = [f"问题：{question}", "当前可核验结果："]
    seen = set()
    for fact in facts:
        eid = fact["evidence_id"]
        if eid in seen:
            continue
        seen.add(eid)
        lines.append(f"- [{eid}] {fact['tool_name']}：{fact['status']}")
        result = fact.get("result", {})
        assessments = result.get("assessments", [])
        if "assessment" in result:
            assessments = [result["assessment"]]
        for assessment in assessments:
            legacy = assessment.get("legacy_result", {})
            version = assessment.get("model_version") or legacy.get("model_version") or "未知"
            lines.append(f"  管段{assessment['pipe_id']}：Hs={shown(legacy.get('Hs'))}分，"
                         f"Rs={shown(legacy.get('Rs'))}（无量纲），等级{shown(legacy.get('grade'))}，"
                         f"模型 legacy_demo/{version}。 [{eid}]")
            explanation = result.get("explanations", {}).get(assessment["pipe_id"], {})
            if explanation.get("status") == "succeeded":
                lines.append(f"    有效指标权重占比：{explanation['legacy_completeness_percent']:.4g}%；不是准确率或概率。 [{eid}]")
                rows = {row["indicator"]: row for row in explanation["indicators"]}
                for name in explanation["deduction_rank"][:3]:
                    deduction = rows[name]["health_deduction_points"]
                    lines.append(f"    管段{assessment['pipe_id']}的{name}健康扣分贡献：{deduction:.6g}分。 [{eid}]")
                if explanation["missing_indicators"]:
                    lines.append("    未评价指标：" + "、".join(explanation["missing_indicators"]))
        if "affected_service_ids" in result:
            ids = result["affected_service_ids"]
            lines.append(f"  通路受影响服务对象：{len(ids)}个；对象ID：{', '.join(ids) or '无'}。 [{eid}]")
            for unit, value in result.get("potentially_disconnected_counts", {}).items():
                name = {"household": "户", "person": "人", "facility": "处"}.get(unit, unit)
                lines.append(f"  已登记受影响服务的{name}计数：{shown(value)}。 [{eid}]")
            if result.get("potentially_disconnected_counts"):
                lines.append("  计数仅覆盖已登记的服务映射；0不证明人口/设施映射完整，也不代表实际零人受影响。")
        for plan in result.get("plans", []):
            lines.append(f"  方案{plan['plan_id']}：状态{plan['status']}，"
                         f"固定配置效用{plan.get('utility') if plan.get('utility') is not None else '未知'}。 [{eid}]")
            metrics = plan.get("metrics", {})
            labels = {"total_cost_fen": "总费用（分）", "remaining_disconnected_services": "通路仍受影响服务（个）",
                      "backup_covered_critical_services": "备用覆盖关键服务（个）",
                      "restoration_hours": "旧口径网络动作最晚生效时间（小时；不是首次恢复时间）"}
            for key, label in labels.items():
                if key in metrics:
                    value = metrics[key]
                    lines.append(f"    {label}：{'未知或不适用' if value is None else value}。 [{eid}]")
            if plan.get("reasons"):
                lines.append("    条件说明：" + "；".join(plan["reasons"]))
        if "recommended_plan_id" in result:
            lines.append(f"  演示推荐：{result.get('recommended_plan_id') or '无'}；需人工复核。 [{eid}]")
        if result.get("missing_items"):
            lines.append("  缺少或未通过校验：" + "；".join(map(str, result["missing_items"])))
    if citations:
        lines.append("资料依据（未结构化摘录，不作为业务数值）：")
        for citation in citations:
            lines.append(
                f"- [{citation['evidence_id']}] {citation.get('document_title', '')} / "
                f"{citation.get('title', '')} {citation.get('location', '')} "
                f"version={citation.get('document_version', '')} "
                f"hash={citation.get('content_hash', '')} "
                f"truncated={citation.get('content_truncated')}")
            lines.append(f"  {(citation.get('content') or citation.get('snippet') or '')[:500]}")
    if limitations:
        lines.append("限制：" + "；".join(dict.fromkeys(limitations)))
    return "\n".join(lines)
