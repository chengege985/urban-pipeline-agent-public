"""本地大模型 API 网关（FIX2 安全整改版）。

边界：
- 只监听 127.0.0.1，不开放公网。
- 在线 SQLite 放本地 ext4；备份使用 AES-256-CBC + PBKDF2 加密后再放 /workdata。
- 不记录 Prompt、回复正文、完整 API Key；数据库只存 key_hash 与 key_prefix。
- 上游固定为 127.0.0.1:30000 的 Qwen3.8-27B，强制 enable_thinking=false。
- 并发许可证、Token 额度预留/结算、Key 软删除、Token 账目语义统一。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import secrets
import shutil
import sqlite3
import stat
import subprocess
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask

BASE_DIR = Path(__file__).resolve().parent
EXPERIMENT_DIR = BASE_DIR.parent
STATIC_DIR = BASE_DIR / "static"
LOCAL_DATA_DIR = Path(os.getenv("LOCAL_GATEWAY_DATA", "/nix/wpn_local_llm/data"))
DB_PATH = Path(os.getenv("LOCAL_GATEWAY_DB", str(LOCAL_DATA_DIR / "gateway.db")))
BACKUP_DIR = Path(os.getenv("LOCAL_GATEWAY_BACKUP", str(EXPERIMENT_DIR / "encrypted_backup")))
LEGACY_BACKUP_DIR = Path(os.getenv("LOCAL_GATEWAY_LEGACY_BACKUP", str(EXPERIMENT_DIR / "backup")))
CONFIG_DIR = Path(os.getenv("LOCAL_GATEWAY_CONFIG", str(LOCAL_DATA_DIR / "config")))

def validated_upstream(value: str) -> str:
    try:
        parsed = urlsplit(value)
        valid = (parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "::1"}
                 and parsed.port is not None and 1024 <= parsed.port <= 65535
                 and not parsed.username and not parsed.password
                 and parsed.path in {"", "/"} and not parsed.query and not parsed.fragment
                 and not any(char.isspace() for char in value))
    except ValueError:
        valid = False
    if not valid:
        raise RuntimeError("local model upstream must be an explicit loopback origin")
    return value.rstrip("/")


UPSTREAM = validated_upstream(os.getenv("LOCAL_GATEWAY_UPSTREAM", "http://127.0.0.1:30000"))


def upstream_auth() -> dict[str, str]:
    return {"Authorization": "Bearer " + os.getenv("LOCAL_GATEWAY_UPSTREAM_TOKEN", "EMPTY")}

MODEL_NAME = os.getenv("LOCAL_GATEWAY_MODEL", "Qwen3.8-27B")
CONTEXT_LIMIT = int(os.getenv("LOCAL_GATEWAY_CONTEXT_LIMIT", "8192"))
MAX_TOKENS_LIMIT = int(os.getenv("LOCAL_GATEWAY_MAX_TOKENS", "2048"))
MAX_BODY_BYTES = int(os.getenv("LOCAL_GATEWAY_MAX_BODY", str(256 * 1024)))
GLOBAL_CONCURRENCY = int(os.getenv("LOCAL_GATEWAY_GLOBAL_CONCURRENCY", "8"))
ADMIN_TOKEN_FILE = CONFIG_DIR / "admin_token"
PPU_SMI = os.getenv("PPU_SMI_BIN", "/usr/local/PPU_SDK/ppu-smi/bin/ppu-smi")
BACKUP_INTERVAL = int(os.getenv("LOCAL_GATEWAY_BACKUP_INTERVAL", "60"))
BACKUP_KEEP = int(os.getenv("LOCAL_GATEWAY_BACKUP_KEEP", "20"))
DISABLE_WARMUP = os.getenv("LOCAL_GATEWAY_DISABLE_WARMUP", "0") == "1"

ALLOWED_TOOLS = {
    "get_pipe_status",
    "get_valve_info",
    "get_node_info",
    "explain_isolation_impact",
    "explain_risk_result",
}
KEY_SCOPES = {"production", "test"}

DB_LOCK = threading.Lock()
RPM_LOCK = threading.Lock()
KEY_RPM: dict[str, list[float]] = {}

STATE = {
    "warmup_done": False,
    "warmup_started_at": None,
    "last_warmup_error": "",
    "last_upstream_error_at": 0.0,
    "last_backup": None,
    "last_backup_error": "",
    "last_restore_error": "",
    "backup_count": 0,
    "restored_from": None,
}


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def best_effort_chmod(path: Path, mode: int) -> None:
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def mode_of(path: Path) -> str:
    try:
        return oct(stat.S_IMODE(path.stat().st_mode))
    except OSError:
        return "unknown"


def load_admin_token() -> str:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    best_effort_chmod(CONFIG_DIR, 0o700)
    env = os.getenv("LOCAL_GATEWAY_ADMIN_TOKEN", "").strip()
    if env:
        return env
    if ADMIN_TOKEN_FILE.exists():
        token = ADMIN_TOKEN_FILE.read_text(encoding="utf-8").strip()
        if token:
            return token
    token = "adm_" + secrets.token_urlsafe(32)
    ADMIN_TOKEN_FILE.write_text(token, encoding="utf-8")
    best_effort_chmod(ADMIN_TOKEN_FILE, 0o600)
    return token


ADMIN_TOKEN = load_admin_token()


def connect_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=5, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    best_effort_chmod(DB_PATH.parent, 0o700)
    with DB_LOCK, connect_db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS api_keys(
              id TEXT PRIMARY KEY,
              name TEXT NOT NULL,
              key_hash TEXT NOT NULL UNIQUE,
              key_prefix TEXT NOT NULL DEFAULT '',
              model TEXT NOT NULL,
              quota_tokens INTEGER NOT NULL DEFAULT 0,
              used_tokens INTEGER NOT NULL DEFAULT 0,
              reserved_tokens INTEGER NOT NULL DEFAULT 0,
              rpm_limit INTEGER NOT NULL DEFAULT 60,
              concurrent_limit INTEGER NOT NULL DEFAULT 4,
              enabled INTEGER NOT NULL DEFAULT 1,
              deleted INTEGER NOT NULL DEFAULT 0,
              scope TEXT NOT NULL DEFAULT 'production',
              created_at TEXT NOT NULL,
              last_used_at TEXT,
              rotated_at TEXT
            );
            CREATE TABLE IF NOT EXISTS requests(
              id TEXT PRIMARY KEY,
              ts TEXT NOT NULL,
              ts_epoch REAL NOT NULL,
              consumer TEXT NOT NULL,
              consumer_id TEXT NOT NULL,
              consumer_name TEXT NOT NULL DEFAULT '',
              key_scope TEXT NOT NULL DEFAULT 'production',
              model TEXT NOT NULL,
              endpoint TEXT NOT NULL,
              prompt_tokens INTEGER NOT NULL DEFAULT 0,
              completion_tokens INTEGER NOT NULL DEFAULT 0,
              total_tokens INTEGER NOT NULL DEFAULT 0,
              upstream_prompt_tokens INTEGER NOT NULL DEFAULT 0,
              upstream_completion_tokens INTEGER NOT NULL DEFAULT 0,
              upstream_total_tokens INTEGER NOT NULL DEFAULT 0,
              estimated_tokens INTEGER NOT NULL DEFAULT 0,
              billed_tokens INTEGER NOT NULL DEFAULT 0,
              usage_source TEXT NOT NULL DEFAULT 'none',
              latency_ms REAL,
              ttft_ms REAL,
              status INTEGER NOT NULL,
              error TEXT,
              stream INTEGER NOT NULL DEFAULT 0,
              tool_calls INTEGER NOT NULL DEFAULT 0,
              usage_missing INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS quota_reservations(
              request_id TEXT PRIMARY KEY,
              key_id TEXT NOT NULL,
              estimated_tokens INTEGER NOT NULL DEFAULT 0,
              status TEXT NOT NULL,
              created_at TEXT NOT NULL,
              settled_at TEXT,
              billed_tokens INTEGER NOT NULL DEFAULT 0,
              usage_source TEXT NOT NULL DEFAULT 'none'
            );
            CREATE INDEX IF NOT EXISTS idx_requests_ts ON requests(ts_epoch);
            CREATE INDEX IF NOT EXISTS idx_requests_status ON requests(status);
            CREATE INDEX IF NOT EXISTS idx_requests_consumer ON requests(consumer_id);
            CREATE INDEX IF NOT EXISTS idx_quota_key ON quota_reservations(key_id, status);
            """
        )
        ensure_column(conn, "api_keys", "key_prefix", "key_prefix TEXT NOT NULL DEFAULT ''")
        ensure_column(conn, "api_keys", "reserved_tokens", "reserved_tokens INTEGER NOT NULL DEFAULT 0")
        ensure_column(conn, "api_keys", "deleted", "deleted INTEGER NOT NULL DEFAULT 0")
        ensure_column(conn, "api_keys", "scope", "scope TEXT NOT NULL DEFAULT 'production'")
        ensure_column(conn, "api_keys", "rotated_at", "rotated_at TEXT")
        ensure_column(conn, "requests", "consumer_name", "consumer_name TEXT NOT NULL DEFAULT ''")
        ensure_column(conn, "requests", "key_scope", "key_scope TEXT NOT NULL DEFAULT 'production'")
        ensure_column(conn, "requests", "upstream_prompt_tokens", "upstream_prompt_tokens INTEGER NOT NULL DEFAULT 0")
        ensure_column(conn, "requests", "upstream_completion_tokens", "upstream_completion_tokens INTEGER NOT NULL DEFAULT 0")
        ensure_column(conn, "requests", "upstream_total_tokens", "upstream_total_tokens INTEGER NOT NULL DEFAULT 0")
        ensure_column(conn, "requests", "estimated_tokens", "estimated_tokens INTEGER NOT NULL DEFAULT 0")
        ensure_column(conn, "requests", "billed_tokens", "billed_tokens INTEGER NOT NULL DEFAULT 0")
        ensure_column(conn, "requests", "usage_source", "usage_source TEXT NOT NULL DEFAULT 'none'")
        conn.execute(
            """UPDATE requests SET
                 consumer_name=CASE WHEN consumer_name='' THEN consumer ELSE consumer_name END,
                 upstream_prompt_tokens=CASE WHEN upstream_prompt_tokens=0 THEN prompt_tokens ELSE upstream_prompt_tokens END,
                 upstream_completion_tokens=CASE WHEN upstream_completion_tokens=0 THEN completion_tokens ELSE upstream_completion_tokens END,
                 upstream_total_tokens=CASE WHEN upstream_total_tokens=0 THEN total_tokens ELSE upstream_total_tokens END,
                 billed_tokens=CASE WHEN billed_tokens=0 AND status<400 THEN total_tokens ELSE billed_tokens END,
                 usage_source=CASE WHEN usage_source='none' AND status<400 THEN 'upstream' ELSE usage_source END
               WHERE estimated_tokens=0"""
        )
    best_effort_chmod(DB_PATH, 0o600)


