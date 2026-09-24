import asyncio
import base64
import json
import os
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

import httpx

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from admin.server import create_app as create_buddy, AdminMiddleware, COOKIE, digest
from admin.metrics import MetricsMiddleware, RequestMetrics
from core import converter
from .trae import Trae
from .custom import Connections
from .monkey_login import MonkeyLogin
from .errors import safe_error_message
from .features import (AutomationManager, BackupManager, RequestLog,
                       RequestLogMiddleware, RouteManager, UnifiedConfig)

STATIC = Path(__file__).parent / "static"
PATHS = {"/v1/chat/completions", "/v1/responses", "/v1/messages"}


def response_failed(body):
    values = []
    try:
        values.append(json.loads(body))
    except (ValueError, UnicodeDecodeError):
        for line in body.splitlines():
            if line.startswith(b"data:") and line[5:].strip() != b"[DONE]":
                try:
                    values.append(json.loads(line[5:].strip()))
                except (ValueError, UnicodeDecodeError):
                    continue
    return any(isinstance(value, dict) and (
        value.get("error") or value.get("type") in {"error", "response.failed", "response.incomplete"}
        or isinstance(value.get("response"), dict) and value["response"].get("status") in {"failed", "incomplete"}
    ) for value in values)


async def body_json(req):
    try:
        data = await req.json()
    except ValueError:
        raise HTTPException(400, "请求必须是 JSON")
    if not isinstance(data, dict):
        raise HTTPException(400, "请求必须是 JSON 对象")
    return data


