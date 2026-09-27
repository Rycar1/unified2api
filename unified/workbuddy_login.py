"""Owner-bound WorkBuddy international OAuth backed by the private hub."""
import asyncio
import secrets
import time
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException, Request
from admin.server import COOKIE, digest
from unified.errors import safe_error_message


class WorkBuddyLogin:
    def __init__(self, store, connections, api_key, admin_key,
                 hub_url="http://workbuddy-hub:8788", client_factory=None):
        self.store, self.connections = store, connections
        self.api_key, self.admin_key = api_key, admin_key
        self.hub_url = hub_url.rstrip("/")
        self.client_factory = client_factory or (lambda: httpx.AsyncClient(
            timeout=httpx.Timeout(40, connect=5), follow_redirects=False, trust_env=False))
        self.panel_token = None
        self.panel_lock = asyncio.Lock()
        self.flows = {}

    async def _panel_login(self):
        async with self.panel_lock:
            if self.panel_token:
                return self.panel_token
            try:
                async with self.client_factory() as client:
                    response = await client.post(self.hub_url + "/panel/login",
                                                 json={"password": self.admin_key})
                if response.status_code != 200:
                    raise HTTPException(502, "WorkBuddy 账号池管理认证失败")
                token = response.json().get("token")
                if not isinstance(token, str) or not token:
                    raise ValueError("missing panel token")
                self.panel_token = token
                return token
            except (httpx.HTTPError, ValueError):
                raise HTTPException(503, "WorkBuddy 账号池不可用") from None

    async def hub(self, method, path, *, body=None):
        for attempt in range(2):
            token = await self._panel_login()
            try:
                async with self.client_factory() as client:
                    response = await client.request(method, self.hub_url + path,
                        json=body, headers={"Authorization": "Bearer " + self.api_key,
                                            "X-Panel-Token": token})
                if response.status_code == 401 and attempt == 0:
                    self.panel_token = None
                    continue
                data = response.json()
                if not isinstance(data, dict):
                    raise ValueError("invalid hub response")
                if response.status_code >= 400:
                    raw = str(data.get("error") or data.get("message") or "WorkBuddy 账号池请求失败")
                    raise HTTPException(502, safe_error_message(raw, response.status_code,
                                         (self.api_key, self.admin_key, token))[:300])
                return data
            except (httpx.HTTPError, ValueError):
                raise HTTPException(503, "无法连接 WorkBuddy 账号池") from None
        raise HTTPException(502, "WorkBuddy 账号池管理认证失败")

    def owner(self, fid, req):
        self.store.require_admin(req)
        flow = self.flows.get(fid)
        if not flow or flow["owner"] != digest(req.cookies.get(COOKIE, "")):
            raise HTTPException(404, "登录会话不存在")
        return flow

    async def save_hub(self, name):
        existing = next((row for row in self.connections.rows()
                         if row.get("kind") == "workbuddy_intl" and row.get("adapter") == "hub"), None)
        if not existing:
            existing = next((row for row in self.connections.rows()
                             if row.get("kind") == "workbuddy_intl"), None)
        cid = existing["id"] if existing else next(
            (candidate for candidate in ["workbuddy", *(f"workbuddy{n}" for n in range(2, 100))]
             if candidate not in self.connections.data), None)
        if not cid:
            raise HTTPException(409, "WorkBuddy 服务标识已用尽")
        body = {"id": cid, "name": name or "WorkBuddy AI 国外", "kind": "workbuddy_intl",
                "adapter": "hub", "base_url": self.hub_url + "/v1", "key": self.api_key,
                "models": [], "enabled": True}
        body["models"] = (await self.connections.discover(body))["models"]
        self.connections.save(body, cid if existing else None)
        return {"state": "success", "message": "WorkBuddy AI 国外账号已添加"}

    async def start(self, req, name):
        self.store.require_admin(req)
        if not isinstance(name, str) or len(name) > 60:
            raise HTTPException(400, "账号备注不能超过 60 个字符")
        for fid, flow in list(self.flows.items()):
            if flow["expires"] < time.monotonic():
                self.flows.pop(fid, None)
        result = await self.hub("POST", "/accounts/login/start",
                                body={"realm": "intl", "platform": "CLI"})
        state, auth_url = result.get("state"), result.get("authUrl")
        if not isinstance(state, str) or not state or not isinstance(auth_url, str):
            raise HTTPException(502, "WorkBuddy 返回了无效的登录信息")
        url = urlsplit(auth_url)
        if url.scheme != "https" or url.hostname != "www.workbuddy.ai" or url.path != "/login" or url.username or url.password:
            raise HTTPException(502, "WorkBuddy 返回了无效的登录地址")
        fid = secrets.token_urlsafe(24)
        self.flows[fid] = {"owner": digest(req.cookies.get(COOKIE, "")), "hub_state": state,
                           "name": name.strip(), "expires": time.monotonic() + 300,
                           "state": "pending", "lock": asyncio.Lock()}
        return {"id": fid, "url": auth_url, "expires_in": 300}

    async def status(self, fid, req):
        flow = self.owner(fid, req)
        if flow["state"] != "pending":
            return {"state": flow["state"], "message": flow.get("message", "")}
        if flow["expires"] < time.monotonic():
            flow.update(state="expired", message="登录已过期")
            return {"state": "expired", "message": flow["message"]}
        async with flow["lock"]:
            if flow["state"] != "pending":
                return {"state": flow["state"], "message": flow.get("message", "")}
            result = await self.hub("GET", "/accounts/login/poll?state=" + flow["hub_state"])
            state = result.get("status")
            if state == "ok":
                try:
                    flow.update(await self.save_hub(flow["name"]))
                except (HTTPException, ValueError) as exc:
                    flow.update(state="failed", message=str(getattr(exc, "detail", exc))[:300])
            elif state in {"error", "expired", "unknown"}:
                flow.update(state="expired" if state in {"expired", "unknown"} else "failed",
                            message=str(result.get("message") or "授权失败")[:300])
            return {"state": flow["state"], "message": flow.get("message", "")}

    async def import_existing(self, req, name):
        self.store.require_admin(req)
        if not isinstance(name, str) or len(name) > 60:
            raise HTTPException(400, "账号备注不能超过 60 个字符")
        result = await self.hub("GET", "/accounts?realm=intl")
        if not result.get("accounts"):
            raise HTTPException(404, "WorkBuddy 账号池中没有国际站账号，请先网页登录")
        return await self.save_hub(name.strip())

    async def cancel(self, fid, req):
        flow = self.owner(fid, req)
        self.flows.pop(fid, None)
        try:
            await self.hub("POST", "/accounts/login/cancel", body={"state": flow["hub_state"]})
        except HTTPException:
            pass
        return {"ok": True}

    def register(self, app, body_json):
        @app.post("/admin/api/unified/workbuddy/login")
        async def start(req: Request):
            return await self.start(req, (await body_json(req)).get("name", ""))

        @app.post("/admin/api/unified/workbuddy/import")
        async def import_existing(req: Request):
            return await self.import_existing(req, (await body_json(req)).get("name", ""))

        @app.api_route("/admin/api/unified/workbuddy/login/{fid}", methods=["GET", "DELETE"])
        async def status(fid: str, req: Request):
            return await self.cancel(fid, req) if req.method == "DELETE" else await self.status(fid, req)