def backup_passphrase() -> tuple[str | None, str]:
    env = os.getenv("LOCAL_GATEWAY_BACKUP_PASSPHRASE", "").strip()
    if env:
        return env, "environment"
    pass_file = CONFIG_DIR / "backup.pass"
    if pass_file.exists():
        value = pass_file.read_text(encoding="utf-8").strip()
        if value:
            return value, "local_file"
    return None, "missing"


def openssl_env(passphrase: str) -> dict[str, str]:
    env = os.environ.copy()
    env["WPN_LOCAL_GATEWAY_BACKUP_KEY"] = passphrase
    return env


def encrypt_file(src: Path, dst: Path, passphrase: str) -> None:
    openssl = shutil.which("openssl")
    if not openssl:
        raise RuntimeError("openssl_missing")
    proc = subprocess.run(
        [openssl, "enc", "-aes-256-cbc", "-pbkdf2", "-salt", "-in", str(src), "-out", str(dst),
         "-pass", "env:WPN_LOCAL_GATEWAY_BACKUP_KEY"],
        env=openssl_env(passphrase), capture_output=True, timeout=60,
    )
    if proc.returncode != 0:
        raise RuntimeError("encrypt_failed")


def decrypt_file(src: Path, dst: Path, passphrase: str) -> None:
    openssl = shutil.which("openssl")
    if not openssl:
        raise RuntimeError("openssl_missing")
    proc = subprocess.run(
        [openssl, "enc", "-d", "-aes-256-cbc", "-pbkdf2", "-in", str(src), "-out", str(dst),
         "-pass", "env:WPN_LOCAL_GATEWAY_BACKUP_KEY"],
        env=openssl_env(passphrase), capture_output=True, timeout=60,
    )
    if proc.returncode != 0:
        raise RuntimeError("decrypt_failed")


def verify_sqlite_file(path: Path) -> bool:
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=3)
        row = conn.execute("PRAGMA quick_check").fetchone()
        conn.close()
        return bool(row and row[0] == "ok")
    except Exception:
        return False


def do_backup() -> dict:
    if not DB_PATH.exists():
        return {"ok": False, "error": "db_missing"}
    passphrase, source = backup_passphrase()
    if not passphrase:
        STATE["last_backup_error"] = "backup_passphrase_missing"
        return {"ok": False, "error": "backup_passphrase_missing", "passphrase_source": source}
    BACKUP_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    best_effort_chmod(BACKUP_DIR, 0o700)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    token = secrets.token_hex(4)
    final = BACKUP_DIR / f"gateway-{stamp}-{token}.db.enc"
    tmp_db = LOCAL_DATA_DIR / f".gateway-backup-{stamp}-{token}.db"
    tmp_enc = BACKUP_DIR / f".gateway-{stamp}-{token}.enc.tmp"
    try:
        src = connect_db()
        dst = sqlite3.connect(tmp_db)
        try:
            with dst:
                src.backup(dst)
        finally:
            src.close()
            dst.close()
        encrypt_file(tmp_db, tmp_enc, passphrase)
        os.replace(tmp_enc, final)
        best_effort_chmod(final, 0o600)
        backups = sorted(BACKUP_DIR.glob("gateway-*.db.enc"))
        for old in backups[:-BACKUP_KEEP]:
            old.unlink(missing_ok=True)
        STATE["last_backup"] = {
            "path": str(final), "at": now_iso(), "size": final.stat().st_size,
            "encrypted": True, "algorithm": "aes-256-cbc+pbkdf2", "passphrase_source": source,
        }
        STATE["last_backup_error"] = ""
        STATE["backup_count"] = len(list(BACKUP_DIR.glob("gateway-*.db.enc")))
        return {"ok": True, **STATE["last_backup"]}
    except Exception as exc:
        for p in (tmp_db, tmp_enc):
            p.unlink(missing_ok=True)
        STATE["last_backup_error"] = type(exc).__name__
        return {"ok": False, "error": type(exc).__name__, "passphrase_source": source}
    finally:
        tmp_db.unlink(missing_ok=True)
        tmp_enc.unlink(missing_ok=True)


