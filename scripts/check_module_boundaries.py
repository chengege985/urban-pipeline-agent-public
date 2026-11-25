"""Offline ownership/dependency gate. No business code imports or network calls.

Baseline refresh is deliberately separate from check. It is a reviewed contract change:
never regenerate the allowlist merely to make a violation pass.
"""
from __future__ import annotations
import argparse
import ast
from collections import defaultdict
import hashlib
from importlib.util import resolve_name
import json
from pathlib import Path
import subprocess
import sys

BASE_COMMIT = "602b56815f8c3d450197134668ad645dbc11bac7"
BASELINE = "docs/architecture/dependency_baseline.json"
FREEZE = "docs/architecture/FROZEN_FILES.json"
TS_SUFFIXES = {".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs"}
TEXT_SUFFIXES = {".py", ".json"} | TS_SUFFIXES
B_PYTHON = ("engineering/src/cuv_application/", "engineering/src/pipe_health/", "engineering/src/simulation/", "engineering/src/decision/", "engineering/src/adapters/", "engineering/src/algorithm_validation/", "engineering/src/gas_application/", "engineering/experiments/gas_m0/")
DISPLAY_UTILS = {"web/src/engine/context.ts", "web/src/engine/format.ts", "web/src/engine/timelineContext.ts"}
SHARED_LEGACY_BRIDGES = {
    "web/src/shared/contracts/domain.ts": "web/src/domain/types.ts",
    "web/src/shared/contracts/constants.ts": "web/src/domain/constants.ts",
    "web/src/shared/contracts/provider.ts": "web/src/providers/types.ts",
    "web/src/shared/contracts/platform.ts": "web/src/generated/platformOpenapi.ts",
    "web/src/shared/api/index.ts": "web/src/providers/types.ts",
    "web/src/shared/api/platformTransport.ts": "web/src/providers/platform/transport.ts",
    "web/src/shared/utils/context.ts": "web/src/engine/context.ts",
    "web/src/shared/utils/format.ts": "web/src/engine/format.ts",
    "web/src/shared/utils/timelineContext.ts": "web/src/engine/timelineContext.ts",
}


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def git(root: Path, *args: str) -> bytes:
    return subprocess.check_output(["git", *args], cwd=root)


def tracked_snapshot(root: Path, revision: str | None = None) -> dict[str, bytes]:
    if revision:
        names = git(root, "ls-tree", "-r", "--name-only", "-z", revision).decode().rstrip("\0").split("\0")
        return {name: git(root, "show", f"{revision}:{name}") for name in names if name}
    names = git(root, "ls-files", "--cached", "--others", "--exclude-standard", "-z").decode().rstrip("\0").split("\0")
    return {name: (root / name).read_bytes() for name in sorted(set(names)) if name and (root / name).is_file()}


def text_files(files: dict[str, bytes]) -> dict[str, str]:
    return {name: raw.decode("utf-8-sig") for name, raw in files.items() if Path(name).suffix in TEXT_SUFFIXES}


