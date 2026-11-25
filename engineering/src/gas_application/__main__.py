from __future__ import annotations
import os
import uvicorn
from .standalone import create_gas_app

app=create_gas_app(
    include_bff=(
        os.getenv("GAS_BFF_MODE", "").strip().lower() == "development"
        and os.getenv("WATER_RUNTIME_MODE", "").strip().lower() in {"local", "development"}
    )
)

if __name__=="__main__":
    uvicorn.run(
        app,
        host=os.getenv("WATER_BIND_HOST","127.0.0.1"),
        port=int(os.getenv("WATER_GAS_PORT","18096")),
        loop="asyncio",
        http="h11",
        proxy_headers=False,
    )