def restore_if_missing() -> None:
    if DB_PATH.exists():
        return
    passphrase, _ = backup_passphrase()
    if not passphrase:
        STATE["last_restore_error"] = "backup_passphrase_missing"
        return
    BACKUP_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    DB_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    best_effort_chmod(DB_PATH.parent, 0o700)
    for enc in sorted(BACKUP_DIR.glob("gateway-*.db.enc"), reverse=True):
        tmp = LOCAL_DATA_DIR / f".restore-{secrets.token_hex(4)}.db"
        try:
            decrypt_file(enc, tmp, passphrase)
            if not verify_sqlite_file(tmp):
                raise RuntimeError("sqlite_quick_check_failed")
            shutil.copy2(tmp, DB_PATH)
            best_effort_chmod(DB_PATH, 0o600)
            STATE["restored_from"] = str(enc)
            STATE["last_restore_error"] = ""
            return
        except Exception as exc:
            STATE["last_restore_error"] = type(exc).__name__
        finally:
            tmp.unlink(missing_ok=True)
    STATE["restored_from"] = None


async def backup_loop() -> None:
    while True:
        await asyncio.sleep(BACKUP_INTERVAL)
        await asyncio.to_thread(do_backup)


async def warmup_loop() -> None:
    STATE["warmup_started_at"] = now_iso()
    if DISABLE_WARMUP:
        STATE["warmup_done"] = True
        STATE["last_warmup_error"] = ""
        return
    async with httpx.AsyncClient(timeout=120.0, trust_env=False, follow_redirects=False) as client:
        while True:
            try:
                r = await client.get(UPSTREAM + "/v1/models", headers=upstream_auth(), timeout=3.0, follow_redirects=False)
                if r.status_code != 200:
                    raise RuntimeError("models_http_%s" % r.status_code)
                r = await client.post(
                    UPSTREAM + "/v1/chat/completions",
                    json={
                        "model": MODEL_NAME,
                        "messages": [{"role": "user", "content": "健康检查：回答一个字，好"}],
                        "temperature": 0,
                        "max_tokens": 8,
                        "chat_template_kwargs": {"enable_thinking": False},
                    },
                    timeout=900.0, headers=upstream_auth(), follow_redirects=False,
                )
                if r.status_code == 200:
                    STATE["warmup_done"] = True
                    STATE["last_warmup_error"] = ""
                    STATE["last_upstream_error_at"] = 0.0
                    return
                raise RuntimeError("warmup_http_%s" % r.status_code)
            except Exception as exc:
                STATE["warmup_done"] = False
                STATE["last_warmup_error"] = type(exc).__name__
                await asyncio.sleep(10)


@dataclass
class ConcurrencyLimiter:
    limit: int
    active: int = 0
    _lock: asyncio.Lock = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.limit = max(1, int(self.limit))
        self._lock = asyncio.Lock()

    async def try_acquire(self) -> bool:
        async with self._lock:
            if self.active >= self.limit:
                return False
            self.active += 1
            return True

    async def set_limit(self, limit: int) -> None:
        async with self._lock:
            self.limit = max(1, int(limit))

    async def release(self) -> None:
        async with self._lock:
            if self.active > 0:
                self.active -= 1

    async def stats(self) -> dict[str, int]:
        async with self._lock:
            return {"active": self.active, "limit": self.limit}


class KeyLimiterManager:
    def __init__(self) -> None:
        self._states: dict[str, ConcurrencyLimiter] = {}
        self._lock = asyncio.Lock()

    async def get_state(self, key_id: str, limit: int) -> ConcurrencyLimiter:
        async with self._lock:
            state = self._states.get(key_id)
            if state is None:
                state = ConcurrencyLimiter(limit)
                self._states[key_id] = state
            return state

    async def set_limit(self, key_id: str, limit: int) -> None:
        state = await self.get_state(key_id, limit)
        await state.set_limit(limit)

    async def stats(self) -> dict[str, dict[str, int]]:
        async with self._lock:
            states = dict(self._states)
        return {k: await v.stats() for k, v in states.items()}


class ConcurrencyLease:
    def __init__(self, key_id: str, global_limiter: ConcurrencyLimiter, key_limiter: ConcurrencyLimiter):
        self.key_id = key_id
        self.global_limiter = global_limiter
        self.key_limiter = key_limiter
        self.released = False
        self._lock = asyncio.Lock()

    async def release(self) -> None:
        async with self._lock:
            if self.released:
                return
            self.released = True
        await self.key_limiter.release()
        await self.global_limiter.release()


GLOBAL_LIMITER: ConcurrencyLimiter | None = None
KEY_LIMITERS: KeyLimiterManager | None = None


async def reset_limiters() -> None:
    global GLOBAL_LIMITER, KEY_LIMITERS
    GLOBAL_LIMITER = ConcurrencyLimiter(GLOBAL_CONCURRENCY)
    KEY_LIMITERS = KeyLimiterManager()


async def acquire_lease(key: dict) -> ConcurrencyLease | None:
    global GLOBAL_LIMITER, KEY_LIMITERS
    if GLOBAL_LIMITER is None or KEY_LIMITERS is None:
        await reset_limiters()
    assert GLOBAL_LIMITER is not None and KEY_LIMITERS is not None
    if not await GLOBAL_LIMITER.try_acquire():
        return None
    key_limiter = await KEY_LIMITERS.get_state(key["id"], int(key.get("concurrent_limit") or 1))
    if not await key_limiter.try_acquire():
        await GLOBAL_LIMITER.release()
        return None
    return ConcurrencyLease(key["id"], GLOBAL_LIMITER, key_limiter)


async def concurrency_stats() -> dict:
    global GLOBAL_LIMITER, KEY_LIMITERS
    if GLOBAL_LIMITER is None or KEY_LIMITERS is None:
        await reset_limiters()
    assert GLOBAL_LIMITER is not None and KEY_LIMITERS is not None
    return {"global": await GLOBAL_LIMITER.stats(), "keys": await KEY_LIMITERS.stats()}


@asynccontextmanager
async def lifespan(app: FastAPI):
    restore_if_missing()
    init_db()
    await reset_limiters()
    tasks = [asyncio.create_task(backup_loop()), asyncio.create_task(warmup_loop())]
    yield
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    await asyncio.to_thread(do_backup)


app = FastAPI(title="WaterPipNet Local LLM Gateway FIX2", version="1.1.0", lifespan=lifespan)
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def secure_headers(response, request_id: str | None = None):
    if request_id:
        response.headers["X-Request-Id"] = request_id
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    return response


@app.middleware("http")
async def request_id_and_body_limit(request: Request, call_next):
    request.state.request_id = "req_" + secrets.token_hex(10)
    if request.url.path.startswith("/v1/"):
        length = request.headers.get("content-length")
        if length:
            try:
                if int(length) > MAX_BODY_BYTES:
                    return secure_headers(JSONResponse(
                        status_code=413,
                        content={"error": {"message": "request body too large", "type": "payload_too_large"}},
                    ), request.state.request_id)
            except ValueError:
                return secure_headers(JSONResponse(
                    status_code=400, content={"error": {"message": "invalid content-length"}}
                ), request.state.request_id)
        body = await request.body()
        if len(body) > MAX_BODY_BYTES:
            return secure_headers(JSONResponse(
                status_code=413,
                content={"error": {"message": "request body too large", "type": "payload_too_large"}},
            ), request.state.request_id)
    response = await call_next(request)
    return secure_headers(response, request.state.request_id)


def require_admin(request: Request) -> None:
    token = request.headers.get("X-Admin-Token", "")
    if not token or not secrets.compare_digest(token, ADMIN_TOKEN):
        raise HTTPException(status_code=401, detail="admin_auth_required")


async def json_body(request: Request) -> Any:
    try:
        return await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid_json")


