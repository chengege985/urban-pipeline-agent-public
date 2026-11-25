"""统一产品合同层（UNIFY-PRODUCT-01）。

只读组合：冻结 DTO、报告投影、跨入口一致性指纹。
不包含评分、拓扑、效用或恢复时间公式，也不新增 HTTP 路由。
"""
from src.unified.contracts import VERSION

__all__ = ["VERSION"]