def python_edges(files: dict[str, str]) -> dict:
    edges, errors, dynamic, unresolved = [], [], [], []
    initialization_edges = []
    # A file can be imported through several real repository roots (including
    # namespace packages). Do not discard the engineering alias of gas sources.
    roots = ("engineering/experiments/gas_m0/src/", "engineering/", "")
    modules: dict[str, set[str]] = defaultdict(set)
    namespaces: set[str] = set()
    namespace_paths: dict[str, set[str]] = defaultdict(set)
    packages = {}
    for name in files:
        if not name.endswith(".py"):
            continue
        for prefix in roots:
            if not name.startswith(prefix):
                continue
            module = name[len(prefix):].removesuffix(".py").replace("/", ".")
            if module.endswith(".__init__"):
                module = module[:-9]
            if module and module != "__init__":
                modules[module].add(name)
                parts = module.split(".")
                for depth in range(1, len(parts)):
                    parent = ".".join(parts[:depth])
                    namespaces.add(parent)
                    namespace_paths[parent].add(prefix + "/".join(parts[:depth]) + "/")
        prefix = next(root for root in roots if name.startswith(root))
        module = name[len(prefix):].removesuffix(".py").replace("/", ".")
        packages[name] = module.rsplit(".", 1)[0] if "." in module else ""
    local_roots = {module.split(".", 1)[0] for module in modules}
    for name, source in files.items():
        if not name.endswith(".py"):
            continue
        package = packages[name]
        # A module can be entered through any repository import root. Its
        # concrete ancestors execute before its body, even when that body has
        # no imports. Keep these entry dependencies separate from ordinary
        # import cycles: a package legitimately re-exporting a child is not a
        # cycle merely because entering that child initializes the package.
        parts = name.split("/")
        for depth in range(1, len(parts)):
            parent = "/".join(parts[:depth]) + "/__init__.py"
            if parent in files and parent != name:
                initialization_edges.append({"source": name, "target": parent,
                                             "line": 1, "kind": "source_init"})
        try:
            tree = ast.parse(source, filename=name)
        except SyntaxError as exc:
            errors.append({"source": name, "reason": str(exc)})
            continue
        loader_names = set()
        builtin_loaders = {"__import__"}
        builtin_modules = set()
        for item in ast.walk(tree):
            if isinstance(item, ast.ImportFrom) and item.module == "importlib":
                loader_names.update(alias.asname or alias.name for alias in item.names if alias.name == "import_module")
            if isinstance(item, ast.ImportFrom) and item.module == "builtins":
                builtin_loaders.update(alias.asname or alias.name for alias in item.names if alias.name == "__import__")
            if isinstance(item, ast.Import):
                builtin_modules.update(alias.asname or alias.name for alias in item.names if alias.name == "builtins")

        def missing(target: str, line: int, reason: str) -> None:
            item = {"source": name, "specifier": target, "line": line, "reason": reason}
            if item not in unresolved:
                unresolved.append(item)

        def parent_initializers(target: str, resolved: str, line: int, kind: str) -> None:
            # Importing a child executes concrete parent packages in the same
            # resolved root, including parents of otherwise empty namespaces.
            # A source already inside that package entered its own parents;
            # adding those again would invent initialization cycles.
            parts = target.split(".")
            for depth in range(1, len(parts)):
                parent = ".".join(parts[:depth])
                candidates = {path for path in modules.get(parent, set())
                              if path.endswith("/__init__.py")
                              and resolved.startswith(path.removesuffix("__init__.py"))}
                if len(candidates) > 1:
                    missing(parent, line, "Ambiguous parent package initializer")
                    continue
                for path in candidates:
                    if name.startswith(path.removesuffix("__init__.py")):
                        continue
                    edge = {"source": name, "target": path, "specifier": parent,
                            "line": line, "kind": "package_init",
                            "via_target": resolved, "import_kind": kind}
                    if edge not in edges:
                        edges.append(edge)

        def add(target: str, line: int, kind: str, *, required=False, relative=False) -> None:
            candidates = modules.get(target, set())
            if len(candidates) == 1:
                resolved = next(iter(candidates))
                edge = {"source": name, "target": resolved, "specifier": target, "line": line, "kind": kind}
                if edge not in edges:
                    edges.append(edge)
                parent_initializers(target, resolved, line, kind)
            elif len(candidates) > 1:
                missing(target, line, "Ambiguous repository import roots")
            elif target in namespaces:
                for directory in sorted(namespace_paths[target]):
                    parent_initializers(target, directory, line, kind)
            elif required and target not in namespaces and (relative or target.split(".", 1)[0] in local_roots):
                missing(target, line, "Unresolved repository-local Python import")

        def absolute(target: str, context: str, line: int) -> str | None:
            try:
                return resolve_name(target, context) if target.startswith(".") else target
            except (ImportError, ValueError) as exc:
                missing(target, line, str(exc))
                return None

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    add(alias.name, node.lineno, "import", required=True)
            elif isinstance(node, ast.ImportFrom):
                target = absolute("." * node.level + (node.module or ""), package, node.lineno)
                if target is None:
                    continue
                # In a package initializer, ``from . import child`` uses the
                # current package as a container, not an explicit self import.
                # Keep every child edge below and preserve all other self edges.
                own_container = (name.endswith("/__init__.py") and node.level == 1
                                 and node.module is None and modules.get(target) == {name}
                                 and all(alias.name != "*" for alias in node.names))
                if not own_container:
                    add(target, node.lineno, "from", required=True, relative=bool(node.level))
                for alias in node.names:
                    if alias.name != "*":
                        # Missing members of concrete modules may be attributes;
                        # namespace packages can only supply local submodules.
                        add(target + "." + alias.name, node.lineno, "from_submodule",
                            required=target in namespaces and target not in modules)
            elif isinstance(node, ast.Call):
                func = node.func
                builtin_import = (isinstance(func, ast.Name) and func.id in builtin_loaders) or (
                    isinstance(func, ast.Attribute) and func.attr == "__import__"
                    and isinstance(func.value, ast.Name) and func.value.id in builtin_modules)
                importing = builtin_import or (isinstance(func, ast.Name) and func.id in loader_names)
                importing |= isinstance(func, ast.Attribute) and func.attr == "import_module"
                if not importing:
                    continue
                keywords = {item.arg: item.value for item in node.keywords if item.arg}
                if builtin_import:
                    level = node.args[4] if len(node.args) > 4 else keywords.get("level")
                    if any(item.arg is None for item in node.keywords) or (level is not None and not (isinstance(level, ast.Constant) and isinstance(level.value, int) and level.value == 0)):
                        dynamic.append({"source": name, "line": node.lineno, "kind": "dynamic_import", "reason": "Nonzero or unknown __import__ level requires runtime globals; refuse static approval"})
                        continue
                argument = node.args[0] if node.args else keywords.get("name")
                if not isinstance(argument, ast.Constant) or not isinstance(argument.value, str):
                    dynamic.append({"source": name, "line": node.lineno, "kind": "dynamic_import"})
                    continue
                target = argument.value
                if target.startswith("."):
                    context = node.args[1] if len(node.args) > 1 else keywords.get("package")
                    if isinstance(context, ast.Constant) and isinstance(context.value, str):
                        context = context.value
                    elif isinstance(context, ast.Name) and context.id == "__package__":
                        context = package
                    else:
                        dynamic.append({"source": name, "line": node.lineno, "kind": "dynamic_import", "reason": "Relative import_module requires a statically known package"})
                        continue
                    target = absolute(target, context, node.lineno)
                if target is not None:
                    add(target, node.lineno, "dynamic_import", required=True)
                    # __import__ loads named fromlist children as well as name.
                    # Only repository-local names need static child resolution;
                    # third-party packages stay outside this ownership graph.
                    if builtin_import and target.split(".", 1)[0] in local_roots:
                        fromlist = node.args[3] if len(node.args) > 3 else keywords.get("fromlist")
                        if fromlist is None or (isinstance(fromlist, ast.Constant) and not fromlist.value):
                            continue
                        if not isinstance(fromlist, (ast.List, ast.Tuple)) or not all(isinstance(item, ast.Constant) and isinstance(item.value, str) for item in fromlist.elts):
                            dynamic.append({"source": name, "line": node.lineno, "kind": "dynamic_import", "reason": "Repository __import__ requires a static string fromlist"})
                            continue
                        for member in fromlist.elts:
                            if not member.value or any(not part.isidentifier() for part in member.value.split(".")):
                                dynamic.append({"source": name, "line": node.lineno, "kind": "dynamic_import", "reason": "Unknown or wildcard repository fromlist dependency"})
                                continue
                            # Importing a dotted member loads its existing
                            # prefixes even if the missing terminal is ignored
                            # by Python's fromlist handling. A prefix can itself
                            # be a module, not only a package initializer.
                            parts = member.value.split(".")
                            for depth in range(1, len(parts) + 1):
                                add(target + "." + ".".join(parts[:depth]), node.lineno,
                                    "dynamic_fromlist",
                                    required=depth == len(parts) and target in namespaces and target not in modules)
    return {"edges": edges, "initialization_edges": initialization_edges, "parse_errors": errors, "dynamic_unknown": dynamic, "unresolved": unresolved}


