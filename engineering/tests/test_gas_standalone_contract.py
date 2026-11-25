"""Both entrypoints retain one DTO contract and truthful token requirements."""
import os
from fastapi.testclient import TestClient
from src.gas_application.standalone import create_gas_app
from src.platform.openapi_export import build_openapi

def test_standalone_contract_matches_shared_dtos_and_default_auth(monkeypatch):
    for key in tuple(os.environ):
        if key.startswith(("WATER_","GAS_BFF_","ALLOW_LOCAL_","AGENT_")):
            monkeypatch.delenv(key)
    app=create_gas_app(include_bff=False)
    gas=app.openapi();platform=build_openapi()
    assert gas["components"]["securitySchemes"]["WaterToken"]==platform["components"]["securitySchemes"]["WaterToken"]
    for name,schema in gas["components"]["schemas"].items():
        assert platform["components"]["schemas"][name]==schema,name
    for path,methods in gas["paths"].items():
        if not path.startswith("/api/v1/gas/"):continue
        for method,operation in methods.items():
            assert operation["security"]==[{"WaterToken":[]}],path
            shared=dict(platform["paths"][path][method])
            # The full-platform middleware has an additional protocol marker for POST.
            # It is not authentication; standalone read-only Query requires Token+ACL.
            if method=="post":
                parameters = shared.pop("parameters")
                marker = [p for p in parameters if p["name"] == "X-Water-Client"]
                assert len(marker) == 1 and marker[0]["required"] is True
                inherited = [p for p in parameters if p["name"] != "X-Water-Client"]
                if inherited:
                    shared["parameters"] = inherited
            assert shared==operation,path
    with TestClient(app) as client:
        for path in ("/api/v1/gas/cases","/api/v1/gas/cases/syn-gas-case-K-001"):
            assert client.get(path).status_code==401
            assert client.get(path,headers={"X-Water-Token":"invalid"}).status_code==401