def authenticate(request: Request) -> dict:
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="api_key_required")
    raw = header[7:].strip()
    if not raw:
        raise HTTPException(status_code=401, detail="api_key_required")
    key_hash = sha256_text(raw)
    with DB_LOCK, connect_db() as conn:
        row = conn.execute(
            "SELECT * FROM api_keys WHERE key_hash=? AND enabled=1 AND deleted=0", (key_hash,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=401, detail="invalid_api_key")
        conn.execute("UPDATE api_keys SET last_used_at=? WHERE id=?", (now_iso(), row["id"]))
        return dict(row)


REQUEST_FIELDS = [
    "id", "ts", "ts_epoch", "consumer", "consumer_id", "consumer_name", "key_scope", "model", "endpoint",
    "prompt_tokens", "completion_tokens", "total_tokens",
    "upstream_prompt_tokens", "upstream_completion_tokens", "upstream_total_tokens",
    "estimated_tokens", "billed_tokens", "usage_source",
    "latency_ms", "ttft_ms", "status", "error", "stream", "tool_calls", "usage_missing",
]


def log_request(**kw) -> None:
    defaults: dict[str, Any] = {
        "ts": now_iso(), "ts_epoch": time.time(), "consumer": "", "consumer_id": "", "consumer_name": "",
        "key_scope": "production", "model": MODEL_NAME, "endpoint": "/v1/chat/completions",
        "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
        "upstream_prompt_tokens": 0, "upstream_completion_tokens": 0, "upstream_total_tokens": 0,
        "estimated_tokens": 0, "billed_tokens": 0, "usage_source": "none",
        "latency_ms": 0, "ttft_ms": None, "error": None, "stream": 0,
        "tool_calls": 0, "usage_missing": 0,
    }
    defaults.update(kw)
    values = {k: defaults.get(k) for k in REQUEST_FIELDS}
    cols = ",".join(REQUEST_FIELDS)
    marks = ",".join(":" + k for k in REQUEST_FIELDS)
    with DB_LOCK, connect_db() as conn:
        conn.execute(f"INSERT OR REPLACE INTO requests({cols}) VALUES({marks})", values)


def request_defaults(request_id: str, key: dict, stream: bool, estimated: int = 0) -> dict[str, Any]:
    return {
        "id": request_id, "consumer": key["name"], "consumer_id": key["id"],
        "consumer_name": key["name"], "key_scope": key.get("scope", "production"),
        "model": MODEL_NAME, "endpoint": "/v1/chat/completions",
        "stream": int(stream), "estimated_tokens": int(estimated),
    }


def check_rpm(key: dict) -> bool:
    limit = int(key.get("rpm_limit") or 0)
    if limit <= 0:
        return True
    now = time.time()
    with RPM_LOCK:
        bucket = KEY_RPM.setdefault(key["id"], [])
        bucket[:] = [t for t in bucket if now - t < 60]
        if len(bucket) >= limit:
            return False
        bucket.append(now)
        return True


def validate_int(value: Any, field: str, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise HTTPException(status_code=400, detail=f"{field}_must_be_integer")
    if value < minimum or value > maximum:
        raise HTTPException(status_code=400, detail=f"{field}_out_of_range")
    return int(value)


def validate_key_payload(body: Any, *, create: bool) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="body_must_be_object")
    allowed = {"name", "model", "quota_tokens", "rpm_limit", "concurrent_limit", "enabled", "scope"}
    unknown = sorted(set(body) - allowed)
    if unknown:
        raise HTTPException(status_code=400, detail="unknown_field:" + unknown[0])
    out: dict[str, Any] = {}
    if "name" in body or create:
        name = body.get("name")
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 64:
            raise HTTPException(status_code=400, detail="name_invalid")
        if any(ord(c) < 32 for c in name):
            raise HTTPException(status_code=400, detail="name_invalid")
        out["name"] = name.strip()
    if "model" in body or create:
        model = body.get("model", MODEL_NAME)
        if model != MODEL_NAME or not isinstance(model, str):
            raise HTTPException(status_code=400, detail="model_not_allowed")
        out["model"] = MODEL_NAME
    if "quota_tokens" in body or create:
        out["quota_tokens"] = validate_int(body.get("quota_tokens", 0), "quota_tokens", minimum=0, maximum=10**12)
    if "rpm_limit" in body or create:
        out["rpm_limit"] = validate_int(body.get("rpm_limit", 60), "rpm_limit", minimum=1, maximum=10000)
    if "concurrent_limit" in body or create:
        out["concurrent_limit"] = validate_int(body.get("concurrent_limit", 4), "concurrent_limit", minimum=1, maximum=64)
    if "enabled" in body:
        if not isinstance(body["enabled"], bool):
            raise HTTPException(status_code=400, detail="enabled_must_be_boolean")
        out["enabled"] = 1 if body["enabled"] else 0
    if create:
        scope = body.get("scope", "production")
        if scope not in KEY_SCOPES or not isinstance(scope, str):
            raise HTTPException(status_code=400, detail="scope_invalid")
        out["scope"] = scope
    elif "scope" in body:
        raise HTTPException(status_code=400, detail="scope_immutable")
    return out


def new_raw_key(scope: str) -> tuple[str, str]:
    if scope == "test":
        raw = "sk-test-" + secrets.token_urlsafe(24)
        prefix = raw[:16]
    else:
        raw = "sk-local-" + secrets.token_urlsafe(24)
        prefix = raw[:16]
    return raw, prefix


def reserve_quota(key: dict, request_id: str, estimated: int) -> tuple[bool, str]:
    """BEGIN IMMEDIATE 事务：检查额度、预留、写 reservation；失败不预留。"""
    conn = connect_db()
    try:
        with DB_LOCK:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT quota_tokens,used_tokens,reserved_tokens,enabled,deleted FROM api_keys WHERE id=?",
                (key["id"],),
            ).fetchone()
            if not row or row["deleted"] or not row["enabled"]:
                conn.rollback()
                return False, "invalid_api_key"
            quota = int(row["quota_tokens"] or 0)
            used = int(row["used_tokens"] or 0)
            reserved = int(row["reserved_tokens"] or 0)
            if quota > 0 and used + reserved + estimated > quota:
                conn.rollback()
                return False, "quota_exceeded"
            status = "reserved" if quota > 0 else "unlimited"
            if quota > 0:
                conn.execute(
                    "UPDATE api_keys SET reserved_tokens=reserved_tokens+? WHERE id=?",
                    (estimated, key["id"]),
                )
            conn.execute(
                """INSERT INTO quota_reservations(request_id,key_id,estimated_tokens,status,created_at)
                   VALUES(?,?,?,?,?)""",
                (request_id, key["id"], estimated, status, now_iso()),
            )
            conn.commit()
            return True, status
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()


def settle_quota(request_id: str, *, success: bool, billed_tokens: int, usage_source: str) -> int:
    """幂等结算：释放预留、扣减实际/标识为估计的 billed_tokens；失败请求扣 0。"""
    conn = connect_db()
    try:
        with DB_LOCK:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM quota_reservations WHERE request_id=?", (request_id,)
            ).fetchone()
            if not row or row["status"] in {"settled", "released"}:
                conn.rollback()
                return 0
            estimated = int(row["estimated_tokens"] or 0)
            billed = 0 if not success else max(0, min(int(billed_tokens), estimated if estimated > 0 else int(billed_tokens)))
            source = usage_source if success else "none"
            if row["status"] == "reserved":
                if success:
                    conn.execute(
                        """UPDATE api_keys
                           SET reserved_tokens=MAX(0,reserved_tokens-?), used_tokens=used_tokens+?
                           WHERE id=?""",
                        (estimated, billed, row["key_id"]),
                    )
                else:
                    conn.execute(
                        "UPDATE api_keys SET reserved_tokens=MAX(0,reserved_tokens-?) WHERE id=?",
                        (estimated, row["key_id"]),
                    )
            elif success:
                conn.execute(
                    "UPDATE api_keys SET used_tokens=used_tokens+? WHERE id=?", (billed, row["key_id"])
                )
            conn.execute(
                """UPDATE quota_reservations
                   SET status='settled', settled_at=?, billed_tokens=?, usage_source=?
                   WHERE request_id=?""",
                (now_iso(), billed, source, request_id),
            )
            conn.commit()
            return billed
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()