def collect_graph(root: Path, raw: dict[str, bytes], node: str = "node") -> dict:
    files = text_files(raw)
    py = python_edges(files)
    payload = {"root": str(root), "files": {n: t for n, t in files.items() if Path(n).suffix in TS_SUFFIXES or n == "web/tsconfig.json"}}
    proc = subprocess.run([node, str(root / "scripts/parse_typescript_dependencies.mjs")], input=json.dumps(payload), text=True, capture_output=True)
    if proc.returncode:
        raise RuntimeError("TypeScript AST parsing failed: " + proc.stderr[-2000:])
    ts = json.loads(proc.stdout)
    return {"edges": sorted(py["edges"] + ts["edges"], key=lambda e: (e["source"], e["target"], e["line"], e["kind"])), "initialization_edges": sorted(py["initialization_edges"], key=lambda e: (e["source"], e["target"])), "facades": ts["facades"], "parse_errors": py["parse_errors"] + ts["parse_errors"], "dynamic_unknown": py["dynamic_unknown"] + ts["dynamic_unknown"], "unresolved": py["unresolved"] + ts["unresolved"]}


def dependency_domain(path: str) -> str:
    if path.startswith(("engineering/src/ai_application/", "engineering/src/agents/", "web/src/features/assistant/")) or path in {"web/src/engine/assistant.ts", "server/llm_assistant.py"}:
        return "A_ONLY"
    if path.startswith(B_PYTHON + ("engineering/experiments/gas_m0/", "web/src/features/cuv/", "web/src/features/gas/", "web/src/features/network/", "web/src/data/")):
        return "B_ONLY"
    if path.startswith("web/src/engine/") and path not in DISPLAY_UTILS and not path.endswith("/report.ts"):
        return "B_ONLY"
    if path in {"server/demo_engine.py", "server/timeline.py"}:
        return "B_ONLY"
    if path.startswith("engineering/tests/test_agent_"):
        return "A_ONLY"
    return "SHARED_CONTROLLED"


