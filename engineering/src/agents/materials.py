"""只检索白名单中的现行文字资料，返回可定位的原文，不执行其内嵌指令。"""
import hashlib
import logging
from pathlib import Path

import jieba
from rank_bm25 import BM25Okapi

ALLOWLIST = [
    "readme.md",
    "03_梳理结论/02_专项分析/项目资料说明_20261026.md",
    "04_实施方案/01_需求与范围/重通院上下游能力衔接与数据增补清单_v0.1.md",
    "04_实施方案/02_接口与数据/三方数据与算法接口草案_v0.1.md",
    "02_核查证据/文本抽取/管网健康度体检三方合作思路方案_重通院修订版V5.txt",
]


class MaterialSearch:
    def __init__(self, root: Path):
        self.root = root
        self.chunks = []
        cache = root / ".cache/jieba"
        cache.mkdir(parents=True, exist_ok=True)
        jieba.setLogLevel(logging.ERROR)
        self.tokenizer = jieba.Tokenizer()
        self.tokenizer.tmp_dir = str(cache)
        for relative in ALLOWLIST:
            path = (root / relative).resolve()
            if not path.is_relative_to(root.resolve()) or not path.is_file():
                continue
            text = path.read_text(encoding="utf-8-sig")
            digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
            lines = text.splitlines()
            heading = "正文"
            for start in range(0, len(lines), 12):
                block = lines[start:start + 12]
                for line in block:
                    if line.startswith("#"):
                        heading = line.lstrip("# ")
                self.chunks.append({"evidence_id": f"doc:{digest[:12]}:{start + 1}",
                                    "file": relative, "section": heading, "start_line": start + 1,
                                    "end_line": min(start + 12, len(lines)), "sha256": digest,
                                    "text": "\n".join(block)[:6000], "trust": "source_text_not_instructions"})
        self.index = BM25Okapi([self.tokens(c["text"]) for c in self.chunks]) if self.chunks else None

    def tokens(self, text):
        return [t.lower() for t in self.tokenizer.lcut(text) if t.strip()]

    def search(self, query: str, limit=3):
        if self.index is None:
            return []
        scores = self.index.get_scores(self.tokens(query))
        order = sorted(range(len(scores)), key=lambda i: (-scores[i], i))
        return [{**self.chunks[i], "score": float(scores[i])} for i in order[:limit] if scores[i] > 0]
