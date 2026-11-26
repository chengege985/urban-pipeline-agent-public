
"""Download the two pinned Pipeline Engineer example files into a local snapshot."""
from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.gas_application.ngn_sample import SOURCE_HASHES, SOURCE_REVISION


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    root = parser.parse_args().destination
    base = "https://raw.githubusercontent.com/pipelineengineer/examples/" + SOURCE_REVISION
    contents = {}
    for name, expected in SOURCE_HASHES.items():
        with urlopen(f"{base}/gas-network-example/{name}", timeout=30) as response:
            contents[name] = response.read(10_000_001)
        if hashlib.sha256(contents[name]).hexdigest() != expected:
            raise ValueError(f"{name}: published bytes do not match the pinned hash")
    root.mkdir(parents=True, exist_ok=True)
    for name, content in contents.items():
        (root / name).write_bytes(content)
    (root / "revision.txt").write_text(SOURCE_REVISION + "\n", encoding="ascii")
    print(f"Verified Pipeline Engineer snapshot: {root}")


if __name__ == "__main__":
    main()