def codeowners_rules(text: str) -> list[dict]:
    # Parse this repository's explicit root/file/directory policy; never approximate globs.
    rules = []
    verified = {"catiseyeqaq", "chengege985"}
    for number, line in enumerate(text.splitlines(), 1):
        fields = line.split("#", 1)[0].split()
        if not fields:
            continue
        pattern, *accounts = fields
        if (pattern != "*" and (not pattern.startswith("/") or any(char in pattern for char in "*?[]!\\"))) or not accounts:
            raise ValueError(f"Unsupported CODEOWNERS rule at line {number}")
        if any(not account.startswith("@") or account[1:] not in verified for account in accounts):
            raise ValueError(f"Unverified CODEOWNERS account at line {number}")
        rules.append({"pattern": pattern, "accounts": sorted(set(account[1:] for account in accounts))})
    if not any(rule["pattern"] == "*" for rule in rules):
        raise ValueError("CODEOWNERS requires an explicit repository default")
    return rules


def codeowners_for_path(path: str, rules: list[dict]) -> dict:
    matched = None
    for rule in rules:
        pattern = rule["pattern"]
        target = pattern.lstrip("/")
        if pattern == "*" or (target.endswith("/") and path.startswith(target)) or path == target:
            matched = rule
    if matched is None:
        raise ValueError("No CODEOWNERS rule for " + path)
    accounts = matched["accounts"]
    category = "A_ONLY" if accounts == ["catiseyeqaq"] else "B_ONLY" if accounts == ["chengege985"] else "SHARED_CONTROLLED"
    return {"owner": category, "accounts": accounts, "rule": matched["pattern"]}


def feature(path: str) -> str | None:
    if path.startswith("web/src/features/"):
        return path.split("/")[3]
    return None


def is_test(path: str) -> bool:
    return "/tests/" in path or Path(path).name.startswith("test_") or Path(path).name.startswith("test-")


def forbidden_edge(source: str, target: str) -> str | None:
    if is_test(source):
        return None
    if is_test(target):
        return "Runtime modules must not use test modules to bypass domain boundaries"
    if source.startswith("engineering/src/ai_application/"):
        if not target.startswith(("engineering/src/ai_application/", "engineering/src/shared_contracts/")):
            return "AI application may import only own modules and shared contracts"
    if source.startswith("engineering/src/shared_contracts/"):
        if not target.startswith("engineering/src/shared_contracts/"):
            return "Shared contracts must not import application/platform/algorithm implementations"
    if source.startswith(B_PYTHON):
        if target.startswith(("engineering/src/ai_application/", "engineering/src/agents/")):
            return "CUV/business modules must not import AI orchestration or frozen Agent"
    if source.startswith("engineering/src/agents/") and target.startswith(B_PYTHON + ("engineering/src/platform/",)):
        return "Frozen legacy Agent cross-domain edge requires exact reviewed exception"
    source_feature, target_feature = feature(source), feature(target)
    if source_feature:
        if target.startswith("web/src/") and not (target.startswith("web/src/shared/") or target_feature == source_feature):
            return "Feature may import only its own feature and shared public surface"
    if source.startswith("web/src/shared/"):
        if target_feature or (dependency_domain(target) != "SHARED_CONTROLLED" and target.startswith("web/src/")):
            return "Shared frontend surface must not import private domain implementations"
    return None


def strongly_connected(edges: list[dict]) -> list[list[str]]:
    graph: dict[str, set[str]] = defaultdict(set)
    for edge in edges:
        graph[edge["source"]].add(edge["target"])
        graph.setdefault(edge["target"], set())
    index, stack, active, indexes, lows, found = 0, [], set(), {}, {}, []
    def visit(node: str) -> None:
        nonlocal index
        indexes[node] = lows[node] = index; index += 1
        stack.append(node); active.add(node)
        for target in graph[node]:
            if target not in indexes:
                visit(target); lows[node] = min(lows[node], lows[target])
            elif target in active:
                lows[node] = min(lows[node], indexes[target])
        if lows[node] == indexes[node]:
            component = []
            while True:
                target = stack.pop(); active.remove(target); component.append(target)
                if target == node: break
            if len(component) > 1 or node in graph[node]: found.append(sorted(component))
    for name in sorted(graph):
        if name not in indexes: visit(name)
    return sorted(found)