def usage_fields(usage: dict | None, estimated: int) -> dict[str, Any]:
    if not usage:
        return {
            "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
            "upstream_prompt_tokens": 0, "upstream_completion_tokens": 0, "upstream_total_tokens": 0,
            "billed_tokens": 0, "usage_source": "none", "usage_missing": 1,
        }
    p = int(usage.get("prompt_tokens") or 0)
    c = int(usage.get("completion_tokens") or 0)
    t = int(usage.get("total_tokens") or (p + c))
    billed = min(t, max(1, estimated))
    return {
        "prompt_tokens": p, "completion_tokens": c, "total_tokens": t,
        "upstream_prompt_tokens": p, "upstream_completion_tokens": c, "upstream_total_tokens": t,
        "billed_tokens": billed,
        "usage_source": "upstream_capped" if t > billed else "upstream",
        "usage_missing": 0,
    }


def failed_usage_fields() -> dict[str, Any]:
    return {
        "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
        "upstream_prompt_tokens": 0, "upstream_completion_tokens": 0, "upstream_total_tokens": 0,
        "billed_tokens": 0, "usage_source": "none", "usage_missing": 1,
    }


def estimate_usage_fields(estimated: int) -> dict[str, Any]:
    return {
        "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
        "upstream_prompt_tokens": 0, "upstream_completion_tokens": 0, "upstream_total_tokens": 0,
        "billed_tokens": estimated, "usage_source": "estimate", "usage_missing": 1,
    }


async def tokenize_count(body: dict) -> tuple[int | None, str]:
    payload = {
        "model": MODEL_NAME,
        "messages": body.get("messages"),
        "chat_template_kwargs": {"enable_thinking": False},
    }
    if body.get("tools"):
        payload["tools"] = body["tools"]
        payload["tool_choice"] = body.get("tool_choice") or "auto"
    try:
        async with httpx.AsyncClient(timeout=10.0, trust_env=False, follow_redirects=False) as client:
            r = await client.post(UPSTREAM + "/v1/tokenize", json=payload, headers=upstream_auth(), follow_redirects=False)
            if r.status_code != 200:
                return None, "tokenize_http_%s" % r.status_code
            data = r.json()
            for key in ("count", "tokens", "length"):
                if isinstance(data.get(key), int):
                    return int(data[key]), "upstream"
            if isinstance(data.get("input_ids"), list):
                return len(data["input_ids"]), "upstream"
            return None, "tokenize_unknown_shape"
    except Exception as exc:
        return None, "tokenize_" + type(exc).__name__


def validate_chat(body: dict, key: dict) -> tuple[dict, int | None, str]:
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="body_must_be_object")
    model = body.get("model")
    if model != MODEL_NAME:
        raise HTTPException(status_code=400, detail="model_not_allowed")
    if key.get("model") != MODEL_NAME:
        raise HTTPException(status_code=403, detail="key_model_not_allowed")
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages or len(messages) > 50:
        raise HTTPException(status_code=400, detail="messages_invalid")
    for message in messages:
        if not isinstance(message, dict) or message.get("role") not in {"system", "user", "assistant", "tool"}:
            raise HTTPException(status_code=400, detail="message_role_invalid")
        content = message.get("content")
        if content is not None and not isinstance(content, str):
            raise HTTPException(status_code=400, detail="multimodal_not_supported")
    temperature = body.get("temperature", 0)
    if isinstance(temperature, bool) or not isinstance(temperature, (int, float)):
        raise HTTPException(status_code=400, detail="temperature_invalid")
    if not 0 <= float(temperature) <= 1:
        raise HTTPException(status_code=400, detail="temperature_out_of_range")
    top_p = body.get("top_p", 1)
    if isinstance(top_p, bool) or not isinstance(top_p, (int, float)) or not 0 < float(top_p) <= 1:
        raise HTTPException(status_code=400, detail="top_p_out_of_range")
    if body.get("n", 1) != 1:
        raise HTTPException(status_code=400, detail="n_must_be_1")
    max_tokens = body.get("max_tokens", body.get("max_completion_tokens", 512))
    if isinstance(max_tokens, bool) or not isinstance(max_tokens, int):
        raise HTTPException(status_code=400, detail="max_tokens_invalid")
    if not 1 <= max_tokens <= MAX_TOKENS_LIMIT:
        raise HTTPException(status_code=400, detail="max_tokens_out_of_range")
    tools = body.get("tools")
    if tools:
        if not isinstance(tools, list) or len(tools) > 8:
            raise HTTPException(status_code=400, detail="tools_invalid")
        for tool in tools:
            name = ((tool or {}).get("function") or {}).get("name")
            if name not in ALLOWED_TOOLS:
                raise HTTPException(status_code=400, detail="tool_not_allowed")
    stream = bool(body.get("stream", False))
    normalized = dict(body)
    normalized["model"] = MODEL_NAME
    normalized["temperature"] = float(temperature)
    normalized["top_p"] = float(top_p)
    normalized["max_tokens"] = max_tokens
    normalized.pop("max_completion_tokens", None)
    normalized["chat_template_kwargs"] = {"enable_thinking": False}
    if stream:
        normalized["stream_options"] = {"include_usage": True}
    return normalized, None, ""


async def readiness() -> dict:
    process = False
    try:
        out = subprocess.run(["pgrep", "-af", "sglang.launch_server"], capture_output=True, text=True, timeout=3)
        process = out.returncode == 0 and bool(out.stdout.strip())
    except Exception:
        process = False
    models_ok = False
    try:
        async with httpx.AsyncClient(timeout=2.0, trust_env=False, follow_redirects=False) as client:
            r = await client.get(UPSTREAM + "/v1/models", headers=upstream_auth(), follow_redirects=False)
            models_ok = r.status_code == 200
    except Exception:
        models_ok = False
    if not models_ok and not process:
        state = "offline"
    elif process and not models_ok:
        state = "loading"
    elif models_ok and not STATE["warmup_done"]:
        state = "warming"
    else:
        state = "ready"
    if state == "ready" and STATE["last_upstream_error_at"] and time.time() - STATE["last_upstream_error_at"] < 60:
        state = "degraded"
    return {"state": state, "process_running": process, "models_ok": models_ok,
            "warmup_done": STATE["warmup_done"], "last_warmup_error": STATE["last_warmup_error"],
            "upstream": UPSTREAM, "model": MODEL_NAME, "context_limit": CONTEXT_LIMIT}


def read_ppu() -> dict:
    try:
        out = subprocess.run([PPU_SMI], capture_output=True, text=True, timeout=5)
        text = out.stdout
    except Exception as exc:
        return {"available": False, "error": type(exc).__name__, "cards": []}
    cards: list[dict] = []
    for line in text.splitlines():
        m = re.search(r"\|\s*(\d+)\s+PPU-ZW810E", line)
        if m:
            cards.append({"index": int(m.group(1)), "name": "PPU-ZW810E", "mem_used_mib": None,
                          "mem_total_mib": None, "util_pct": None, "temp_c": None, "power_w": None})
            continue
        m = re.search(r"(\d+)MiB\s*/\s*(\d+)MiB", line)
        if m and cards:
            cards[-1]["mem_used_mib"] = int(m.group(1))
            cards[-1]["mem_total_mib"] = int(m.group(2))
        m = re.search(r"\|\s*(\d+)%\s+Default", line)
        if m and cards:
            cards[-1]["util_pct"] = int(m.group(1))
        m = re.search(r"\s(\d+)C\s", line)
        if m and cards and cards[-1]["temp_c"] is None:
            cards[-1]["temp_c"] = int(m.group(1))
        m = re.search(r"(\d+)W\s*/\s*(\d+)W", line)
        if m and cards:
            cards[-1]["power_w"] = int(m.group(1))
    return {"available": bool(cards), "cards": cards, "source": PPU_SMI}


