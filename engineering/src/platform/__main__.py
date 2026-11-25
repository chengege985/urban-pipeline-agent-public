"""单进程本地服务入口；生产平台接入与多人授权尚不在本原型范围内。"""
import argparse
import os

import uvicorn


def main():
    parser = argparse.ArgumentParser(description="WaterPipNet供水技术原型API")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    host = os.getenv("WATER_BIND_HOST", "127.0.0.1").strip() or "127.0.0.1"
    uvicorn.run("src.platform.app:create_app", factory=True, host=host, port=args.port, workers=1)


if __name__ == "__main__":
    main()