class API:
    def __init__(self, app, buddy, native, connections=None, routes=None):
        self.app, self.buddy, self.native = app, buddy, native
        self.connections = connections
        self.routes = routes

    async def models(self):
        code, result = await self.native.request("GET", "/v1/models")
        models = [{**m, "id": "trae/" + m["id"], "owned_by": "trae"}
                  for m in result.get("data", [])] if code == 200 else []
        models += [{"id": "codebuddy/" + m, "object": "model", "owned_by": "codebuddy"}
                   for m in converter.get_available_models()]
        unavailable = [] if code == 200 else ["trae"]
        code, result = await self.native.request("GET", "/monkey/v1/models")
        if code == 200:
            models += [{**m, "id": "monkeycode/" + m["id"], "owned_by": "monkeycode"} for m in (result.get("data") or [])]
        else:
            unavailable.append("monkeycode")
        if self.connections:
            models += self.connections.models()
        if self.routes:
            models += self.routes.public_models()
        return {"object": "list", "data": models, "unavailable_providers": unavailable}

    async def route_request(self, rid, scope, receive, send, data):
        route, targets = self.routes.candidates(rid)
        if not targets:
            raise HTTPException(503, "路由没有可用目标")
        for index, target in enumerate(targets):
            routed = dict(data)
            routed["model"] = target
            body = json.dumps(routed, ensure_ascii=False, allow_nan=False).encode()
            sent = False

            async def replay():
                nonlocal sent
                if not sent:
                    sent = True
                    return {"type": "http.request", "body": body, "more_body": False}
                return await receive()

            child_scope = dict(scope)
            child_scope["headers"] = [(k, v) for k, v in scope["headers"] if k.lower() != b"content-length"]
            child_scope["headers"].append((b"content-length", str(len(body)).encode()))
            child_scope["state"] = scope.setdefault("state", {})
            started = time.monotonic()
            if data.get("stream"):
                queue = asyncio.Queue(maxsize=16)

                async def capture(message):
                    await queue.put(message)

                async def run():
                    try:
                        await self.__call__(child_scope, replay, capture)
                    finally:
                        await queue.put(None)

                task = asyncio.create_task(run())
                pending = []
                status = 502
                failed_payload = False
                while True:
                    event = await queue.get()
                    if event is None:
                        break
                    pending.append(event)
                    if event["type"] == "http.response.start":
                        status = event["status"]
                        if not 200 <= status < 300:
                            break
                    elif event["type"] == "http.response.body" and event.get("body"):
                        first = event["body"][:65536]
                        failed_payload = response_failed(first)
                        break
                ok = 200 <= status < 300 and not failed_payload
                if ok or index == len(targets) - 1:
                    scope.setdefault("state", {})["resolved_model"] = target
                    try:
                        for event in pending:
                            await send(event)
                        while True:
                            event = await queue.get()
                            if event is None:
                                break
                            await send(event)
                        await task
                    finally:
                        self.routes.record(target, ok, status, (time.monotonic() - started) * 1000, route["cooldown_seconds"])
                    return
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                self.routes.record(target, False, status, (time.monotonic() - started) * 1000, route["cooldown_seconds"])
            else:
                events = []

                async def capture(message):
                    events.append(message)

                await self.__call__(child_scope, replay, capture)
                status = next((event["status"] for event in events if event["type"] == "http.response.start"), 502)
                response_body = b"".join(event.get("body", b"") for event in events if event["type"] == "http.response.body")
                failed_payload = response_failed(response_body[:65536])
                ok = 200 <= status < 300 and not failed_payload
                self.routes.record(target, ok, status, (time.monotonic() - started) * 1000, route["cooldown_seconds"])
                if ok or index == len(targets) - 1:
                    scope.setdefault("state", {})["resolved_model"] = target
                    for event in events:
                        await send(event)
                    return
        raise HTTPException(503, "所有路由目标均不可用")

    async def stream_native(self, scope, receive, send, body):
        started = False
        async def produce():
            nonlocal started
            async for item in self.native.events(scope["method"], scope["path"] +
                    ("?" + scope["query_string"].decode() if scope.get("query_string") else ""), body):
                if "status" in item:
                    headers = [(k.lower().encode(), v.encode()) for k, vs in item.get("headers", {}).items()
                               for v in vs if k.lower() not in {"connection", "transfer-encoding"}]
                    await send({"type": "http.response.start", "status": item["status"], "headers": headers})
                    started = True
                elif item.get("data"):
                    await send({"type": "http.response.body", "body": base64.b64decode(item["data"]), "more_body": True})
            if not started:
                await send({"type": "http.response.start", "status": 502, "headers": []})
            await send({"type": "http.response.body", "body": b"", "more_body": False})

        async def disconnected():
            while (await receive())["type"] != "http.disconnect":
                pass
        producer, watcher = asyncio.create_task(produce()), asyncio.create_task(disconnected())
        try:
            done, _ = await asyncio.wait([producer, watcher], return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        finally:
            for task in (producer, watcher):
                task.cancel()
            await asyncio.gather(producer, watcher, return_exceptions=True)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope["path"]
        if path == "/authorize" and scope["method"] == "GET":
            return await self.stream_native(scope, receive, send, b"")
        if not path.startswith("/v1/"):
            return await self.app(scope, receive, send)
        req = Request(scope, receive)
        try:
            self.buddy.state.store.check_api(req.headers.get("authorization"), req.headers.get("x-api-key"))
            if path == "/v1/models" and req.method == "GET":
                return await JSONResponse(await self.models())(scope, receive, send)
            if path not in PATHS or req.method != "POST":
                raise HTTPException(404, "接口不存在")
            chunks, size = [], 0
            async for chunk in req.stream():
                size += len(chunk)
                if size > 8 * 1024 * 1024:
                    raise HTTPException(413, "请求超过 8 MB")
                chunks.append(chunk)
            try:
                data = json.loads(b"".join(chunks))
            except (ValueError, RecursionError):
                raise HTTPException(400, "请求必须是 JSON")
            if not isinstance(data, dict) or not isinstance(data.get("model"), str):
                raise HTTPException(400, "请指定带平台前缀的模型")
            provider, sep, model = data["model"].partition("/")
            custom_ids = {r["id"] for r in self.connections.rows()} if self.connections else set()
            providers = {"trae", "codebuddy", "monkeycode"} | custom_ids
            if self.routes:
                providers.add("route")
            if not sep or provider not in providers or not model.strip():
                raise HTTPException(400, "请选择模型目录中带平台或服务前缀的模型")
            if provider == "route":
                return await self.route_request(model, scope, receive, send, data)
            if provider in {"trae", "monkeycode"} and path != "/v1/chat/completions":
                raise HTTPException(400, "此平台当前仅支持 Chat Completions")
            data["model"] = model
            try:
                body = json.dumps(data, ensure_ascii=False, allow_nan=False).encode()
            except (ValueError, RecursionError):
                raise HTTPException(400, "请求包含无效数字或嵌套过深")
            if provider == "trae":
                return await self.stream_native(scope, receive, send, body)
            if provider == "monkeycode":
                return await self.stream_native({**scope, "path": "/monkey" + path}, receive, send, body)
            if provider in custom_ids:
                return await self.connections.relay(provider, path, body, scope, receive, send)
            sent = False
            async def replay():
                nonlocal sent
                if not sent:
                    sent = True
                    return {"type": "http.request", "body": body, "more_body": False}
                return await receive()
            new_scope = dict(scope)
            new_scope["headers"] = [(k, v) for k, v in scope["headers"] if k.lower() not in (b"content-length", b"cookie")]
            new_scope["headers"].append((b"content-length", str(len(body)).encode()))
            return await self.buddy(new_scope, replay, send)
        except HTTPException as exc:
            return await JSONResponse({"error": {"message": exc.detail, "type": "invalid_request_error"}}, exc.status_code)(scope, receive, send)


def create_app(native=None, buddy=None):
    buddy = buddy or create_buddy(secure_cookie=os.environ.get("SECURE_COOKIE", "false").lower() == "true")
    native = native or Trae()
    store, pool = buddy.state.store, buddy.state.pool
    connections = Connections(store.root / "connections.json")
    metrics = RequestMetrics()
    login_owners = {}
    unified_config = UnifiedConfig(store)
    routes = RouteManager(unified_config)
    saved_console_settings = unified_config.get("console_settings", {})
    request_log = RequestLog(store.root / "request_logs.sqlite", saved_console_settings.get("retention_days", 365))
    request_log.set_retention_days(saved_console_settings.get("retention_days", 365))
    backup = BackupManager({
        "management": store.root,
        "codebuddy-auth": store.auth_dir,
        "trae-auth": Path(os.environ.get("TW2A_AUTH_DIR", "/app/auths")),
        "trae-data": Path(os.environ.get("TW2A_STATE_FILE", "/app/trae-data/state.json")).parent,
    })
    automation = None

    @asynccontextmanager
    async def lifespan(app):
        async with buddy.router.lifespan_context(buddy):
            automation_task = asyncio.create_task(automation.loop())
            try:
                yield
            finally:
                automation_task.cancel()
                await asyncio.gather(automation_task, return_exceptions=True)
                native.close()

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.add_middleware(AdminMiddleware)
    api = API(app, buddy, native, connections, routes)

    async def trae(method, path, obj=None):
        code, data = await native.request(method, path, json.dumps(obj).encode() if obj is not None else b"")
        if code >= 400:
            raise HTTPException(code, "TRAE 操作失败，请检查账号状态或重新授权")
        return data

    @app.get("/")
    @app.get("/admin")
    async def root():
        return RedirectResponse("/admin/")

    @app.get("/healthz")
    @app.get("/health")
    async def health():
        return {"status": "ok", "mode": "single-process", "providers": ["trae", "codebuddy", "monkeycode", "custom"]}

    @app.get("/admin/")
    async def home():
        return FileResponse(STATIC / "index.html")

    @app.get("/admin/assets/{filename}")
    async def asset(filename: str):
        if filename not in ("app.js", "style.css"):
            raise HTTPException(404)
        return FileResponse(STATIC / filename)

    @app.get("/admin/api/unified/overview")
    async def overview(req: Request):
        store.require_admin(req)
        data = await trae("GET", "/admin/api/accounts")
        rows = [{"id": a["uid"], "provider": "trae", "name": a.get("nickname") or a["uid"],
                 "uid": a["uid"], "enabled": a["enabled"], "expires_at": a.get("expires_at", 0) * (1 if a.get("expires_at", 0) > 100000000000 else 1000),
                 "status": "invalid" if a["disabled"] else "paused" if not a["enabled"] else "cooling" if a["cooling"] else "ready",
                 "remaining": a.get("remaining"), "last_error": a.get("reason", "")}
                for a in data["accounts"]]
        with store.lock:
            rows += [{**a, "provider": "codebuddy"} for a in pool.rows(store.account_rows())]
            keys = [{"id": kid, **{k: v for k, v in item.items() if k != "hash"}} for kid, item in store.data["keys"].items()]
            settings = dict(store.data["pool"])
        code, monkey = await native.request("GET", "/monkey/admin/accounts")
        if code == 200:
            rows += [{"id": a["uid"], "uid": a["uid"], "provider": "monkeycode", "name": a.get("nickname") or a["uid"],
                      "enabled": not a["disabled"], "status": "paused" if a["disabled"] else "cooling" if a["cooling"] else "ready",
                      "remaining": a["balance"] / 1000, "daily_tokens": a["daily_token_balance"], "last_error": a.get("reason", "")}
                     for a in monkey.get("accounts", [])]
        return {"accounts": rows, "connections": connections.rows(), "keys": keys, "pool": settings, "metrics": metrics.snapshot(),
                "models": (await api.models())["data"], "routes": routes.rows(), "automation": automation.settings(),
                "automation_history": unified_config.get("automation_history", [])[:10],
                "usage_summary": request_log.summary(), "mode": "single-process"}

    @app.post("/admin/api/unified/test")
    async def test_model(req: Request):
        store.require_admin(req)
        body = await body_json(req)
        if not isinstance(body.get("model"), str) or not isinstance(body.get("message"), str) or not 1 <= len(body["message"].strip()) <= 16000:
            raise HTTPException(400, "请选择模型并输入不超过 16000 字符的消息")
        key = secrets.token_urlsafe(32)
        error_secrets = [key]
        provider = body["model"].partition("/")[0]
        if provider in {item["id"] for item in connections.rows()}:
            error_secrets.append(connections.get(provider)["key"])
        with store.lock:
            store.test_keys.add(digest(key))
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=result), base_url="http://internal") as client:
                response = await asyncio.wait_for(client.post("/v1/chat/completions",
                    headers={"Authorization": "Bearer " + key}, json={"model": body["model"],
                    "messages": [{"role": "user", "content": body["message"]}], "max_tokens": 1024, "stream": False}), 150)
                try:
                    data = response.json()
                except ValueError:
                    data = response.text
                if response.status_code >= 400:
                    return {"ok": False, "error": safe_error_message(data, response.status_code, secrets=error_secrets), "status": response.status_code}
                if not isinstance(data, dict) or data.get("error"):
                    return {"ok": False, "error": safe_error_message(data, response.status_code, secrets=error_secrets), "status": response.status_code}
                choices = data.get("choices") or []
                message = choices[0].get("message") if isinstance(choices, list) and choices and isinstance(choices[0], dict) else None
                answer = message.get("content") if isinstance(message, dict) else None
                if not isinstance(answer, str):
                    answer = None
                return {"ok": bool(answer), "answer": answer, "error": None if answer else "未返回正文，请检查模型权限或生成预算。"}
        except asyncio.TimeoutError:
            return {"ok": False, "error": "调用超时（150 秒），请检查平台任务状态或稍后重试。"}
        except (ValueError, httpx.HTTPError) as exc:
            return {"ok": False, "error": safe_error_message({"detail": str(exc) or type(exc).__name__}, 502, secrets=error_secrets)}
        finally:
            with store.lock:
                store.test_keys.discard(digest(key))

    @app.post("/admin/api/unified/trae/accounts")
    async def import_trae(req: Request):
        store.require_admin(req)
        body = await body_json(req)
        doc = body.get("credential")
        if not isinstance(doc, dict):
            raise HTTPException(400, "请输入 JSON 凭据对象")
        auth_doc = doc.get("auth", doc)
        if isinstance(auth_doc, dict):
            expiry = auth_doc.get("expiresAt")
            if isinstance(expiry, (float, int)) and expiry > 100000000000:
                auth_doc["expiresAt"] = int(expiry / 1000)
        result = await trae("POST", "/admin/api/accounts/import", {"json": json.dumps(doc)})
        name = body.get("name")
        if isinstance(name, str) and name.strip():
            await trae("PATCH", "/admin/api/accounts/" + quote(result["uid"], safe=""), {"nickname": name.strip()[:60]})
        return result

    @app.api_route("/admin/api/unified/trae/accounts/{aid}", methods=["PATCH", "DELETE"])
    async def edit_trae(aid: str, req: Request):
        store.require_admin(req)
        obj = None
        if req.method == "PATCH":
            body = await body_json(req)
            obj = {}
            if "enabled" in body:
                if type(body["enabled"]) is not bool:
                    raise HTTPException(400, "启用状态必须为布尔值")
                obj["enabled"] = body["enabled"]
            if "name" in body:
                if not isinstance(body["name"], str) or not body["name"].strip():
                    raise HTTPException(400, "请输入账号备注")
                obj["nickname"] = body["name"].strip()[:60]
        return await trae(req.method, "/admin/api/accounts/" + quote(aid, safe=""), obj)

    @app.post("/admin/api/unified/trae/accounts/{aid}/refresh")
    async def refresh_trae(aid: str, req: Request):
        store.require_admin(req)
        return await trae("POST", "/admin/api/accounts/" + quote(aid, safe="") + "/refresh", {})

    @app.post("/admin/api/unified/trae/accounts/{aid}/checkin")
    async def checkin_trae(aid: str, req: Request):
        store.require_admin(req)
        return await trae("POST", "/admin/api/accounts/" + quote(aid, safe="") + "/checkin", {})

    @app.post("/admin/api/unified/trae/accounts/{aid}/balance")
    async def balance_trae(aid: str, req: Request):
        store.require_admin(req)
        return await trae("POST", "/admin/api/accounts/" + quote(aid, safe="") + "/balance", {})

    @app.get("/admin/api/unified/trae/credits")
    async def credits(req: Request):
        store.require_admin(req)
        return await trae("GET", "/admin/api/credits")

    async def run_maintenance(action):
        groups = []
        try:
            if action == "checkin":
                result = await trae("POST", "/admin/api/checkin", {})
                trae_results = result.get("results", [])
            else:
                accounts = await trae("GET", "/admin/api/accounts")
                trae_results = []
                for account in accounts.get("accounts", []):
                    if not account.get("enabled"):
                        continue
                    aid = quote(account["uid"], safe="")
                    try:
                        await trae("POST", "/admin/api/accounts/" + aid + "/refresh", {})
                        item = await trae("POST", "/admin/api/accounts/" + aid + "/balance", {})
                        trae_results.append({"id": account["uid"], "ok": True, "message": "状态和余额已更新", **item})
                    except HTTPException as exc:
                        trae_results.append({"id": account["uid"], "ok": False, "message": str(exc.detail)})
            groups.append({"provider": "trae", "results": trae_results})
        except HTTPException as exc:
            groups.append({"provider": "trae", "results": [{"ok": False, "message": str(exc.detail)}]})
        try:
            result = await pool.batch("checkin" if action == "checkin" else "status")
            groups.append({"provider": "codebuddy", "results": result.get("results", [])})
        except HTTPException as exc:
            groups.append({"provider": "codebuddy", "results": [{"ok": False, "message": str(exc.detail)}]})
        monkey_results = []
        code, monkey = await native.request("GET", "/monkey/admin/accounts")
        if code == 200:
            for account in monkey.get("accounts", []):
                if account.get("disabled"):
                    continue
                try:
                    suffix = "/checkin" if action == "checkin" else "/refresh"
                    item_code, item = await native.request("POST", "/monkey/admin/accounts/" + quote(account["uid"], safe="") + suffix)
                    monkey_results.append(item if item_code < 400 else {"id": account.get("uid"), "ok": False, "message": item.get("detail", "操作失败")})
                except (ValueError, RuntimeError):
                    monkey_results.append({"id": account.get("uid"), "ok": False, "message": "操作失败"})
        else:
            monkey_results.append({"ok": False, "message": "MonkeyCode 服务暂时不可用"})
        groups.append({"provider": "monkeycode", "results": monkey_results})
        all_results = [item for group in groups for item in group["results"]]
        return {"providers": groups, "summary": {
            "total": len(all_results),
            "succeeded": sum(1 for item in all_results if item.get("ok")),
            "failed": sum(1 for item in all_results if not item.get("ok")),
        }}

    @app.post("/admin/api/unified/checkin")
    async def checkin_all(req: Request):
        store.require_admin(req)
        return await run_maintenance("checkin")

    automation = AutomationManager(unified_config, run_maintenance)

    @app.post("/admin/api/unified/trae/login")
    async def login(req: Request):
        store.require_admin(req)
        owner = digest(req.cookies.get(COOKIE, ""))
        for fid, record in list(login_owners.items()):
            if record[1] < time.time() or record[0] == owner:
                await trae("POST", "/admin/api/login/cancel", {"pending_id": fid})
                login_owners.pop(fid, None)
        if len(login_owners) >= 16:
            raise HTTPException(429, "等待授权的请求过多")
        result = await trae("POST", "/admin/api/login", {"callback_port": "18080"})
        login_owners[result["pending_id"]] = (owner, time.time() + 600)
        return result

    @app.api_route("/admin/api/unified/trae/login/{fid}", methods=["GET", "DELETE"])
    async def login_status(fid: str, req: Request):
        store.require_admin(req)
        record = login_owners.get(fid)
        if not record or record[0] != digest(req.cookies.get(COOKIE, "")) or record[1] < time.time():
            raise HTTPException(404, "授权会话不存在或已过期")
        if req.method == "DELETE":
            result = await trae("POST", "/admin/api/login/cancel", {"pending_id": fid})
            login_owners.pop(fid, None)
            return result
        return await trae("GET", "/admin/api/login/result?pending_id=" + quote(fid, safe=""))

    @app.post("/admin/api/unified/monkeycode/accounts")
    async def import_monkey(req: Request):
        store.require_admin(req)
        return await monkey_request("POST", "/admin/accounts", await body_json(req))

    async def monkey_request(method, path, body=None):
        code, data = await native.request(method, "/monkey" + path, json.dumps(body).encode() if body is not None else b"")
        if code >= 400:
            raise HTTPException(code, data.get("detail", "MonkeyCode 操作失败"))
        return data

    @app.api_route("/admin/api/unified/monkeycode/accounts/{aid}", methods=["PATCH", "DELETE"])
    async def edit_monkey(aid: str, req: Request):
        store.require_admin(req)
        return await monkey_request(req.method, "/admin/accounts/" + quote(aid, safe=""),
                                    await body_json(req) if req.method == "PATCH" else None)

    @app.post("/admin/api/unified/monkeycode/accounts/{aid}/refresh")
    async def refresh_monkey(aid: str, req: Request):
        store.require_admin(req)
        return await monkey_request("POST", "/admin/accounts/" + quote(aid, safe="") + "/refresh")

    @app.post("/admin/api/unified/monkeycode/accounts/{aid}/checkin")
    async def checkin_monkey(aid: str, req: Request):
        store.require_admin(req)
        return await monkey_request("POST", "/admin/accounts/" + quote(aid, safe="") + "/checkin")

    @app.post("/admin/api/unified/monkeycode/accounts/{aid}/balance")
    async def balance_monkey(aid: str, req: Request):
        store.require_admin(req)
        await monkey_request("POST", "/admin/accounts/" + quote(aid, safe="") + "/refresh")
        accounts = await monkey_request("GET", "/admin/accounts")
        account = next((item for item in accounts.get("accounts", []) if item.get("uid") == aid), None)
        if not account:
            raise HTTPException(404, "账号不存在")
        return {"id": aid, "ok": True, "message": "余额已更新", "remaining": account.get("balance", 0) / 1000,
                "daily_tokens": account.get("daily_token_balance", 0)}

    @app.post("/admin/api/unified/accounts/{provider}/{aid}/balance")
    async def balance_account(provider: str, aid: str, req: Request):
        store.require_admin(req)
        if provider == "trae":
            return await balance_trae(aid, req)
        elif provider == "codebuddy":
            return await asyncio.to_thread(pool.operate, aid, "status")
        elif provider == "monkeycode":
            return await balance_monkey(aid, req)
        else:
            raise HTTPException(404, "不支持的平台")

    @app.post("/admin/api/unified/connections/discover")
    async def discover_connections(req: Request):
        store.require_admin(req)
        return await connections.discover(await body_json(req))

    @app.post("/admin/api/unified/connections")
    async def add_connection(req: Request):
        store.require_admin(req)
        return connections.save(await body_json(req))

    @app.api_route("/admin/api/unified/connections/{cid}", methods=["PATCH", "DELETE"])
    async def edit_connection(cid: str, req: Request):
        store.require_admin(req)
        return connections.delete(cid) if req.method == "DELETE" else connections.save(await body_json(req), cid)

    async def routable_models():
        return {item["id"] for item in (await api.models())["data"] if not item["id"].startswith("route/")}

    @app.get("/admin/api/unified/routes")
    async def list_routes(req: Request):
        store.require_admin(req)
        return {"routes": routes.rows()}

    @app.post("/admin/api/unified/routes")
    async def add_route(req: Request):
        store.require_admin(req)
        return routes.save(await body_json(req), await routable_models())

    @app.api_route("/admin/api/unified/routes/{rid}", methods=["PATCH", "DELETE"])
    async def edit_route(rid: str, req: Request):
        store.require_admin(req)
        if req.method == "DELETE":
            return routes.delete(rid)
        return routes.save(await body_json(req), await routable_models(), rid)

    @app.get("/admin/api/unified/logs")
    async def logs(req: Request):
        store.require_admin(req)
        query = req.query_params
        ok = None if query.get("ok") not in {"true", "false"} else query.get("ok") == "true"
        return {**request_log.query(query.get("limit", 100), query.get("offset", 0), query.get("model", ""), ok),
                "summary": request_log.summary(query.get("hours", 24))}

    @app.delete("/admin/api/unified/logs")
    async def clear_logs(req: Request):
        store.require_admin(req)
        request_log.clear()
        return {"ok": True}

    @app.get("/admin/api/unified/settings")
    async def console_settings(req: Request):
        store.require_admin(req)
        saved = unified_config.get("console_settings", {})
        return {"settings": {"retention_days": request_log.retention_days,
                             "auto_refresh_seconds": saved.get("auto_refresh_seconds", 0)}}

    @app.patch("/admin/api/unified/settings")
    async def update_console_settings(req: Request):
        store.require_admin(req)
        body = await body_json(req)
        current = unified_config.get("console_settings", {"retention_days": 365, "auto_refresh_seconds": 0})
        retention = body.get("retention_days", current.get("retention_days", 365))
        refresh = body.get("auto_refresh_seconds", current.get("auto_refresh_seconds", 0))
        if retention not in (30, 90, 180, 365):
            raise HTTPException(400, "统计保留天数只能选择 30、90、180 或 365 天")
        if refresh not in (0, 30, 60, 300):
            raise HTTPException(400, "自动刷新间隔只能选择关闭、30 秒、1 分钟或 5 分钟")
        settings = {"retention_days": retention, "auto_refresh_seconds": refresh}
        unified_config.set("console_settings", settings)
        request_log.set_retention_days(retention)
        return {"settings": settings}

    @app.get("/admin/api/unified/usage")
    async def usage(req: Request):
        store.require_admin(req)
        return request_log.usage()

    @app.get("/admin/api/unified/automation")
    async def automation_status(req: Request):
        store.require_admin(req)
        return {"settings": automation.settings(), "history": unified_config.get("automation_history", [])}

    @app.patch("/admin/api/unified/automation")
    async def update_automation(req: Request):
        store.require_admin(req)
        return {"settings": automation.update(await body_json(req))}

    @app.post("/admin/api/unified/automation/run/{action}")
    async def run_automation(action: str, req: Request):
        store.require_admin(req)
        return await automation.execute(action, manual=True)

    @app.post("/admin/api/unified/backup/export")
    async def export_backup(req: Request):
        store.require_admin(req)
        body = await body_json(req)
        blob = await asyncio.to_thread(backup.export, body.get("password"), bool(body.get("include_logs", False)))
        filename = "unified2api-" + time.strftime("%Y%m%d-%H%M%S") + ".ubak"
        return Response(blob, media_type="application/octet-stream",
                        headers={"Content-Disposition": f'attachment; filename="{filename}"'})

    @app.post("/admin/api/unified/backup/import")
    async def import_backup(req: Request):
        store.require_admin(req)
        body = await body_json(req)
        if not isinstance(body.get("data"), str):
            raise HTTPException(400, "请选择备份文件")
        result_data = await asyncio.to_thread(backup.restore, body["data"], body.get("password"))
        if os.environ.get("UNIFIED_ALLOW_RESTART", "false").lower() == "true":
            asyncio.get_running_loop().call_later(1.5, os._exit, 0)
            result_data["restarting"] = True
        return result_data

    monkey_login = MonkeyLogin(store, monkey_request)
    monkey_login.register(app, body_json)

    @app.get("/admin/downloads/monkey-login-helper.zip")
    async def login_helper_download(req: Request):
        store.require_admin(req)
        archive = Path(__file__).parent / "downloads" / "monkey-login-helper.zip"
        if not archive.exists():
            raise HTTPException(404, "登录助手安装包尚未构建")
        return FileResponse(archive, filename="monkey-login-helper.zip", media_type="application/zip")

    app.mount("/", buddy)
    result = RequestLogMiddleware(MetricsMiddleware(api, metrics=metrics), request_log)
    result.state = app.state
    result.state.buddy, result.state.native = buddy, native
    result.state.connections = connections
    result.state.monkey_login = monkey_login
    return result


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(create_app(), host="0.0.0.0", port=8080, access_log=False, proxy_headers=False)
