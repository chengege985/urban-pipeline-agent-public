"""Trusted server-side data authorization for platform read paths.

Principals come from server configuration, never from a client header. A shared
developer token authenticates the caller but does not automatically grant real
datasets: real mode also requires an explicit mode and dataset mapping.

Local demo mode is unauthenticated and only covers synthetic data by default.
`fixture_replay` and `real` must be enabled explicitly by the deployment.
"""
from __future__ import annotations

from dataclasses import dataclass
import hmac
import json
import os
from typing import Any

from fastapi import HTTPException, Request

from .provenance import SyntheticProvenance, verify_assessment_request

REAL_MODE = "real"
DEMO_MODES = {"synthetic"}
GENERIC_DENIAL = "当前主体无权读取该数据集"
DEFAULT_PERMISSIONS = frozenset({"run", "review", "resume", "import"})
LOCAL_ANONYMOUS_FLAG_NAMES = ("ALLOW_LOCAL_SYNTHETIC_ANONYMOUS", "WATER_ALLOW_LOCAL_SYNTHETIC_ANONYMOUS")
LOCAL_RUNTIME_MODES = frozenset({"local", "development"})
FORMAL_RUNTIME_MODES = frozenset({"controlled", "staging", "production"})
LOOPBACK_BIND_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


@dataclass(frozen=True)
class Principal:
    actor_id: str
    authenticated: bool
    modes: frozenset[str]
    datasets: frozenset[str] | None = None
    scope: str = "internal"
    corpora: frozenset[str] = frozenset()
    permissions: frozenset[str] = DEFAULT_PERMISSIONS



