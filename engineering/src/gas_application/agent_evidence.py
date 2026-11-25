"""Read-only evidence identity shared by the two gas assistant entry points."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict

from .data_package import Digest

PILOT_REFERENCE = re.compile(
    r"(?<![A-Za-z0-9_.-])(?:demo-v\d+[A-Za-z0-9_.-]*|syn-gas-pilot-[A-Za-z0-9_.-]+)(?![A-Za-z0-9_.-])",
    re.I,
)
PILOT_HISTORY = re.compile(r"试点(?:历史|监测|数据集|回放)|pilot\s+(?:replay|dataset|history)", re.I)
PUBLIC_BENCHMARK_REFERENCE = re.compile(r"weymouth|韦茅斯|韦默斯|公开(?:单管|稳态|求解|验证)?基准|公开算例", re.I)
GAS_M0_REFERENCE = re.compile(
    r"(?<![A-Za-z0-9_-])(?:GAS-M0|syn-gas-(?:case|scenario|network|isolation|event|monitoring-replay|cuv)-[A-Za-z0-9_.-]+)"
    r"(?![A-Za-z0-9_.-])", re.I,
)
ENGINEERING_CLAIM = re.compile(
    r"(?:真实|实际|现场|正式).{0,8}(?:复气|供气|泄漏|风险分)|供气恢复(?:时间|时长)|"
    r"泄漏(?:概率|风险|量|率)|水力(?:预测|计算|校核|推演)"
)


class GasAgentEvidenceScope(BaseModel):
    """Digest of the source for this scope, never a join key by itself."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scope: Literal["gas_m0_case", "gas_m0_monitoring", "gas_m0_graph_cuv", "pilot_history", "public_reference_benchmark"]
    dataset_id: str
    dataset_version: str
    data_mode: Literal["synthetic", "real_authorized"]
    source_sha256: Digest


class GasAgentCaseBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    network_content_sha256: Digest
    case_content_sha256: Digest
