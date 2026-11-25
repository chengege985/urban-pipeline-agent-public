"""Authenticated AMap district proxy for the loopback gas preview."""
from __future__ import annotations

from dataclasses import dataclass, field
import hmac
import json
from pathlib import Path
import re
import stat
from urllib.parse import parse_qsl, urlencode

import httpx


DISTRICT_PATH = "/_AMapService/v3/config/district"
DISTRICT_UPSTREAM = "https://restapi.amap.com/v3/config/district"
_WEB_CREDENTIAL = re.compile(r"[a-fA-F0-9]{32}")
_CHONGQING_ADCODE = re.compile(r"500(?:000|[12]\d{2})")
_CALLBACK = re.compile(r"[A-Za-z_$][\w$.]{0,95}", re.ASCII)
_BODY_LIMIT = 5_000_000


@dataclass(frozen=True)
class PreviewAmap:
    key: str = field(repr=False)
    security_code: str = field(repr=False)

    @classmethod
    def from_runtime(cls, root: Path) -> PreviewAmap | None:
        path = root / "amap-credentials.json"
        try:
            info = path.lstat()
        except FileNotFoundError:
            return None
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_size > 1024:
            raise RuntimeError("AMap preview credentials require a private runtime file")
        try:
            values = json.loads(path.read_text())
            key, code = values["key"], values["security_js_code"]
            if not all(isinstance(value, str) and _WEB_CREDENTIAL.fullmatch(value) for value in (key, code)):
                raise ValueError
        except (OSError, TypeError, KeyError, ValueError):
            raise RuntimeError("Invalid AMap preview credentials") from None
        return cls(key, code)

    def configure_page(self, html: bytes) -> bytes:
        runtime = json.dumps({"key": self.key, "serviceHost": "/_AMapService", "version": "2.0"})
        config = b"<script>window.__WPN_AMAP_CONFIG__=" + runtime.encode() + b";</script>"
        return html.replace(b"</head>", config + b"</head>", 1)

    def district_parameters(self, query: str) -> dict[str, str]:
        if len(query) > 2048:
            raise ValueError("invalid district query")
        items = parse_qsl(query, keep_blank_values=True, max_num_fields=32)
        params = dict(items)
        # JS API 2.0 repeats key and s with identical values.
        if (any(params[name] != value for name, value in items) or "jscode" in params
            or not hmac.compare_digest(params.get("key", "").encode(), self.key.encode())
            or not _CHONGQING_ADCODE.fullmatch(params.get("keywords", ""))
            or params.get("subdistrict") != "0" or params.get("extensions") != "all"
            or ("callback" in params and not _CALLBACK.fullmatch(params["callback"]))):
            raise ValueError("invalid district query")
        return params

    def request_district(self, params: dict[str, str], referer: str) -> DistrictResponse:
        query = urlencode({**params, "jscode": self.security_code})
        with httpx.Client(trust_env=False, follow_redirects=False, timeout=6.5) as client:
            with client.stream("GET", DISTRICT_UPSTREAM + "?" + query,
                               headers={"Referer": referer, "Accept": "*/*"}) as response:
                status = response.status_code
                # Only the provider's JSON/JSONP is forwarded to the script consumer.
                if status != 200:
                    return DistrictResponse(status if 400 <= status < 600 else 502)
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > _BODY_LIMIT:
                        return DistrictResponse(502)
        if self.security_code.lower().encode() in body.lower():
            return DistrictResponse(502)
        callback = params.get("callback")
        payload = parse_district_payload(bytes(body), callback)
        if payload is None:
            return DistrictResponse(502)
        code = str(payload.get("infocode", ""))
        payload_bytes = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode()
        if callback:
            payload_bytes = callback.encode() + b"(" + payload_bytes + b");"
        return DistrictResponse(status, payload_bytes, "application/javascript" if callback else "application/json",
                                code if re.fullmatch(r"\d{5}", code) else None)


def parse_district_payload(body: bytes, callback: str | None) -> dict | None:
    body = body.strip()
    if callback and not body.startswith(b"{"):
        prefix = callback.encode() + b"("
        if not body.startswith(prefix) or not body.removesuffix(b";").endswith(b")"):
            return None
        body = body.removesuffix(b";")[len(prefix):-1]
    try:
        result = json.loads(body)
    except (UnicodeDecodeError, ValueError, RecursionError):
        return None
    return result if isinstance(result, dict) else None


@dataclass(frozen=True)
class DistrictResponse:
    status: int
    body: bytes = b""
    content_type: str = "application/json"
    provider_code: str | None = None