def effective_targets(target: str, graph: dict, seen: frozenset[str] = frozenset()) -> set[str]:
    if target in seen: return {target}
    if target not in graph.get("facades", []): return {target}
    exports = [edge["target"] for edge in graph["edges"] if edge["source"] == target and edge["kind"] == "export"]
    return set().union(*(effective_targets(name, graph, seen | {target}) for name in exports)) if exports else {target}


def check(raw: dict[str, bytes], graph: dict, baseline: dict, frozen: dict) -> list[dict]:
    violations = []
    hashes = {name: digest(body) for name, body in raw.items()}
    for group, entries in frozen.get("groups", {}).items():
        for name, sha in entries.items():
            if hashes.get(name) != sha: violations.append({"code": "frozen_sha", "source": name, "group": group})
    for item in graph.get("parse_errors", []): violations.append({"code": "parse_error", **item})
    # Checked legacy exceptions are exact edges and both endpoint hashes, never globs.
    allowed = {(e["source"], e["target"]): e for e in baseline.get("legacy_exceptions", [])}
    for edge in graph["edges"]:
        why = forbidden_edge(edge["source"], edge["target"])
        if why:
            prior = allowed.get((edge["source"], edge["target"]))
            exact_legacy = bool(prior and prior["source_sha256"] == hashes.get(edge["source"]) and prior["target_sha256"] == hashes.get(edge["target"]))
            # A frozen, exactly approved import already executed its parents.
            # Derive only that import's implicit edge, pinning the initializer
            # to its original baseline bytes as well; never add an exception.
            if not exact_legacy and edge.get("kind") == "package_init":
                prior = allowed.get((edge["source"], edge.get("via_target")))
                parent_sha = baseline.get("files", {}).get(edge["target"])
                exact_legacy = bool(prior and parent_sha and parent_sha == hashes.get(edge["target"])
                                    and prior["source_sha256"] == hashes.get(edge["source"])
                                    and prior["target_sha256"] == hashes.get(edge.get("via_target")))
            if not exact_legacy:
                violations.append({"code": "forbidden_import", **edge, "reason": why})
        if edge["source"].startswith("web/src/shared/") and edge["target"].startswith("web/src/") and not edge["target"].startswith("web/src/shared/"):
            if SHARED_LEGACY_BRIDGES.get(edge["source"]) != edge["target"] or edge["source"] not in graph.get("facades", []):
                violations.append({"code": "unreviewed_shared_bridge", **edge, "reason": "Only explicit export-only compatibility edges may reference legacy source"})
            elif edge["target"] != "web/src/generated/platformOpenapi.ts" and hashes.get(edge["target"]) != baseline.get("files", {}).get(edge["target"]):
                violations.append({"code": "shared_bridge_target_changed", **edge, "reason": "Legacy contract/display/input-identity bridge target is pinned to reviewed SHA"})
        # A feature using a shared/old compatibility facade still cannot enter another feature.
        if feature(edge["source"]):
            for target in effective_targets(edge["target"], graph):
                if feature(target) and feature(target) != feature(edge["source"]):
                    violations.append({"code": "facade_bypass", **edge, "effective_target": target})
    # Business code may use local helpers, but those helpers cannot hide an
    # eventual dependency on AI/Agent code. Edges already represent local files;
    # standard-library and third-party imports are not in this closure.
    adjacency: dict[str, set[str]] = defaultdict(set)
    initialization_edges = graph.get("initialization_edges", [])
    runtime_edges = graph["edges"] + initialization_edges
    for edge in runtime_edges:
        adjacency[edge["source"]].add(edge["target"])
    for source in sorted(adjacency):
        if not source.startswith(B_PYTHON) or is_test(source):
            continue
        pending = [(target, [source, target]) for target in sorted(adjacency[source])]
        seen = set()
        while pending:
            target, chain = pending.pop()
            if target in seen:
                continue
            seen.add(target)
            if len(chain) > 2 and target.startswith(("engineering/src/ai_application/", "engineering/src/agents/")):
                violations.append({"code": "indirect_business_ai_import", "source": source, "target": target, "via": chain})
            for item in graph.get("unresolved", []) + graph.get("dynamic_unknown", []):
                if item["source"] == target:
                    violations.append({"code": "unresolved_business_dependency", "source": source, "target": target, "via": chain, "dependency": item})
            pending.extend((next_target, chain + [next_target]) for next_target in sorted(adjacency[target]))
    # AI/shared modules may enter their own ancestors without treating that
    # implicit entry as a forbidden explicit import. The ancestors' actual
    # imports still obey the originating domain's allowlist. Do not apply this
    # new-domain policy to the frozen Agent's exact historical imports.
    imports_by_source: dict[str, list[dict]] = defaultdict(list)
    entries_by_source: dict[str, list[dict]] = defaultdict(list)
    for edge in runtime_edges:
        imports_by_source[edge["source"]].append(edge)
    for edge in initialization_edges:
        entries_by_source[edge["source"]].append(edge)
    for source, entries in entries_by_source.items():
        if not source.startswith(("engineering/src/ai_application/", "engineering/src/shared_contracts/")) or is_test(source):
            continue
        pending = [(edge, [source, edge["target"]]) for edge in entries]
        seen = set()
        while pending:
            edge, chain = pending.pop()
            target = edge["target"]
            # Validate each incoming ordinary edge even if an implicit entry
            # has already visited the target through another path.
            why = forbidden_edge(source, target) if edge["kind"] != "source_init" else None
            if why:
                violations.append({"code": "forbidden_initialization_dependency", "source": source,
                                   "target": target, "via": chain, "reason": why})
            if target in seen:
                continue
            seen.add(target)
            for item in graph.get("unresolved", []) + graph.get("dynamic_unknown", []):
                if item["source"] == target:
                    violations.append({"code": "unresolved_initialization_dependency", "source": source,
                                       "target": target, "via": chain, "dependency": item})
            pending.extend((child, chain + [child["target"]]) for child in imports_by_source[target])
    for item in graph.get("dynamic_unknown", []):
        source = item["source"]
        if source.startswith(("engineering/src/ai_application/", "engineering/src/shared_contracts/", "web/src/") + B_PYTHON) and not is_test(source):
            violations.append({"code": "unresolved_dynamic_import", **item})
    for item in graph.get("unresolved", []):
        if item["source"].startswith(("engineering/src/ai_application/", "engineering/src/shared_contracts/", "web/src/") + B_PYTHON) and not is_test(item["source"]):
            violations.append({"code": "unresolved_local_import", **item})
    old_cycles = {tuple(entry["members"]): entry for entry in baseline.get("cycles", [])}
    for members in strongly_connected(graph["edges"]):
        prior = old_cycles.get(tuple(members))
        if not prior or any(hashes.get(name) != prior["sha256"].get(name) for name in members):
            violations.append({"code": "new_or_changed_cycle", "members": members})
    return violations