def overview() -> dict:
    now = time.time()
    with DB_LOCK, connect_db() as conn:
        row = conn.execute(
            """SELECT COUNT(*) total,
            COALESCE(SUM(upstream_prompt_tokens),0) prompt_tokens,
            COALESCE(SUM(upstream_completion_tokens),0) completion_tokens,
            COALESCE(SUM(upstream_total_tokens),0) total_tokens,
            COALESCE(SUM(estimated_tokens),0) estimated_tokens,
            COALESCE(SUM(billed_tokens),0) billed_tokens,
            COALESCE(AVG(latency_ms),0) avg_latency_ms,
            COALESCE(SUM(CASE WHEN status<400 THEN 1 ELSE 0 END),0) ok,
            COALESCE(SUM(CASE WHEN status>=400 THEN 1 ELSE 0 END),0) errors,
            COALESCE(SUM(CASE WHEN status=499 THEN 1 ELSE 0 END),0) cancelled
            FROM requests"""
        ).fetchone()
        rpm = conn.execute("SELECT COUNT(*) c FROM requests WHERE ts_epoch>=?", (now - 60,)).fetchone()["c"]
        ttfts = [r[0] for r in conn.execute(
            "SELECT ttft_ms FROM requests WHERE ttft_ms IS NOT NULL ORDER BY ts_epoch DESC LIMIT 1000").fetchall()]
        lats = [r[0] for r in conn.execute(
            "SELECT latency_ms FROM requests WHERE latency_ms IS NOT NULL ORDER BY ts_epoch DESC LIMIT 1000").fetchall()]
    def pct(values, q):
        if not values:
            return None
        values = sorted(values)
        idx = min(len(values) - 1, int(round((len(values) - 1) * q)))
        return round(values[idx], 2)
    total = row["total"] or 0
    ok = row["ok"] or 0
    return {
        "total_requests": total,
        "rpm": rpm,
        "prompt_tokens": row["prompt_tokens"],
        "completion_tokens": row["completion_tokens"],
        "total_tokens": row["total_tokens"],
        "estimated_tokens": row["estimated_tokens"],
        "billed_tokens": row["billed_tokens"],
        "avg_latency_ms": round(row["avg_latency_ms"] or 0, 2),
        "ttft_p50_ms": pct(ttfts, 0.50),
        "ttft_p95_ms": pct(ttfts, 0.95),
        "latency_p95_ms": pct(lats, 0.95),
        "ok_requests": ok,
        "error_requests": row["errors"],
        "cancelled": row["cancelled"],
        "success_rate": round(ok / total, 4) if total else None,
    }


def timeseries(minutes: int = 30) -> dict:
    since = time.time() - minutes * 60
    with DB_LOCK, connect_db() as conn:
        rows = conn.execute(
            """SELECT CAST(ts_epoch/60 AS INTEGER)*60 bucket, COUNT(*) requests,
            COALESCE(SUM(upstream_prompt_tokens),0) prompt_tokens,
            COALESCE(SUM(upstream_completion_tokens),0) completion_tokens,
            COALESCE(SUM(upstream_total_tokens),0) total_tokens,
            COALESCE(SUM(estimated_tokens),0) estimated_tokens,
            COALESCE(SUM(billed_tokens),0) billed_tokens
            FROM requests WHERE ts_epoch>=? GROUP BY bucket ORDER BY bucket""",
            (since,),
        ).fetchall()
    return {"minutes": minutes, "points": [dict(r) for r in rows]}


def list_keys() -> list[dict]:
    with DB_LOCK, connect_db() as conn:
        rows = conn.execute(
            """SELECT id,name,key_prefix,model,quota_tokens,used_tokens,reserved_tokens,rpm_limit,
            concurrent_limit,enabled,deleted,scope,created_at,last_used_at,rotated_at
            FROM api_keys ORDER BY created_at"""
        ).fetchall()
    return [dict(r) for r in rows]


