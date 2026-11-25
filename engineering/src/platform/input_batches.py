"""完整评分输入批次：不可变、内容寻址、按访问域和主体隔离。

此处只登记已通过门禁的原始记录。评分快照只包含部分属性，不能替代本批次。
批次文件不是在线数据库；未来AgentStore记录引用，本模块不依赖PostgreSQL。
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Literal

from pydantic import Field

from .schemas import Contract, ID, RecomputeRequest
from .store import Conflict, canonical, fingerprint

Scope = Literal["internal", "competition"]


class BatchPrincipal(Contract):
    """只由已认证服务端构造，不能直接从LLM或客户端role字段获得。"""
    actor_id: ID
    scope: Scope


class BatchInput(Contract):
    schema_version: Literal["agent-input-1"] = "agent-input-1"
    principal: BatchPrincipal
    request: RecomputeRequest


class BatchReference(Contract):
    batch_ref: str = Field(pattern=r"^batch-[0-9a-f]{64}$")
    sha256: str
    static_rows: int
    dynamic_rows: int
    evaluation_clock: str
    model_version: Literal["0.1.1"] = "0.1.1"


class InputBatchRepository:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.directory = self.root / "data/agent/input_batches"

    def _path(self, principal: BatchPrincipal, batch_ref: str) -> Path:
        if not re.fullmatch(r"batch-[0-9a-f]{64}", batch_ref):
            raise ValueError("非法批次引用，不接受文件路径")
        actor = hashlib.sha256(principal.actor_id.encode()).hexdigest()
        path = self.directory / principal.scope / actor / (batch_ref + ".json")
        if not path.resolve().is_relative_to(self.root):
            raise PermissionError("输入批次路径超出工程范围")
        return path

    def register(self, request: RecomputeRequest, principal: BatchPrincipal) -> BatchReference:
        """登记前校验完整输入；相同主体、内容重复登记不创建新批次。"""
        from src.adapters.scoring import validate_recompute
        if principal.scope == "competition" and request.data_mode != "synthetic":
            raise PermissionError("比赛批次只允许合成数据")
        value = BatchInput(principal=principal, request=request).model_dump(mode="json")
        encoded = canonical(value)
        if len(encoded.encode()) > 10 * 1024 * 1024:
            raise ValueError("输入批次超过10MiB")
        validate_recompute(request)
        digest = fingerprint(value)
        ref = "batch-" + digest
        path = self._path(principal, ref)
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {"sha256": digest, "payload": value, "created_at": datetime.now(timezone.utc).isoformat()}
        try:
            with path.open("x", encoding="utf-8") as stream:
                json.dump(record, stream, ensure_ascii=False, allow_nan=False)
        except FileExistsError:
            self.load(ref, principal)
        return BatchReference(batch_ref=ref, sha256=digest, static_rows=len(request.static_rows),
                              dynamic_rows=len(request.dynamic_rows), evaluation_clock=request.as_of.isoformat())

    def load(self, batch_ref: str, principal: BatchPrincipal) -> RecomputeRequest:
        """请求需匹配主体与域；文件完整性校验失败即拒绝，不返回半截输入。"""
        path = self._path(principal, batch_ref)
        if not path.is_file():
            raise KeyError("输入批次不存在或无权访问")
        record = json.loads(path.read_text(encoding="utf-8"))
        digest = fingerprint(record["payload"])
        if digest != record["sha256"] or batch_ref != "batch-" + digest:
            raise Conflict("输入批次内容与哈希不一致")
        batch = BatchInput.model_validate(record["payload"])
        if batch.principal != principal:
            raise PermissionError("输入批次归属不匹配")
        if principal.scope == "competition" and batch.request.data_mode != "synthetic":
            raise PermissionError("禁止在比赛域读取非合成输入")
        return batch.request