def baseline_document(files: dict[str, bytes], graph: dict, commit: str) -> dict:
    hashes = {name: digest(raw) for name, raw in files.items()}
    return {"schema_version": "module-boundaries/1", "base_commit": commit, "source": "git objects, before migration", "file_count": len(files), "files": hashes, "edges": graph["edges"], "legacy_exceptions": [{**edge, "source_sha256": hashes[edge["source"]], "target_sha256": hashes[edge["target"]], "reason": forbidden_edge(edge["source"], edge["target"])} for edge in graph["edges"] if forbidden_edge(edge["source"], edge["target"])], "cycles": [{"members": members, "sha256": {name: hashes[name] for name in members}} for members in strongly_connected(graph["edges"])], "limits": ["Static literal imports and TypeScript compiler resolution; arbitrary runtime loading is not a proof of runtime isolation.", "Unknown dynamic imports in new domain/shared source fail closed.", "Changing baseline or frozen manifest is a shared integration PR, not an automatic repair."]}


def migration_target(name: str) -> str:
    if name == "web/src/pages/AssistantPage.tsx": return "web/src/features/assistant/pages/AssistantPage.tsx"
    for domain, names in {"assistant": ["AssistantChat", "ToolCallCard"], "cuv": ["DecisionPanel", "FactorBars", "HealthGauge", "PlanCards", "ScenarioCards"], "network": ["NetworkMap", "PipeRiskList", "ServiceCards", "TimelinePanel"]}.items():
        if name in [f"web/src/components/{item}.tsx" for item in names]: return f"web/src/features/{domain}/components/{Path(name).name}"
    if name in [f"web/src/components/{item}.tsx" for item in ["EvidenceList", "Icons", "StatusPill", "PageHeader", "EmptyState", "MetricCard"]]: return "web/src/shared/ui/" + Path(name).name
    return name


