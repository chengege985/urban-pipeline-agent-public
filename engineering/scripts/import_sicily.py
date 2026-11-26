"""Import the pinned published benchmark into a local, immutable snapshot."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.gas_application import sicily


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkout", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    checkout = args.checkout.resolve()
    destination = sicily.long_path(args.destination)
    source_rel = "sample_data/Sicily_transmission_network"
    git = ["git", "-C", str(checkout)]
    commit = subprocess.check_output([*git, "rev-parse", "HEAD"], text=True).strip()
    dirty = subprocess.check_output([*git, "status", "--porcelain", "--", source_rel], text=True)
    if commit != sicily.COMMIT or dirty:
        parser.error("检出提交与要求不一致，或西西里原始目录存在修改")
    if destination.exists():
        parser.error("目标目录已存在，请使用新的快照目录")
    source = sicily.long_path(checkout / source_rel)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix=".sicily-import-", dir=destination.parent))
    try:
        default_dir = source / "all_19_days/20251115/input_h12"
        reference_nodes, reference_pipes = sicily.network({
            name: (default_dir / name).read_bytes() for name in sicily.CASE_NAMES[:2]
        })
        cases = {}
        for hour in sorted((source / "all_19_days").glob("*/input_h*")):
            day, label = hour.parent.name, hour.name
            if not re.fullmatch(r"\d{8}", day) or not re.fullmatch(r"input_h(?:[01]\d|2[0-3])", label):
                continue
            case = day + "-" + label[-2:]
            payload = {}
            for name in sicily.CASE_NAMES:
                parent = hour / "results" if name.startswith("results_") else hour
                if (parent / name).is_file():
                    payload[name] = (parent / name).read_bytes()
            if not all(name in payload for name in sicily.CASE_NAMES[:2]):
                continue
            nodes, pipes = sicily.network(payload)
            if nodes.keys() != reference_nodes.keys() or pipes != reference_pipes:
                parser.error(f"{case} 与默认工况的网络连接或管段属性不一致")
            target = temp / case
            target.mkdir()
            for name, data in payload.items():
                (target / name).write_bytes(data)
            cases[case] = {name: hashlib.sha256(data).hexdigest() for name, data in payload.items()}
        if sicily.DEFAULT not in cases:
            parser.error("缺少默认参考工况")
        version = "sicily-" + commit[:12] + "-" + hashlib.sha256(
            json.dumps(cases, sort_keys=True).encode()).hexdigest()[:12]
        meta = {"dataset_id": sicily.ID, "version": version, "commit": commit,
                "source": sicily.SOURCE, "cases": cases}
        (temp / "manifest.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        temp.rename(destination)
        print(f"{version}: {len(cases)} 个工况，"
              f"{sum(len(case) == 4 for case in cases.values())} 组发布结果")
    finally:
        if temp.exists():
            shutil.rmtree(temp)


if __name__ == "__main__":
    main()