def _env_true(*names: str) -> bool:
    return any(os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"} for name in names)


def local_synthetic_anonymous_enabled() -> bool:
    """Return true only for an explicit loopback-only synthetic demo configuration."""
    return (
        _env_true(*LOCAL_ANONYMOUS_FLAG_NAMES)
        and os.getenv("WATER_RUNTIME_MODE", "").strip().lower() in LOCAL_RUNTIME_MODES
        # Require the binding to be explicitly declared. The app cannot infer
        # the host used by an externally launched ASGI server, so an implicit
        # loopback default must not enable anonymous access.
        and os.getenv("WATER_BIND_HOST", "").strip().lower() in LOOPBACK_BIND_HOSTS
        and not os.getenv("WATER_API_TOKEN", "").strip()
        and not os.getenv("WATER_PRINCIPALS", "").strip()
    )


def validate_security_configuration() -> None:
    """Reject an explicitly controlled deployment that has no server ACL."""
    raw_principals = os.getenv("WATER_PRINCIPALS", "").strip()
    if raw_principals:
        _principals()
    runtime_mode = os.getenv("WATER_RUNTIME_MODE", "").strip().lower()
    has_server_auth = bool(os.getenv("WATER_API_TOKEN", "").strip() or raw_principals)
    if runtime_mode in FORMAL_RUNTIME_MODES and not has_server_auth and not local_synthetic_anonymous_enabled():
        raise RuntimeError("受控运行模式缺少服务端认证配置")
    if _env_true(*LOCAL_ANONYMOUS_FLAG_NAMES) and not local_synthetic_anonymous_enabled() and runtime_mode in FORMAL_RUNTIME_MODES:
        raise RuntimeError("匿名合成例外仅允许loopback本地运行模式")


def _list_env(name: str, default: str = "") -> list[str]:
    raw = os.getenv(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


def _principals() -> dict[str, Any]:
    raw = os.getenv("WATER_PRINCIPALS", "").strip()
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("WATER_PRINCIPALS 不是合法 JSON") from exc
    if not isinstance(value, dict):
        raise RuntimeError("WATER_PRINCIPALS 必须是 token 到主体的映射")
    return value


def _string_set(value) -> frozenset[str]:
    # A malformed ACL never means unrestricted access.
    if not isinstance(value, list) or any(not isinstance(x, str) or not x.strip() for x in value):
        return frozenset()
    return frozenset(value)


def _configured_principal(entry) -> Principal:
    if not isinstance(entry, dict):
        raise HTTPException(401, "未认证或令牌无效")
    actor = entry.get("actor_id")
    scope = entry.get("scope", "internal")
    if (not isinstance(actor, str) or not actor.strip() or len(actor) > 128
            or actor in {"local-demo", "legacy-unverified"} or scope not in {"internal", "competition"}):
        raise HTTPException(403, "服务端主体配置无效")
    return Principal(actor_id=actor, authenticated=True, modes=_string_set(entry.get("modes", [])),
                     datasets=None if "datasets" not in entry else _string_set(entry["datasets"]),
                     scope=scope, corpora=_string_set(entry.get("corpora", [])),
                     permissions=_string_set(entry.get("permissions", sorted(DEFAULT_PERMISSIONS))))


def _developer_principal() -> Principal:
    return Principal(actor_id="dev-token", authenticated=True,
                     modes=frozenset(_list_env("WATER_DEV_MODES", "synthetic")),
                     datasets=(frozenset(_list_env("WATER_DEV_DATASETS"))
                               if "WATER_DEV_DATASETS" in os.environ else None),
                     corpora=frozenset(_list_env("WATER_DEV_CORPORA")),
                     permissions=frozenset(_list_env("WATER_DEV_PERMISSIONS", ",".join(sorted(DEFAULT_PERMISSIONS)))))


def resolve_actor(actor_id: str, scope: str) -> Principal:
    """Resolve a persisted identity against *current* server ACLs, without a token copy.

    Workers cannot revive a revoked identity from the queue. Multiple token entries
    for the same actor/scope are deliberately rejected until configuration is unique.
    """
    try:
        principals = _principals()
    except RuntimeError:
        # A live configuration reload must fail closed without leaking parser details.
        # BFF callers translate this into a sanitized 503 and invalidate the session.
        raise HTTPException(503, "认证配置不可用") from None
    if principals:
        entries = [entry for entry in principals.values() if isinstance(entry, dict)
                   and entry.get("actor_id") == actor_id and entry.get("scope", "internal") == scope]
        if len(entries) != 1:
            raise HTTPException(403, "主体已撤销或身份配置不唯一")
        return _configured_principal(entries[0])
    if os.getenv("WATER_API_TOKEN") and (actor_id, scope) == ("dev-token", "internal"):
        return _developer_principal()
    raise HTTPException(403, "主体已撤销或未认证")


def require_authenticated(request: Request) -> Principal:
    principal = resolve_principal(request)
    if not principal.authenticated:
        raise HTTPException(401, "未完成服务端身份认证")
    return principal


def require_permission(principal: Principal, action: str) -> None:
    if not principal.authenticated:
        raise HTTPException(401, "未完成服务端身份认证")
    if action not in principal.permissions:
        raise HTTPException(403, "当前主体无权执行此操作")


def resolve_principal(request: Request) -> Principal:
    """Resolve the caller from server configuration only."""
    token = request.headers.get("X-Water-Token", "")
    try:
        principals = _principals()
    except RuntimeError:
        # Configuration is server-owned input. Do not expose malformed JSON or
        # a traceback through an API response if a process reload races with a
        # bad configuration update; startup validation still fails closed.
        raise HTTPException(503, "认证配置不可用") from None
    if principals:
        return _configured_principal(principals.get(token))

    configured = os.getenv("WATER_API_TOKEN", "")
    if configured:
        if not hmac.compare_digest(token, configured):
            raise HTTPException(401, "未认证或令牌无效")
        return _developer_principal()

    if local_synthetic_anonymous_enabled():
        # This is a deliberately narrow development exception: loopback,
        # explicit local mode, synthetic-only, read-only permissions.
        return Principal(
            actor_id="local-demo",
            authenticated=False,
            modes=frozenset(DEMO_MODES),
            datasets=None,
            scope="local",
            permissions=frozenset(),
        )
    raise HTTPException(401, "未完成服务端身份认证")


def snapshot_allowed(principal: Principal, snapshot: dict[str, Any], *, store=None) -> bool:
    dataset_id = snapshot.get("snapshot_id")
    mode = snapshot.get("data_mode")
    if not isinstance(dataset_id, str) or not isinstance(mode, str):
        return False
    if mode not in principal.modes:
        return False
    if not principal.authenticated and mode != "synthetic":
        return False
    if mode == REAL_MODE and (not principal.authenticated or principal.datasets is None):
        return False
    if principal.datasets is not None and dataset_id not in principal.datasets:
        # A synthetic recompute creates a new content-addressed output. Only its
        # authenticated owner gets a persisted, result-bound derived grant.
        return bool(mode == "synthetic" and principal.authenticated and store is not None
                    and store.has_generated_snapshot(principal, dataset_id))
    return True


def authorize_snapshot(principal: Principal, snapshot: dict[str, Any], *, store=None) -> None:
    """Raise a generic 403 without echoing snapshot content."""
    if not snapshot_allowed(principal, snapshot, store=store):
        raise HTTPException(403, GENERIC_DENIAL)


def authorize_dataset(principal: Principal, dataset_id: str, mode: str) -> None:
    """Require server-side mode and dataset authorization, with the bounded local exception."""
    if not principal.authenticated:
        if mode == "synthetic" and local_synthetic_anonymous_enabled():
            return
        raise HTTPException(401, "未完成服务端身份认证")
    if mode not in principal.modes:
        raise HTTPException(403, GENERIC_DENIAL)
    if principal.datasets is None or dataset_id not in principal.datasets:
        raise HTTPException(403, GENERIC_DENIAL)


def authorize_assessment(principal: Principal, request: dict[str, Any]) -> SyntheticProvenance:
    """Require a request to match a server-verified synthetic fixture.

    Client data_mode and source_refs are not authority.  The worker repeats this
    check before calculating so queued input cannot become trusted after it is
    changed or its fixture is revoked.
    """
    require_permission(principal, "run")
    if request.get("data_mode") != "synthetic" or "synthetic" not in principal.modes:
        raise HTTPException(403, "内存重算只接收获准的合成输入；其它来源缺少受控数据集绑定")
    return verify_assessment_request(request)


def authorize_run(principal: Principal, run: dict[str, Any], store) -> None:
    """Authorize both run ownership and its associated snapshot.

    Dataset visibility alone is insufficient: two principals may both read a
    synthetic snapshot but must not read each other's private Run/Report.
    """
    if not principal.authenticated:
        raise HTTPException(401, "未完成服务端身份认证")
    run_id = run.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        raise HTTPException(403, GENERIC_DENIAL)
    try:
        owner_actor, owner_scope = store.task_owner(run_id)
    except (KeyError, TypeError):
        raise HTTPException(403, GENERIC_DENIAL) from None
    if owner_actor in {"local-demo", "legacy-unverified"} or principal.actor_id != owner_actor or principal.scope != owner_scope:
        raise HTTPException(403, GENERIC_DENIAL)
    request = run.get("request") if isinstance(run.get("request"), dict) else {}
    result = run.get("result") if isinstance(run.get("result"), dict) else {}
    required_corpora = result.get("required_corpora", [])
    if not isinstance(required_corpora, list) or any(not isinstance(x, str) for x in required_corpora):
        raise HTTPException(403, GENERIC_DENIAL)
    required = set(required_corpora)
    if run.get("kind") == "assistant" and result.get("citations"):
        # Historical legacy outputs predate explicit corpus tags. They are not
        # public merely because their associated network snapshot is synthetic.
        required.add("legacy-materials")
    if not required.issubset(principal.corpora):
        raise HTTPException(403, GENERIC_DENIAL)
    if run.get("kind") == "assessment" and not request.get("snapshot_id"):
        # The owned task is already a protected resource while queued/running;
        # its output snapshot does not exist yet. Do not make polling depend on it.
        if request.get("data_mode") != "synthetic" or "synthetic" not in principal.modes:
            raise HTTPException(403, GENERIC_DENIAL)
        return
    snapshot_id = request.get("snapshot_id") or result.get("snapshot_id")
    if not isinstance(snapshot_id, str) or not snapshot_id:
        raise HTTPException(403, "该运行记录未关联可授权数据集")
    try:
        snapshot = store.get_snapshot(snapshot_id)
    except KeyError as exc:
        raise HTTPException(403, GENERIC_DENIAL) from exc
    authorize_snapshot(principal, snapshot, store=store)


def run_allowed(principal: Principal, run: dict[str, Any], store) -> bool:
    try:
        authorize_run(principal, run, store)
        return True
    except HTTPException:
        return False
