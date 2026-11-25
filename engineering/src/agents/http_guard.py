"""模型出口的不可变目标绑定。

检查对象与真实 HTTP 出口必须一致：本模块在 httpx 传输层按**实际请求 URL**校验
scheme/host/port/path 前缀，并强制不跟随重定向。仅校验模型对象的 endpoint 属性
不足以证明请求发往何处，构造后修改客户端或 SDK base_url 也必须被拒绝。
"""
from __future__ import annotations

from urllib.parse import urlparse

import httpx

from src.agents.outbound import _endpoint_allowed, normalize_endpoint


def split_endpoint(value: str) -> tuple[str, str]:
    """返回 (origin, path)，均来自规范化端点。"""
    normalized = normalize_endpoint(value)
    parsed = urlparse(normalized)
    return f"{parsed.scheme}://{parsed.netloc}", parsed.path.rstrip("/") or "/"


def _require_target(url: str, origin: str, base_path: str) -> None:
    """只允许同一 origin 且位于批准路径之下；任何编码/参数/重定向迹象直接拒绝。"""
    if not isinstance(url, str) or not url:
        raise PermissionError("模型请求缺少目标URL")
    if any(char in url for char in ("%", "\\", "\x00", "\r", "\n", "\t", ";")):
        raise PermissionError("模型请求URL含编码/参数分隔符或控制字符")
    parsed = urlparse(url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise PermissionError("模型请求URL的scheme或host非法")
    if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.params:
        raise PermissionError("模型请求URL不得含凭据、参数、查询或片段")
    if any(segment == ".." for segment in parsed.path.split("/")):
        raise PermissionError("模型请求URL含上级目录")
    actual_origin = f"{parsed.scheme.lower()}://{parsed.hostname.lower()}:{parsed.port or (443 if parsed.scheme.lower() == 'https' else 80)}"
    if actual_origin != origin:
        raise PermissionError("模型请求目标与批准端点不一致")
    actual_path = parsed.path or "/"
    base = base_path.rstrip("/") or "/"
    if actual_path != base_path and not actual_path.startswith(base + "/") and not base == "/":
        raise PermissionError("模型请求路径不在批准端点之下")


class GuardedHTTPClient(httpx.Client):
    """按批准端点校验每个请求，并始终禁止重定向。

    共享客户端在构造后被改成 ``follow_redirects=True`` 也不会跟随：``send`` 显式
    传入 ``follow_redirects=False``，且任何 3xx 响应都被视为未授权跳转。
    """

    def __init__(self, *, allowed_endpoint: str, **kwargs):
        kwargs.pop("follow_redirects", None)
        super().__init__(follow_redirects=False, **kwargs)
        if not _endpoint_allowed(allowed_endpoint):
            raise ValueError("模型端点未获允许，拒绝构造HTTP客户端")
        self._allowed_endpoint = normalize_endpoint(allowed_endpoint)
        self._allowed_origin, self._allowed_path = split_endpoint(self._allowed_endpoint)

    @property
    def allowed_endpoint(self) -> str:
        return self._allowed_endpoint

    def send(self, request, **kwargs):
        kwargs["follow_redirects"] = False
        _require_target(str(request.url), self._allowed_origin, self._allowed_path)
        response = super().send(request, **kwargs)
        if response.is_redirect:
            raise PermissionError("模型服务返回重定向，禁止跟随")
        return response

    def request(self, method, url, **kwargs):
        kwargs["follow_redirects"] = False
        _require_target(str(url), self._allowed_origin, self._allowed_path)
        return super().request(method, url, **kwargs)


def build_guarded_client(*, allowed_endpoint: str, timeout: float,
                         injected: httpx.Client | None = None) -> GuardedHTTPClient:
    """统一构造入口；注入的客户端只复用其 transport，不继承可变配置。"""
    if injected is not None:
        if getattr(injected, "follow_redirects", False):
            raise ValueError("模型HTTP客户端不得自动跟随重定向")
        transport = getattr(injected, "_transport", None)
        if transport is None:
            raise ValueError("注入的客户端缺少可用transport，拒绝构造")
        return GuardedHTTPClient(allowed_endpoint=allowed_endpoint, timeout=timeout,
                                 transport=transport, trust_env=False)
    return GuardedHTTPClient(allowed_endpoint=allowed_endpoint, timeout=timeout,
                             trust_env=False, follow_redirects=False)


def sdk_http_client(model) -> httpx.Client | None:
    """返回 SDK 真正用于发送的 httpx 客户端（openai SDK 的 ``_client``）。"""
    client = getattr(model, "root_client", None) or getattr(model, "client", None)
    active = getattr(client, "_client", None)
    return active if isinstance(active, httpx.Client) else None


def sdk_base_url(model) -> str:
    client = getattr(model, "root_client", None) or getattr(model, "client", None)
    base = getattr(client, "base_url", None)
    return str(base) if base is not None else ""


def require_sdk_egress(model, *, allowed_endpoint: str) -> None:
    """发送前核对 SDK 的真实目标与活动客户端。

    只检查 Provider 保存的 endpoint 字符串不够：SDK 的 base_url 可被改指，活动
    httpx 客户端可被换成普通客户端。两者都必须是受控对象才允许发送。
    非真实 SDK（测试用 Fake 模型）没有 httpx 客户端时不在此阻断。
    """
    active = sdk_http_client(model)
    if active is None:
        return
    if not isinstance(active, GuardedHTTPClient):
        raise PermissionError("模型活动HTTP客户端不是受控客户端，禁止外发")
    if not allowed_endpoint:
        raise PermissionError("缺少批准端点，禁止外发")
    approved = normalize_endpoint(allowed_endpoint)
    if active.allowed_endpoint != approved:
        raise PermissionError("模型活动客户端的批准端点与当前调用不一致，禁止外发")
    base = sdk_base_url(model)
    if not base or normalize_endpoint(base) != approved:
        raise PermissionError("模型SDK实际base_url与批准端点不一致，禁止外发")
