"""Shared HTTP error/security declarations for the platform OpenAPI document."""
from __future__ import annotations

from fastapi.security import APIKeyHeader
from pydantic import BaseModel

WATER_TOKEN_SCHEME = APIKeyHeader(
    name="X-Water-Token",
    auto_error=False,
    scheme_name="WaterToken",
    description="开发或部署令牌。共享令牌只认证调用方，不自动授予真实数据集；"
                "真实数据还需要服务端 principal 到数据集的显式映射。",
)

CLIENT_HEADER = {
    "name": "X-Water-Client",
    "in": "header",
    "required": True,
    "schema": {"type": "string", "enum": ["local-prototype", "unified-react"], "example": "unified-react"},
    "description": "协议标识，所有写操作必填；不是身份凭据，不能代替X-Water-Token或数据集/操作权限。",
}

OPTIONAL_SECURITY = [{}, {"WaterToken": []}]


class ErrorResponse(BaseModel):
    detail: str


class ValidationIssue(BaseModel):
    field: list[str | int]
    reason: str
    type: str


class ValidationErrorResponse(BaseModel):
    detail: list[ValidationIssue]


COMMON_ERRORS = {
    401: {"model": ErrorResponse, "description": "未认证或令牌无效"},
    403: {"model": ErrorResponse, "description": "无权读取或写入该数据集"},
    404: {"model": ErrorResponse, "description": "对象不存在"},
    409: {"model": ErrorResponse, "description": "状态或版本冲突"},
    503: {"model": ErrorResponse, "description": "服务端候选fixture不可用"},
    422: {"model": ValidationErrorResponse, "description": "请求字段或取值不符合契约"},
}