def recent_requests(limit: int = 50) -> list[dict]:
    with DB_LOCK, connect_db() as conn:
        rows = conn.execute(
            """SELECT id,ts,consumer,consumer_id,consumer_name,key_scope,model,endpoint,
            prompt_tokens,completion_tokens,total_tokens,upstream_total_tokens,estimated_tokens,
            billed_tokens,usage_source,latency_ms,ttft_ms,status,error,stream,tool_calls,usage_missing
            FROM requests ORDER BY ts_epoch DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def recent_errors(limit: int = 50) -> list[dict]:
    with DB_LOCK, connect_db() as conn:
        rows = conn.execute(
            """SELECT id,ts,consumer,consumer_name,key_scope,model,status,error,latency_ms,
            estimated_tokens,billed_tokens,usage_source
            FROM requests WHERE status>=400 ORDER BY ts_epoch DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


@app.get("/")
async def index():
    index_file = STATIC_DIR / "index.html"
    if not index_file.exists():
        return JSONResponse({"error": "dashboard_missing"}, status_code=500)
    return FileResponse(str(index_file))


@app.get("/health")
async def health():
    ready = await readiness()
    # Public liveness never exposes filesystem paths, upstream URLs or errors.
    return {"status": "ok" if ready["state"] == "ready" else "degraded",
            "state": ready["state"], "model": MODEL_NAME}



@app.get("/v1/models")
async def models(request: Request):
    key = authenticate(request)
    if key.get("model") != MODEL_NAME:
        raise HTTPException(status_code=403, detail="key_model_not_allowed")
    return {"object": "list", "data": [{"id": MODEL_NAME, "object": "model", "owned_by": "local-gateway",
                                        "max_model_len": CONTEXT_LIMIT}]}


async def upstream_error_response(response: httpx.Response, request_id: str) -> JSONResponse:
    # Never forward model server exception bodies, paths, credentials or a
    # redirect Location. Accounting/release still runs in the existing flow.
    status = response.status_code if 400 <= response.status_code <= 599 else 502
    return JSONResponse(status_code=status,
                        content={"error": {"message": "upstream_request_failed", "type": "upstream_error"}},
                        headers={"X-Request-Id": request_id})


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    request_id = request.state.request_id
    started = time.perf_counter()
    key = authenticate(request)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": {"message": "invalid_json"}},
                            headers={"X-Request-Id": request_id})
    normalized, _, tokenize_error = validate_chat(body, key)
    stream = bool(normalized.get("stream", False))
    max_tokens = int(normalized["max_tokens"])
    defaults = request_defaults(request_id, key, stream)

    if not check_rpm(key):
        log_request(**{**defaults, **failed_usage_fields(), "status": 429, "error": "rpm_limit",
                       "latency_ms": round((time.perf_counter() - started) * 1000, 2)})
        return JSONResponse(status_code=429, content={"error": {"message": "rpm_limit_exceeded"}},
                            headers={"X-Request-Id": request_id})

    lease = await acquire_lease(key)
    if lease is None:
        log_request(**{**defaults, **failed_usage_fields(), "status": 429, "error": "concurrency_limit",
                       "latency_ms": round((time.perf_counter() - started) * 1000, 2)})
        return JSONResponse(status_code=429, content={"error": {"message": "concurrency_limit_exceeded"}},
                            headers={"X-Request-Id": request_id})

    reservation = False
    settlement_done = False
    stream_started = False
    prompt_tokens: int | None = None
    estimated = 0

    try:
        prompt_tokens, tokenize_source = await tokenize_count(normalized)
        if prompt_tokens is None:
            prompt_tokens = max(1, len(json.dumps(normalized.get("messages") or [], ensure_ascii=False)) // 2)
            tokenize_error = tokenize_source
        estimated = int(prompt_tokens) + max_tokens
        defaults["estimated_tokens"] = estimated

        if int(prompt_tokens) + max_tokens > CONTEXT_LIMIT:
            log_request(**{**defaults, **failed_usage_fields(), "status": 400,
                           "error": "context_budget_exceeded",
                           "latency_ms": round((time.perf_counter() - started) * 1000, 2)})
            return JSONResponse(status_code=400, content={"error": {
                "message": "context_budget_exceeded", "prompt_tokens": prompt_tokens,
                "max_tokens": max_tokens, "context_limit": CONTEXT_LIMIT}}, headers={"X-Request-Id": request_id})

        ok, reason = reserve_quota(key, request_id, estimated)
        if not ok:
            status = 401 if reason == "invalid_api_key" else 429
            log_request(**{**defaults, **failed_usage_fields(), "status": status, "error": reason,
                           "latency_ms": round((time.perf_counter() - started) * 1000, 2)})
            return JSONResponse(status_code=status, content={"error": {"message": reason}},
                                headers={"X-Request-Id": request_id})
        reservation = True

        headers = {"Content-Type": "application/json"}
        upstream_headers = {"Content-Type": "application/json", **upstream_auth()}

        if stream:
            client = httpx.AsyncClient(
                timeout=httpx.Timeout(connect=5.0, read=300.0, write=30.0, pool=5.0),
                trust_env=False, follow_redirects=False,
            )
            req = client.build_request("POST", UPSTREAM + "/v1/chat/completions", json=normalized,
                                       headers=upstream_headers)
            resp = await client.send(req, stream=True, follow_redirects=False)
            if resp.status_code != 200:
                await resp.aread()
                await resp.aclose()
                await client.aclose()
                billed = settle_quota(request_id, success=False, billed_tokens=0, usage_source="none")
                settlement_done = True
                STATE["last_upstream_error_at"] = time.time()
                log_request(**{**defaults, **failed_usage_fields(), "billed_tokens": billed,
                               "status": resp.status_code, "error": "upstream_%s" % resp.status_code,
                               "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                               "stream": 1})
                return await upstream_error_response(resp, request_id)

            STATE["last_upstream_error_at"] = 0.0

            async def stream_background():
                nonlocal settlement_done
                if reservation and not settlement_done:
                    settle_quota(request_id, success=False, billed_tokens=0, usage_source="none")
                    settlement_done = True
                await lease.release()

            async def generator():
                nonlocal settlement_done
                ttft = None
                usage = None
                tool_calls = 0
                status = 200
                error = None
                try:
                    async for line in resp.aiter_lines():
                        if not line:
                            continue
                        if line.startswith("data: "):
                            data = line[6:]
                            if data.strip() == "[DONE]":
                                break
                            try:
                                payload = json.loads(data)
                            except Exception:
                                yield line + "\n\n"
                                continue
                            if payload.get("usage"):
                                usage = payload["usage"]
                            for choice in payload.get("choices") or []:
                                delta = choice.get("delta") or {}
                                if delta.get("content") and ttft is None:
                                    ttft = (time.perf_counter() - started) * 1000
                                if delta.get("tool_calls"):
                                    tool_calls += len(delta["tool_calls"])
                            yield line + "\n\n"
                        else:
                            yield line + "\n"
                    yield "data: [DONE]\n\n"
                except (asyncio.CancelledError, GeneratorExit):
                    status = 499
                    error = "client_disconnected"
                    raise
                except Exception as exc:
                    status = 502
                    error = "stream_" + type(exc).__name__
                    STATE["last_upstream_error_at"] = time.time()
                finally:
                    await resp.aclose()
                    await client.aclose()
                    if status == 200:
                        fields = usage_fields(usage, estimated) if usage else estimate_usage_fields(estimated)
                    else:
                        fields = failed_usage_fields()
                    billed = 0
                    if not settlement_done:
                        billed = settle_quota(
                            request_id, success=status == 200,
                            billed_tokens=fields["billed_tokens"], usage_source=fields["usage_source"],
                        )
                        settlement_done = True
                    fields["billed_tokens"] = billed
                    log_request(**{**defaults, **fields, "status": status, "error": error,
                                   "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                                   "ttft_ms": ttft, "stream": 1, "tool_calls": tool_calls})
                    await lease.release()

            response = StreamingResponse(
                generator(), media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "X-Request-Id": request_id},
                background=BackgroundTask(stream_background),
            )
            stream_started = True
            return response

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5.0, read=300.0, write=30.0, pool=5.0),
            trust_env=False, follow_redirects=False,
        ) as client:
            resp = await client.post(UPSTREAM + "/v1/chat/completions", json=normalized,
                                     headers=upstream_headers, follow_redirects=False)
        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        if resp.status_code != 200:
            STATE["last_upstream_error_at"] = time.time()
            billed = settle_quota(request_id, success=False, billed_tokens=0, usage_source="none")
            settlement_done = True
            log_request(**{**defaults, **failed_usage_fields(), "billed_tokens": billed,
                           "status": resp.status_code, "error": "upstream_%s" % resp.status_code,
                           "latency_ms": latency_ms})
            return await upstream_error_response(resp, request_id)
        STATE["last_upstream_error_at"] = 0.0
        data = resp.json()
        usage = data.get("usage") or {}
        fields = usage_fields(usage, estimated) if usage else estimate_usage_fields(estimated)
        tool_calls = sum(len(choice.get("message", {}).get("tool_calls") or [])
                         for choice in data.get("choices") or [])
        billed = settle_quota(request_id, success=True, billed_tokens=fields["billed_tokens"],
                              usage_source=fields["usage_source"])
        settlement_done = True
        fields["billed_tokens"] = billed
        log_request(**{**defaults, **fields, "status": 200, "error": None,
                       "latency_ms": latency_ms, "tool_calls": tool_calls})
        return JSONResponse(content=data, headers={"X-Request-Id": request_id})

    except httpx.ConnectError:
        STATE["last_upstream_error_at"] = time.time()
        log_request(**{**defaults, **failed_usage_fields(), "status": 503,
                       "error": "upstream_connect_error",
                       "latency_ms": round((time.perf_counter() - started) * 1000, 2)})
        return JSONResponse(status_code=503, content={"error": {"message": "model_service_unavailable",
                                                                "type": "upstream_unavailable"}},
                            headers={"X-Request-Id": request_id})
    except httpx.TimeoutException:
        STATE["last_upstream_error_at"] = time.time()
        log_request(**{**defaults, **failed_usage_fields(), "status": 504, "error": "upstream_timeout",
                       "latency_ms": round((time.perf_counter() - started) * 1000, 2)})
        return JSONResponse(status_code=504, content={"error": {"message": "model_service_timeout",
                                                                "type": "upstream_timeout"}},
                            headers={"X-Request-Id": request_id})
    except asyncio.CancelledError:
        if reservation and not settlement_done:
            settle_quota(request_id, success=False, billed_tokens=0, usage_source="none")
            settlement_done = True
        raise
    except Exception as exc:
        STATE["last_upstream_error_at"] = time.time()
        log_request(**{**defaults, **failed_usage_fields(), "status": 502,
                       "error": "upstream_" + type(exc).__name__,
                       "latency_ms": round((time.perf_counter() - started) * 1000, 2)})
        return JSONResponse(status_code=502, content={"error": {"message": "bad_gateway",
                                                                "type": "upstream_error"}},
                            headers={"X-Request-Id": request_id})
    finally:
        if not stream_started:
            if reservation and not settlement_done:
                settle_quota(request_id, success=False, billed_tokens=0, usage_source="none")
                settlement_done = True
            await lease.release()


@app.get("/admin/api/overview")
async def admin_overview(request: Request):
    require_admin(request)
    return {"overview": overview(), "timeseries": timeseries(30)}


@app.get("/admin/api/status")
async def admin_status(request: Request):
    require_admin(request)
    return {"readiness": await readiness(), "ppu": read_ppu(), "checked_at": now_iso()}


@app.get("/admin/api/keys")
async def admin_keys(request: Request):
    require_admin(request)
    return {"keys": list_keys()}


@app.post("/admin/api/keys")
async def admin_create_key(request: Request):
    require_admin(request)
    body = await json_body(request)
    values = validate_key_payload(body, create=True)
    key_id = "key_" + secrets.token_hex(8)
    raw, prefix = new_raw_key(values["scope"])
    with DB_LOCK, connect_db() as conn:
        conn.execute(
            """INSERT INTO api_keys(id,name,key_hash,key_prefix,model,quota_tokens,used_tokens,
            reserved_tokens,rpm_limit,concurrent_limit,enabled,deleted,scope,created_at)
            VALUES(?,?,?,?,?,?,0,0,?,?,1,0,?,?)""",
            (key_id, values["name"], sha256_text(raw), prefix, values["model"],
             values["quota_tokens"], values["rpm_limit"], values["concurrent_limit"],
             values["scope"], now_iso()),
        )
    return {"id": key_id, "key": raw, "key_prefix": prefix, "name": values["name"],
            "model": values["model"], "quota_tokens": values["quota_tokens"],
            "rpm_limit": values["rpm_limit"], "concurrent_limit": values["concurrent_limit"],
            "scope": values["scope"], "show_once": True,
            "warning": "完整 Bearer Token 仅本次响应返回；数据库只存 SHA-256 与前缀。"}


@app.patch("/admin/api/keys/{key_id}")
async def admin_update_key(key_id: str, request: Request):
    require_admin(request)
    body = await json_body(request)
    updates = validate_key_payload(body, create=False)
    if not updates:
        raise HTTPException(status_code=400, detail="no_updates")
    sets = ",".join(f"{k}=?" for k in updates)
    with DB_LOCK, connect_db() as conn:
        cur = conn.execute(f"UPDATE api_keys SET {sets} WHERE id=? AND deleted=0",
                           (*updates.values(), key_id))
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="key_not_found")
    if "concurrent_limit" in updates and KEY_LIMITERS is not None:
        await KEY_LIMITERS.set_limit(key_id, updates["concurrent_limit"])
    return {"ok": True, "id": key_id, "updated": updates}


@app.delete("/admin/api/keys/{key_id}")
async def admin_delete_key(key_id: str, request: Request):
    require_admin(request)
    with DB_LOCK, connect_db() as conn:
        cur = conn.execute(
            "UPDATE api_keys SET enabled=0, deleted=1 WHERE id=? AND deleted=0", (key_id,)
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="key_not_found")
    return {"ok": True, "deleted": key_id, "soft_delete": True,
            "note": "历史请求记录保留 consumer_id/consumer_name，不产生孤儿记录。"}


@app.post("/admin/api/keys/{key_id}/rotate")
async def admin_rotate_key(key_id: str, request: Request):
    require_admin(request)
    with DB_LOCK, connect_db() as conn:
        row = conn.execute("SELECT scope FROM api_keys WHERE id=? AND deleted=0", (key_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="key_not_found")
        raw, prefix = new_raw_key(row["scope"])
        conn.execute(
            "UPDATE api_keys SET key_hash=?, key_prefix=?, rotated_at=? WHERE id=?",
            (sha256_text(raw), prefix, now_iso(), key_id),
        )
    return {"id": key_id, "key": raw, "key_prefix": prefix, "show_once": True,
            "note": "旧 Bearer Token 立即失效；请通过安全通道分发新值。"}


@app.get("/admin/api/requests")
async def admin_requests(request: Request, limit: int = 50):
    require_admin(request)
    return {"requests": recent_requests(max(1, min(limit, 200)))}


@app.get("/admin/api/errors")
async def admin_errors(request: Request, limit: int = 50):
    require_admin(request)
    return {"errors": recent_errors(max(1, min(limit, 200)))}


@app.get("/admin/api/concurrency")
async def admin_concurrency(request: Request):
    require_admin(request)
    return await concurrency_stats()


@app.get("/admin/api/stats-consistency")
async def admin_stats_consistency(request: Request):
    require_admin(request)
    with DB_LOCK, connect_db() as conn:
        req_billed = conn.execute("SELECT COALESCE(SUM(billed_tokens),0) v FROM requests").fetchone()["v"]
        req_actual = conn.execute("SELECT COALESCE(SUM(upstream_total_tokens),0) v FROM requests").fetchone()["v"]
        key_used = conn.execute("SELECT COALESCE(SUM(used_tokens),0) v FROM api_keys").fetchone()["v"]
        per_key = [dict(r) for r in conn.execute(
            """SELECT k.id, k.used_tokens, COALESCE(SUM(r.billed_tokens),0) billed
               FROM api_keys k LEFT JOIN requests r ON r.consumer_id=k.id
               GROUP BY k.id, k.used_tokens""").fetchall()]
        active_reservations = conn.execute(
            "SELECT COUNT(*) c FROM quota_reservations WHERE status IN ('reserved','unlimited')"
        ).fetchone()["c"]
    ov = overview()
    ok = (req_billed == key_used == ov["billed_tokens"]
          and req_actual == ov["total_tokens"]
          and all(int(r["used_tokens"]) == int(r["billed"]) for r in per_key))
    return {"ok": bool(ok), "requests_billed_tokens": req_billed,
            "requests_upstream_total_tokens": req_actual, "keys_used_tokens": key_used,
            "overview_billed_tokens": ov["billed_tokens"], "overview_total_tokens": ov["total_tokens"],
            "active_reservations": active_reservations, "per_key": per_key}


@app.get("/admin/api/persistence")
async def admin_persistence(request: Request):
    require_admin(request)
    encrypted = sorted(BACKUP_DIR.glob("gateway-*.db.enc")) if BACKUP_DIR.exists() else []
    legacy = sorted(LEGACY_BACKUP_DIR.glob("gateway-*.db")) if LEGACY_BACKUP_DIR.exists() else []
    latest = encrypted[-1] if encrypted else None
    last_backup = STATE["last_backup"]
    if last_backup is None and latest is not None:
        last_backup = {"path": str(latest),
                       "at": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(latest.stat().st_mtime)),
                       "size": latest.stat().st_size, "encrypted": True}
    passphrase, source = backup_passphrase()
    return {
        "db_path": str(DB_PATH),
        "db_exists": DB_PATH.exists(),
        "db_size": DB_PATH.stat().st_size if DB_PATH.exists() else 0,
        "db_mode": mode_of(DB_PATH),
        "data_dir_mode": mode_of(DB_PATH.parent),
        "config_dir_mode": mode_of(CONFIG_DIR),
        "backup_dir": str(BACKUP_DIR),
        "backup_dir_mode": mode_of(BACKUP_DIR),
        "backup_encryption": "aes-256-cbc+pbkdf2",
        "backup_passphrase_source": source,
        "passphrase_configured": bool(passphrase),
        "backup_count": len(encrypted),
        "legacy_plaintext_backup_count": len(legacy),
        "latest_backup": str(latest) if latest else None,
        "last_backup": last_backup,
        "last_backup_error": STATE["last_backup_error"],
        "restored_from": STATE["restored_from"],
        "last_restore_error": STATE["last_restore_error"],
        "backup_interval_seconds": BACKUP_INTERVAL,
        "loss_window_seconds": BACKUP_INTERVAL,
        "ossfs_chmod_is_not_security_control": True,
    }


@app.post("/admin/api/backup")
async def admin_backup(request: Request):
    require_admin(request)
    return await asyncio.to_thread(do_backup)
