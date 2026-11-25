from __future__ import annotations
from dataclasses import dataclass
import hmac
import os
import secrets
import time
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel
from src.platform.authz import LOOPBACK_BIND_HOSTS, Principal, authorize_dataset, resolve_actor
from .contracts import GAS_DATASET_ID
from .service import GasFixtureService

SESSION_COOKIE="gas_bff_session"
TTL=1800

def _has_untrusted_proxy_headers(request: Request) -> bool:
    for name in request.headers:
        lowered = name.lower()
        if (lowered == "forwarded" or lowered.startswith("x-forwarded-")
                or lowered.startswith("x-real-") or lowered in {"via", "forwarded-for", "forwarded-host", "forwarded-proto"}):
            return True
    return False

def _invalid_query(field: str, reason: str) -> None:
    raise RequestValidationError([{"loc": ("query", field), "msg": reason, "type": "value_error"}])

class BFFSessionResponse(BaseModel):
    authenticated: bool=True
    mode: str="development"
    csrf_token: str

@dataclass
class _Session:
    principal: Principal
    csrf_token: str
    expires_at: float

class _Sessions:
    def __init__(self): self.items={}
    def create(self,p):
        now=time.time()
        self.items={k:v for k,v in self.items.items() if v.expires_at>now}
        if len(self.items)>=1000:
            oldest=min(self.items,key=lambda k:self.items[k].expires_at)
            self.items.pop(oldest,None)
        token=secrets.token_urlsafe(32); s=_Session(p,secrets.token_urlsafe(24),now+TTL); self.items[token]=s; return token,s
    def get(self,token):
        s=self.items.get(token or "")
        if s is None or s.expires_at<=time.time(): raise HTTPException(401,"browser session unavailable")
        return s
    def delete(self,token): self.items.pop(token or "",None)

def _principal():
    if os.getenv("GAS_BFF_MODE","").strip().lower()!="development": raise HTTPException(503,"BFF identity source unavailable")
    actor=os.getenv("GAS_BFF_ACTOR_ID","").strip()
    scope=os.getenv("GAS_BFF_ACTOR_SCOPE","internal").strip()
    if not os.getenv("GAS_BFF_BOOTSTRAP_TOKEN","").strip() or not actor: raise HTTPException(503,"BFF test identity unavailable")
    try: return resolve_actor(actor,scope)
    except HTTPException: raise
    except Exception: raise HTTPException(503,"BFF principal configuration unavailable") from None

def bff_router(service: GasFixtureService) -> APIRouter:
    router=APIRouter(prefix="/bff",tags=["gas-bff"]); sessions=_Sessions()
    def get_session(req: Request, *, refresh: bool = True):
        require_loopback(req)
        token = req.cookies.get(SESSION_COOKIE)
        try:
            session = sessions.get(token)
            if refresh:
                session.principal = resolve_actor(session.principal.actor_id, session.principal.scope)
        except HTTPException as exc:
            if token:
                sessions.delete(token)
                expiry = Response()
                expiry.delete_cookie(SESSION_COOKIE, path="/", httponly=True)
                headers = dict(exc.headers or {})
                headers["set-cookie"] = expiry.headers["set-cookie"]
                exc.headers = headers
            raise
        return session

    def require_loopback(req: Request) -> None:
        peer = req.client.host if req.client else None
        if peer not in LOOPBACK_BIND_HOSTS and peer != "testclient":
            raise HTTPException(401, "BFF仅允许本地访问")
        if _has_untrusted_proxy_headers(req):
            raise HTTPException(401, "BFF不接受客户端转发身份")

    @router.post("/session",response_model=BFFSessionResponse)
    def create(req: Request,res: Response):
        require_loopback(req)
        if os.getenv("GAS_BFF_MODE","").strip().lower()!="development": raise HTTPException(503,"BFF identity source unavailable")
        expected=os.getenv("GAS_BFF_BOOTSTRAP_TOKEN","").strip()
        if not expected or not hmac.compare_digest(req.headers.get("X-Gas-BFF-Bootstrap",""),expected): raise HTTPException(401,"BFF bootstrap credential invalid")
        p=_principal(); authorize_dataset(p,GAS_DATASET_ID,"synthetic")
        token,s=sessions.create(p)
        secure=os.getenv("GAS_BFF_SECURE_COOKIE","").strip().lower() in {"1","true","yes","on"} or os.getenv("WATER_RUNTIME_MODE","").strip().lower() in {"controlled","staging","production"}
        res.set_cookie(SESSION_COOKIE,token,max_age=TTL,httponly=True,secure=secure,samesite="lax",path="/")
        return BFFSessionResponse(csrf_token=s.csrf_token)
    @router.get("/session")
    def status(req: Request):
        s=get_session(req); return {"authenticated":True,"actor_id":s.principal.actor_id,"expires_at":s.expires_at}
    @router.delete("/session")
    def delete(req: Request,res: Response):
        require_loopback(req)
        s=get_session(req, refresh=False)
        if not hmac.compare_digest(req.headers.get("X-Gas-CSRF",""),s.csrf_token): raise HTTPException(403,"CSRF validation failed")
        sessions.delete(req.cookies.get(SESSION_COOKIE)); res.delete_cookie(SESSION_COOKIE,path="/"); return {"authenticated":False}
    @router.get("/gas/assets")
    def assets(req: Request,asset_type: str|None=None,limit: int=200,offset: int=0):
        s=get_session(req); authorize_dataset(s.principal,GAS_DATASET_ID,"synthetic")
        if limit<1 or limit>1000: _invalid_query("limit", "must be between 1 and 1000")
        if offset<0: _invalid_query("offset", "must be non-negative")
        return service.assets(asset_type=asset_type,limit=limit,offset=offset)
    @router.get("/gas/assets/{asset_id}")
    def asset(asset_id: str,req: Request):
        s=get_session(req); authorize_dataset(s.principal,GAS_DATASET_ID,"synthetic"); return service.asset(asset_id)
    @router.get("/gas/events")
    def events(req: Request,limit: int=100):
        s=get_session(req); authorize_dataset(s.principal,GAS_DATASET_ID,"synthetic")
        if limit<1 or limit>1000: _invalid_query("limit", "must be between 1 and 1000")
        return service.events(limit=limit)
    @router.get("/gas/events/{event_id}")
    def event(event_id: str,req: Request):
        s=get_session(req); authorize_dataset(s.principal,GAS_DATASET_ID,"synthetic"); return service.event(event_id)
    return router

__all__=["bff_router","SESSION_COOKIE"]
