"""Short-lived, owner-bound handoff to the local interactive login helper."""
import asyncio
import secrets
import time
from urllib.parse import urlencode

from fastapi import HTTPException, Request
from admin.server import COOKIE, digest


class MonkeyLogin:
    def __init__(self, store, importer):
        self.store, self.importer, self.flows = store, importer, {}

    def live(self, fid):
        flow = self.flows.get(fid)
        if not flow or flow["expires"] < time.time():
            raise HTTPException(404, "登录已过期，请重新打开登录窗口")
        with self.store.lock:
            session = self.store.sessions.get(flow["owner"])
            if not session or session["expires"] < time.time():
                raise HTTPException(401, "控制台已退出登录，请重新开始")
        return flow

    def owner(self, fid, req):
        self.store.require_admin(req)
        flow = self.live(fid)
        if flow["owner"] != digest(req.cookies.get(COOKIE, "")):
            raise HTTPException(404, "登录会话不存在")
        return flow

    def helper(self, fid, req):
        flow = self.live(fid)
        token = req.headers.get("authorization", "").removeprefix("Bearer ")
        if not token or not secrets.compare_digest(digest(token), flow["token"]):
            raise HTTPException(403, "登录凭据无效")
        return flow

    def start(self, req, name):
        self.store.require_admin(req)
        if not isinstance(name, str) or len(name) > 60:
            raise HTTPException(400, "备注不能超过 60 字符")
        host = req.url.hostname
        if host not in {"localhost", "127.0.0.1"} or req.url.scheme != "http" or req.url.port not in {8080, 8787, 7864, 18080}:
            raise HTTPException(400, "本机助手请通过 http://localhost:8080/admin/ 使用")
        owner = digest(req.cookies.get(COOKIE, ""))
        for fid, flow in list(self.flows.items()):
            if flow["expires"] < time.time() or (flow["owner"] == owner and flow["state"] != "validating"):
                self.flows.pop(fid, None)
        if any(f["owner"] == owner and f["state"] == "validating" for f in self.flows.values()):
            raise HTTPException(409, "账号正在保存，请稍候")
        if len(self.flows) >= 16:
            raise HTTPException(429, "登录窗口过多，请稍后重试")
        fid, token = secrets.token_urlsafe(24), secrets.token_urlsafe(32)
        self.flows[fid] = {"owner": owner, "token": digest(token), "expires": time.time()+600,
                           "state": "pending", "name": name.strip(), "lock": asyncio.Lock()}
        return {"id": fid, "expires_in": 600, "launch_url": "unified2api-login://monkeycode?" + urlencode({
            "base": str(req.base_url).rstrip("/"), "id": fid, "token": token})}

    def register(self, app, body_json):
        @app.post("/admin/api/unified/monkeycode/login")
        async def start(req: Request):
            self.store.require_admin(req)
            return self.start(req, (await body_json(req)).get("name", ""))

        @app.api_route("/admin/api/unified/monkeycode/login/{fid}", methods=["GET", "DELETE"])
        async def status(fid: str, req: Request):
            flow = self.owner(fid, req)
            if req.method == "DELETE":
                if flow["state"] == "validating":
                    raise HTTPException(409, "账号正在保存，请稍候")
                self.flows.pop(fid, None)
                return {"ok": True}
            return {"state": flow["state"], "message": flow.get("message", "")}

        @app.api_route("/admin/api/unified/monkeycode/login/{fid}/helper", methods=["GET", "POST"])
        async def helper(fid: str, req: Request):
            flow = self.helper(fid, req)
            if req.method == "POST":
                if flow["state"] != "pending":
                    raise HTTPException(409, "该登录窗口已被打开")
                flow["state"] = "waiting"
            return {"state": flow["state"], "expires_in": max(0, int(flow["expires"] - time.time()))}

        @app.post("/admin/api/unified/monkeycode/login/{fid}/complete")
        async def complete(fid: str, req: Request):
            flow = self.helper(fid, req)
            if flow["state"] != "waiting":
                raise HTTPException(409, "此登录会话已结束或正在保存")
            body = await body_json(req)
            cookie = body.get("cookie")
            if not isinstance(cookie, str) or not cookie or len(cookie) > 32768 or any(c in cookie for c in "\r\n"):
                raise HTTPException(400, "登录信息无效")
            async with flow["lock"]:
                self.live(fid)
                if flow["state"] != "waiting":
                    raise HTTPException(409, "登录信息已提交")
                flow["state"] = "validating"
                try:
                    await self.importer("POST", "/admin/accounts", {"cookie": cookie, "name": flow["name"]})
                except HTTPException:
                    flow["state"] = "failed"
                    flow["message"] = "登录校验或保存失败，请重新打开窗口登录"
                    raise HTTPException(502, flow["message"])
                except Exception:
                    flow["state"] = "failed"
                    flow["message"] = "登录保存失败，请重试"
                    raise HTTPException(502, flow["message"])
                flow["state"] = "success"
                return {"ok": True}

        @app.post("/admin/api/unified/monkeycode/login/{fid}/cancel")
        async def cancel(fid: str, req: Request):
            flow = self.helper(fid, req)
            if flow["state"] in {"pending", "waiting"}:
                flow["state"] = "cancelled"
                flow["message"] = "登录窗口已关闭，可重新打开"
            return {"ok": True}