def responsibility(name: str) -> str:
    if is_test(name): return "Regression/negative test: " + Path(name).name
    prefixes = {
        "engineering/src/agents/": "Frozen Agent kernel: routing, evidence, RAG and orchestration",
        "engineering/src/ai_application/": "A-owned AI application ports; not a new registered runtime",
        "engineering/src/shared_contracts/": "Shared typed intent, knowledge and controlled-tool contracts",
        "engineering/src/cuv_application/": "B-owned business capability/API catalogue; no duplicate algorithms",
        "engineering/src/platform/": "FastAPI authentication, authorization, Snapshot/Run persistence and API composition",
        "engineering/src/unified/": "Frozen Report/result identity and integrity contract",
        "engineering/src/pipe_health/": "Existing water 0.1.1 scoring implementation",
        "engineering/src/simulation/": "Existing graph connectivity analysis; no hydraulic restoration claim",
        "engineering/src/decision/": "Existing plan comparison and service/event timeline calculation",
        "engineering/src/adapters/": "Existing model/data service adapters",
        "engineering/src/algorithm_validation/": "Candidate algorithm validation; distinct from formal models",
        "engineering/src/gas_application/": "B-owned gas graph and candidate scoring adapter; no formal business score",
        "engineering/experiments/gas_m0/": "Isolated candidate gas arithmetic/topology/fixtures; pending independent acceptance",
        "engineering/data/": "Synthetic/evaluation fixture input, identity and provenance",
        "web/src/providers/": "Shared data-source and HTTP boundary; Platform must not silently fall back to Demo",
        "web/src/adapters/": "Shared mapping of server data/result semantics into presentation contracts",
        "web/src/generated/": "Generated FastAPI OpenAPI types; do not edit manually",
        "web/src/domain/": "Existing shared frontend domain contracts and constants",
        "web/src/features/assistant/": "A-owned assistant presentation",
        "web/src/features/cuv/": "B-owned CUV/assessment/plan presentation",
        "web/src/features/network/": "B-owned network/timeline presentation",
        "web/src/features/gas/": "B-owned gas candidate screen; reads the controlled FastAPI gas contract only",
        "web/src/features/reports/": "Controlled Report presentation boundary; existing page preserved",
        "web/src/shared/": "Shared UI/contract/display bridge; no private-domain algorithms",
        "web/src/app/": "Controlled app composition and route integration",
        "web/src/pages/": "Existing page composition; review-sensitive pages preserved",
        "web/src/components/": "Existing component implementation or compatibility export",
        "web/src/hooks/": "Shared routing/data-source hooks",
        "web/src/engine/": "Existing explicitly Demo calculation/presentation helpers; no Platform substitution",
        "web/src/data/": "Explicit synthetic Demo network/timeline fixture",
        "web/src/assets/": "Frontend asset/font resource",
        "server/": "Preserved compatibility Demo API; not another formal platform",
        "scripts/": "Development verification/synchronization tooling",
        ".github/": "Repository ownership, CI or PR process",
        "docs/": "Architecture/workflow/source provenance documentation",
    }
    for prefix, meaning in prefixes.items():
        if name.startswith(prefix): return meaning + ": " + Path(name).name
    return "Repository build/configuration/documentation: " + name


def supplemental_tests(name: str, files: dict[str, bytes]) -> list[str]:
    candidates = ["scripts/test_module_boundaries.py"]
    if name.startswith("engineering/src/platform/"):
        candidates += ["engineering/tests/test_platform.py", "engineering/tests/test_m1fix2_identity.py", "engineering/tests/test_m1fix2_platform.py", "engineering/tests/test_m1fix_authz.py"]
    if name.startswith("engineering/src/unified/") or "platformReport" in name or "ReportPage" in name:
        candidates += ["engineering/tests/test_report_identity.py", "engineering/tests/test_report_integrity.py", "engineering/tests/test_report_leaf_integrity.py", "web/src/tests/platformReportApi.test.ts", "web/tests/integration/m1b03/run.mjs"]
    if name.startswith("engineering/data/synthetic/"):
        candidates += ["engineering/tests/test_m1fix_fixture.py", "engineering/tests/test_unified_contracts.py"]
    if name.startswith("engineering/experiments/gas_m0/"):
        candidates += [x for x in files if x.startswith("engineering/experiments/gas_m0/tests/test_")]
    if name.startswith("web/src/") and Path(name).suffix in TS_SUFFIXES:
        candidates += ["web/src/tests/moduleCompatibility.test.ts", "web/src/tests/interaction.browser.test.ts"]
    if name.startswith(("engineering/src/shared_contracts/", "engineering/src/ai_application/", "engineering/src/cuv_application/")):
        candidates += ["engineering/tests/test_module_tool_contracts.py"]
    return [candidate for candidate in candidates if candidate in files]


