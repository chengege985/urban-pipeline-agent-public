"""Exercise the migration-only gateway using real loopback HTTP and synthetic data."""
from __future__ import annotations
import argparse
import importlib.util
from http.cookiejar import CookieJar
import json
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--backend-root", type=Path, required=True)
    p.add_argument("--output-dir", type=Path)
    a = p.parse_args()
    if importlib.util.find_spec("fcntl") is None:
        p.error("Native Windows FastAPI restoration is BLOCKED by the existing fcntl worker lock; run this HTTP check in Linux/WSL2.")
    root = a.backend_root.resolve()
    out = a.output_dir.resolve() if a.output_dir else Path(tempfile.mkdtemp(prefix="waterpulse-http-check-"))
    if a.output_dir:
        out.mkdir(parents=True, exist_ok=False)
    dist = out / "web-stub"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html><title>Synthetic gateway transport check only</title>", encoding="utf-8")
    checks = []
    log = (out / "server-stderr.log").open("w", encoding="utf-8")
    proc = subprocess.Popen([sys.executable, "-B", str(Path(__file__).with_name("start_synthetic_local.py")),
        "--backend-root", str(root), "--web-dist", str(dist), "--runtime-dir", str(out / "runtime"), "--port", "0"],
        stdout=subprocess.PIPE, stderr=log, text=True, encoding="utf-8")
    def check(name, passed, actual):
        checks.append({"name": name, "passed": bool(passed), "actual": actual})
        if not passed:
            raise AssertionError(name + ": " + str(actual))
    try:
        ready = json.loads(proc.stdout.readline())
        base = ready["base_url"]
        for _ in range(100):
            try:
                urllib.request.urlopen(base + "/health", timeout=2).read()
                break
            except (OSError, urllib.error.URLError):
                time.sleep(.1)
        jar = CookieJar()
        client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        def request(path, data=None, headers=None, authenticated=True):
            headers = {"X-Water-Client": "unified-react", **(headers or {})}
            body = None if data is None else json.dumps(data).encode("utf-8")
            if body is not None:
                headers.update({"Content-Type": "application/json", "Idempotency-Key": "migration-" + str(time.time_ns())})
            req = urllib.request.Request(base + path, body, headers)
            try:
                r = (client.open if authenticated else urllib.request.urlopen)(req, timeout=10)
            except urllib.error.HTTPError as e:
                r = e
            text = r.read().decode()
            return r.status, json.loads(text)
        status, denied = request("/api/v1/capabilities", authenticated=False,
            headers={"X-Water-Token": "forged", "X-Principal-Id": "local-synthetic-operator"})
        check("spoofed_headers_without_session_denied", status == 401, status)
        client.open(ready["login_url"], timeout=10).read()
        check("login_httponly_samesite_cookie", any(c.name == "waterpulse_local" and c.has_nonstandard_attr("HttpOnly") for c in jar), "cookie metadata")
        status, health = request("/health")
        check("mock_only_agent_disabled", status == 200 and health["llm_mode"] == "mock" and not health["agent_enabled"], health)
        sid = ready["snapshot_id"]
        status, view = request("/api/v1/datasets/" + sid + "/view")
        score = next(p["score"] for p in view["pipes"] if p["pipe_id"] == "syn-pipe-fault")
        check("synthetic_frozen_score_read", status == 200 and score["hs"] == 78.5 and score["rs"] == 1.86, score)
        fixture = json.loads((root / "data/synthetic/m1a_unified.json").read_text(encoding="utf-8"))
        status, run = request("/api/v1/simulation-runs", {
            "snapshot_id": sid, "as_of": fixture["as_of"], "fault_edge_id": "syn-fault",
            "isolation_plan_id": "syn-isolate", "horizon_hours": 8})
        check("async_simulation_accepted", status == 202, status)
        rid = run["run_id"]
        for _ in range(200):
            status, run = request("/api/v1/runs/" + rid)
            if run["status"] not in {"queued", "running"}:
                break
            time.sleep(.05)
        check("async_simulation_completed", status == 200 and run["status"] == "succeeded", run["status"])
        status, report = request("/api/v1/runs/" + rid + "/report")
        check("same_run_report_read", status == 200 and report["run_id"] == rid and report["dataset"]["snapshot_id"] == sid
              and report["recomputation"] == "none", status)
        status, denied = request("/api/v1/assistant/tasks", {"question": "test"})
        check("agent_route_excluded", status == 403, status)
        status, denied = request("/api/v1/capabilities", headers={"Origin": "http://example.invalid"})
        check("cross_origin_denied", status == 403, status)
        status, denied = request("/api/v1/runs/" + rid + "/report", authenticated=False)
        check("anonymous_report_denied", status == 401, status)
    finally:
        if proc.poll() is None:
            if sys.platform != "win32":
                proc.send_signal(signal.SIGINT)
            else:
                proc.terminate()
        try:
            stdout, _ = proc.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, _ = proc.communicate()
        log.close()
        # Do not persist the login nonce, session cookie, or private backend token.
        stop = [json.loads(line) for line in stdout.splitlines() if line.startswith('{"stopped"')]
        result = {"checks": checks, "passed": sum(c["passed"] for c in checks), "total": len(checks),
                  "stopped": proc.poll() is not None, "server_shutdown": stop,
                  "scope": "real loopback HTTP; HTML transport stub, not React browser acceptance"}
        (out / "http_summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False))

if __name__ == "__main__":
    main()
