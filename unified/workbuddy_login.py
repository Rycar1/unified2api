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

    async def accounts(self):
        result = await self.hub("GET", "/accounts?realm=intl")
        rows = result.get("accounts")
        if not isinstance(rows, list):
            raise HTTPException(502, "WorkBuddy 账号池返回了无效的账号列表")
        return [a for a in rows if isinstance(a, dict) and a.get("realm") == "intl"
                and isinstance(a.get("uid"), str) and a["uid"]]

    async def public_accounts(self):
        rows = []
        for account in await self.accounts():
            credits = account.get("credits")
            remaining = credits.get("remain") if isinstance(credits, dict) else None
            expiry = account.get("expiresAt")
            rows.append({"id": account["uid"], "uid": account["uid"], "provider": "workbuddy_intl",
                         "name": account.get("nickname") or account["uid"], "enabled": account.get("enabled", False),
                         "status": "paused" if not account.get("enabled") else "cooling" if account.get("inCooldown") else "ready",
                         "remaining": remaining if isinstance(remaining, (int, float)) else None,
                         "inflight": getattr(self,"active",{}).get(account["uid"],0),
                         "expires_at": expiry * 1000 if isinstance(expiry, (int, float)) and expiry < 100000000000 else expiry or 0,
                         "last_error": account.get("lastError") or ""})
        return rows

    async def models(self):
        if not any(a.get("enabled") for a in await self.accounts()):
            return []
        data = await self.hub("GET", "/v1/models?realm=intl")
        items = data.get("data")
        if not isinstance(items, list):
            raise HTTPException(502, "WorkBuddy 模型列表格式无效")
        return [{"id": "workbuddy/" + item["id"], "object": "model", "owned_by": "workbuddy",
                 "provider_name": "WorkBuddy AI 国外"}
                for item in items if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"]]

    async def relay(self, path, body, scope, receive, send):
        accounts = await self.accounts()
        if not any(a.get("enabled") for a in accounts):
            raise HTTPException(503, "WorkBuddy 国际站没有已启用账号")
        row = {"id": "workbuddy", "kind": "workbuddy_intl", "adapter": "hub",
               "base_url": self.hub_url + "/v1", "key": self.api_key, "enabled": True}
        from .diagnostics import CURRENT_TRACE, note_account
        trace = CURRENT_TRACE.get()
        if trace and trace.smart:
            if not hasattr(self,"active"):
                self.active = {}
            ready = [account for account in accounts if account.get("enabled") and not account.get("inCooldown")
                     and (account.get("credits") or {}).get("remain")!=0
                     and self.active.get(account["uid"],0)<trace.max_account_concurrency]
            ready.sort(key=lambda account:(self.active.get(account["uid"],0),-((account.get("credits") or {}).get("remain") or 0)))
            if not ready:
                raise HTTPException(503,"WorkBuddy 当前账号的额度或并发已达上限")
            for index,account in enumerate(ready):
                aid = account["uid"]
                if self.active.get(aid,0)>=trace.max_account_concurrency:
                    continue
                self.active[aid] = self.active.get(aid,0)+1
                note_account("workbuddy",aid,account.get("nickname",aid))
                try:
                    return await self.connections.relay_row({**row,"account_uid":aid},path,body,scope,receive,send)
                except HTTPException as exc:
                    if index==len(ready)-1 or exc.status_code in (400,422):
                        raise
                finally:
                    self.active[aid] = max(0,self.active.get(aid,0)-1)
            raise HTTPException(503,"WorkBuddy 账号池繁忙，请稍后重试")
        return await self.connections.relay_row(row, path, body, scope, receive, send)

    async def account(self, aid):
        account = next((a for a in await self.accounts() if a["uid"] == aid), None)
        if not account:
            raise HTTPException(404, "WorkBuddy 国际站账号不存在")
        return account

    async def operate(self, aid, action, account=None):
        account = account or await self.account(aid)
        if action == "checkin" and str(account.get("lastDailyChat") or "").startswith(time.strftime("%Y-%m-%d")):
            credits = account.get("credits")
            remaining = credits.get("remain") if isinstance(credits, dict) else None
            return {"ok": True, "id": aid, "message": "今日已完成每日活跃",
                    "remaining": remaining if isinstance(remaining, (int, float)) else None}
        endpoint = {"refresh": "/accounts/refresh", "balance": "/accounts/credits/fetch",
                    "checkin": "/accounts/daily-chat"}[action]
        data = await self.hub("POST", endpoint, body={"uid": aid})
        result = next((r for r in data.get("results", []) if r.get("uid") == aid), None)
        if not result:
            raise HTTPException(502, "WorkBuddy 未返回该账号的操作结果")
        if result.get("ok") is not True:
            raise HTTPException(502, safe_error_message(result.get("error") or result.get("msg") or "账号操作失败", 502))
        account = await self.account(aid)
        credits = account.get("credits")
        remaining = credits.get("remain") if isinstance(credits, dict) else None
        return {"ok": True, "id": aid, "message": result.get("msg") or "账号已更新",
                "remaining": remaining if isinstance(remaining, (int, float)) else None}

    async def daily_active_all(self):
        accounts = [account for account in await self.accounts() if account.get("enabled")]
        limit = asyncio.Semaphore(3)

        async def activate(account):
            async with limit:
                try:
                    return await self.operate(account["uid"], "checkin", account)
                except HTTPException as exc:
                    return {"id": account["uid"], "ok": False, "message": str(exc.detail)}

        return await asyncio.gather(*(activate(account) for account in accounts))

    async def edit_account(self, aid, req, body_json):
        self.store.require_admin(req)
        await self.account(aid)
        if req.method == "DELETE":
            result = await self.hub("POST", "/accounts/delete", body={"uid": aid})
            if not result.get("deleted"):
                raise HTTPException(502, "WorkBuddy 未删除该账号")
            return {"ok": True}
        body = await body_json(req)
        if type(body.get("enabled")) is not bool:
            raise HTTPException(400, "启用状态必须为布尔值")
        await self.hub("POST", "/accounts/set", body={"uid": aid, "enabled": body["enabled"]})
        return {"ok": True}

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
                flow.update(state="success", message="WorkBuddy AI 国外账号已添加")
            elif state in {"error", "expired", "unknown"}:
                flow.update(state="expired" if state in {"expired", "unknown"} else "failed",
                            message=str(result.get("message") or "授权失败")[:300])
            return {"state": flow["state"], "message": flow.get("message", "")}

    async def import_existing(self, req, name):
        self.store.require_admin(req)
        if not isinstance(name, str) or len(name) > 60:
            raise HTTPException(400, "账号备注不能超过 60 个字符")
        if not await self.accounts():
            raise HTTPException(404, "WorkBuddy 账号池中没有国际站账号，请先网页登录")
        return {"state": "success", "message": "WorkBuddy AI 国外账号已就绪"}

    async def cancel(self, fid, req):
        flow = self.owner(fid, req)
        self.flows.pop(fid, None)
        try:
            await self.hub("POST", "/accounts/login/cancel", body={"state": flow["hub_state"]})
        except HTTPException:
            pass
        return {"ok": True}

    def register(self, app, body_json):
        @app.api_route("/admin/api/unified/workbuddy_intl/accounts/{aid}", methods=["PATCH", "DELETE"])
        async def edit_account(aid: str, req: Request):
            return await self.edit_account(aid, req, body_json)

        @app.post("/admin/api/unified/workbuddy_intl/accounts/{aid}/{action}")
        async def operate(aid: str, action: str, req: Request):
            self.store.require_admin(req)
            if action not in {"refresh", "balance", "checkin"}:
                raise HTTPException(404, "不支持的账号操作")
            return await self.operate(aid, action)

        @app.post("/admin/api/unified/workbuddy/login")
        async def start(req: Request):
            return await self.start(req, (await body_json(req)).get("name", ""))

        @app.post("/admin/api/unified/workbuddy/import")
        async def import_existing(req: Request):
            return await self.import_existing(req, (await body_json(req)).get("name", ""))

        @app.api_route("/admin/api/unified/workbuddy/login/{fid}", methods=["GET", "DELETE"])
        async def status(fid: str, req: Request):
            return await self.cancel(fid, req) if req.method == "DELETE" else await self.status(fid, req)
