"""模型服务验收脚本的协议回归；不调用真实PPU服务。"""
import json

import httpx

from deploy.ppu.verify_services import check_embeddings, check_models, check_tool_round_trip


def make_client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler), base_url="http://model")


def test_models_and_tool_round_trip():
    def handler(request):
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "Qwen3.6-35B-A3B"}]})
        body = json.loads(request.content)
        if any(m.get("role") == "tool" for m in body["messages"]):
            return httpx.Response(200, json={"choices": [{"message": {"content": "WP-016 的演示分为 17.5。"}}]})
        return httpx.Response(200, json={"choices": [{"message": {"content": None, "tool_calls": [{
            "id": "call-1", "type": "function",
            "function": {"name": "get_pipe_health", "arguments": json.dumps({"pipe_id": "WP-016"})}}]}}]})
    with make_client(handler) as client:
        assert check_models(client, "http://model", "Qwen3.6") == []
        issues, evidence = check_tool_round_trip(client, "http://model", "Qwen3.6")
        assert issues == [] and "17.5" in evidence["final"]


def test_embedding_contract_and_dimension_failure():
    def handler(request):
        body = json.loads(request.content)
        dims = body.get("dimensions", 4)
        if dims != 4:
            dims = 4
        v1 = [1.0, 0.0, 0.0, 0.0][:dims]
        v2 = [0.0, 1.0, 0.0, 0.0][:dims]
        return httpx.Response(200, json={"data": [
            {"embedding": v1}, {"embedding": v2}, {"embedding": v1}]})
    with make_client(handler) as client:
        issues, evidence = check_embeddings(client, "http://model", "Qwen3-VL", 4)
        assert issues == [] and evidence["count"] == 3
        issues, _ = check_embeddings(client, "http://model", "Qwen3-VL", 8)
        assert issues  # 服务返回4维，配置8维必须失败


def test_missing_tool_call_is_reported():
    def handler(request):
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "Qwen3.6"}]})
        return httpx.Response(200, json={"choices": [{"message": {"content": "直接回答"}}]})
    with make_client(handler) as client:
        issues, _ = check_tool_round_trip(client, "http://model", "Qwen3.6")
        assert "没有返回标准tool_calls" in issues[0]