def matrix(files: dict[str, bytes], graph: dict, frozen: dict, ownership_rules: list[dict] | None = None) -> list[dict]:
    if ownership_rules is None:
        ownership_rules = codeowners_rules(files[".github/CODEOWNERS"].decode("utf-8-sig"))
    freeze_paths = {name for group in frozen.get("groups", {}).values() for name in group}
    deps: dict[str, set[str]] = defaultdict(set)
    for edge in graph["edges"]: deps[edge["source"]].add(edge["target"])
    init_deps: dict[str, set[str]] = defaultdict(set)
    for edge in graph.get("initialization_edges", []): init_deps[edge["source"]].add(edge["target"])
    tests = [name for name in files if is_test(name) and Path(name).suffix in TEXT_SUFFIXES]
    def reachable(name: str, seen=None) -> set[str]:
        seen = set() if seen is None else seen
        for target in deps[name]:
            if target not in seen: seen.add(target); reachable(target, seen)
        return seen
    test_cover = {name: reachable(name) for name in tests}
    rows=[]
    for name in sorted(files):
        target=migration_target(name)
        current_ownership=codeowners_for_path(name, ownership_rules)
        target_ownership=codeowners_for_path(target, ownership_rules)
        ownership=current_ownership["owner"]
        security = any(part in name.lower() for part in ["auth", "security", "snapshot", "platform", "unified", "report", "contract", "outbound", "evidence", "run_context", "agent_store"])
        rows.append({"path":name,"sha256":None if name == "docs/architecture/CURRENT_FILE_MATRIX.json" else digest(files[name]),"current_responsibility":responsibility(name),"direct_dependencies":sorted(deps[name]),"initialization_dependencies":sorted(init_deps[name]),"target_module":str(Path(target).parent),"target_path":target,"owner":ownership,"responsible_github_accounts":current_ownership["accounts"],"ownership_rule":current_ownership["rule"],"ownership_source":"current .github/CODEOWNERS policy, not historical authorship","target_owner":target_ownership["owner"],"target_responsible_github_accounts":target_ownership["accounts"],"target_ownership_rule":target_ownership["rule"],"shared":ownership=="SHARED_CONTROLLED","frozen":name in freeze_paths,"security_contract":security,"movable_now":target!=name and name not in freeze_paths,"compatibility":("old path remains export-only facade" if target!=name else "preserve path and behavior; integration PR for shared/frozen changes"),"required_tests":sorted(set(test for test,targets in test_cover.items() if name in targets or test==name) | set(supplemental_tests(name,files)))})
    return rows


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root",type=Path,default=Path(__file__).resolve().parents[1])
    parser.add_argument("--node",default="node")
    parser.add_argument("--initialize-baseline",action="store_true")
    parser.add_argument("--write-matrix",type=Path)
    parser.add_argument("--json-out",type=Path)
    parser.add_argument("--write-dependencies",type=Path)
    args=parser.parse_args(); root=args.root.resolve()
    frozen=json.loads((root/FREEZE).read_text())
    if args.initialize_baseline:
        destination=root/BASELINE
        if destination.exists(): raise SystemExit("Baseline already exists; edit only in a reviewed integration PR")
        files=tracked_snapshot(root,BASE_COMMIT); graph=collect_graph(root,files,args.node)
        destination.write_text(json.dumps(baseline_document(files,graph,BASE_COMMIT),ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
        (root/"docs/architecture/M0_FILE_MATRIX.json").write_text(json.dumps(matrix(files,graph,frozen,codeowners_rules((root/".github/CODEOWNERS").read_text())),ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    baseline=json.loads((root/BASELINE).read_text())
    files=tracked_snapshot(root); graph=collect_graph(root,files,args.node)
    violations=check(files,graph,baseline,frozen)
    if args.write_matrix: args.write_matrix.write_text(json.dumps(matrix(files,graph,frozen),ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    if args.write_dependencies:
        args.write_dependencies.write_text(json.dumps({"base_commit":baseline["base_commit"],"edges":graph["edges"],"initialization_edges":graph["initialization_edges"],"facades":graph["facades"],"cycles":strongly_connected(graph["edges"]),"shared_legacy_bridges":SHARED_LEGACY_BRIDGES,"limits":baseline["limits"]},ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    output={"status":"PASS" if not violations else "FAIL","checked_files":len(files),"dependency_edges":len(graph["edges"]),"initialization_edges":len(graph["initialization_edges"]),"legacy_exception_count":len(baseline["legacy_exceptions"]),"legacy_cycle_count":len(baseline["cycles"]),"violations":violations,"base_commit":baseline["base_commit"]}
    if args.json_out: args.json_out.write_text(json.dumps(output,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(output,ensure_ascii=False,indent=2)); return int(bool(violations))

if __name__ == "__main__":
    raise SystemExit(main())
