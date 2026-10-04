"""Unified management adapter for Qoder2API-Hub's regional account pools."""
import asyncio
import hashlib
import json
import re
import secrets
import time
from pathlib import Path
from urllib.parse import urlencode

from fastapi import HTTPException, Request
from admin.server import COOKIE, digest, write_json
from .qoder_hub import QoderHubTransport, error_for, realm
from .qoder import number, expiry, quota_balance
from .qoder_relay import QoderRelay
from .errors import safe_error_message


def model_aliases(rows):
    aliases = {row["key"]: re.sub(r"[^a-z0-9._-]+", "-", str(row.get("display_name") or row["key"]).lower()).strip("-") or row["key"]
               for row in rows if row.get("key")}
    counts = {}
    for alias in aliases.values():
        counts[alias] = counts.get(alias, 0) + 1
    keys = set(aliases)
    return {key: alias if counts[alias] == 1 and (alias == key or alias not in keys) else key
            for key, alias in aliases.items()}


class QoderAccounts(QoderRelay):
    def __init__(self, store, transport=None):
        super().__init__()
        self.store, self.transport = store, transport or QoderHubTransport()
        self.root = store.root / "qoder"
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.pool = self.transport.accounts.AccountPool(str(self.root / "hub-accounts"))
        self.pool.load()
        machine_path = self.root / "hub-machine.json"
        machines = json.loads(machine_path.read_text("utf-8")) if machine_path.exists() else {
            "cn": secrets.token_hex(16), "intl": secrets.token_hex(16)}
        if not machine_path.exists():
            write_json(machine_path, machines)
        self.pool._local_machine_id = lambda target: machines[target]
        self.flows, self.locks = {}, {}
        self.models_path = self.root / "hub-models.json"
        self.catalog = json.loads(self.models_path.read_text("utf-8")) if self.models_path.exists() else {}
        self.migrate()

    def migrate(self):
        old, marker = self.root / "accounts.json", self.root / "hub-migration.json"
        if not old.exists() or marker.exists():
            return
        rows = json.loads(old.read_text("utf-8"))
        write_json(self.root / "accounts.pre-hub.json", rows)
        count = 0
        for row in rows:
            value = row.get("credential", {})
            node = value.get("auth_user_info_raw", value)
            target = realm(node.get("region"))
            if self.pool.get(str(row["uid"]), target):
                continue
            account = self.transport.accounts.Account({"uid": str(row["uid"]), "unifiedId": row["id"],
                "nickname": row.get("name", ""), "realm": target,
                "accessToken": node.get("securityOauthToken") or node.get("security_oauth_token") or "",
                "refreshToken": node.get("refreshToken") or node.get("refresh_token") or "",
                "expiresAt": row.get("expires_at", 0), "enabled": row.get("enabled", True),
                "userType": node.get("userType", ""), "source": "unified-migration",
                "credits": {"remain": row.get("remaining"), "exceeded": row.get("quota_exceeded", False)}})
            self.pool.add(account)
            count += 1
        write_json(marker, {"version": 1, "migrated": count})
        old.unlink()

    def account_id(self, account):
        if not account.unified_id:
            key = account.uid if account.realm == "intl" else "cn:" + account.uid
            account.unified_id = hashlib.sha256(key.encode()).hexdigest()[:32]
        return account.unified_id

    def public(self, account):
        credits = account.credits or {}
        status = "ready"
        if account.cooldown_until > time.time():
            status = "cooling"
        if account.expires_at and account.expires_at <= time.time():
            status = "expired"
        if credits.get("exceeded") is True:
            status = "exhausted"
        if not account.enabled:
            status = "paused"
        return {"id": self.account_id(account), "uid": account.uid, "name": account.nickname,
            "provider": "qoder", "region": "cn" if account.realm == "cn" else "global",
            "enabled": bool(account.enabled), "expires_at": account.expires_at * 1000,
            "remaining": number(credits.get("remain")), "status": status,
            "last_error": str(error_for(account.last_error, account=account).detail) if account.last_error else ""}

    async def accounts(self):
        with self.pool._lock:
            return [self.public(a) for a in self.pool.accounts]

    def get(self, aid):
        with self.pool._lock:
            for account in self.pool.accounts:
                if self.account_id(account) == aid:
                    return account
        raise HTTPException(404, "Qoder 账号不存在")

    def save(self, account):
        with self.pool._lock:
            if account not in self.pool.accounts:
                raise HTTPException(409, "账号已删除，请重试")
            previous = Path(account.path) if account.path else None
            account.save(self.pool.dir)
            if previous and str(previous) != account.path and previous.parent.resolve() == Path(self.pool.dir).resolve():
                previous.unlink(missing_ok=True)

    def name(self, value):
        if not isinstance(value, str) or len(value.strip()) > 60:
            raise HTTPException(400, "备注不能超过 60 字符")
        return value.strip()

    async def import_account(self, body):
        value, target = body.get("credential"), realm(body.get("region"))
        name = self.name(body.get("name", ""))
        if isinstance(value, str):
            value = value.strip()
            if value.startswith("pt-"):
                return await self.import_pat(value, target, name)
            try:
                value = json.loads(value)
            except ValueError:
                raise HTTPException(400, "请输入 PAT（pt- 开头）或有效 JSON 凭据") from None
        if isinstance(value, list) and value:
            value = value[0]
        if not isinstance(value, dict):
            raise HTTPException(400, "凭据必须是 PAT 或 JSON 对象")
        value = value.get("user", value)
        if not isinstance(value, dict):
            raise HTTPException(400, "凭据格式无效")
        value = value.get("auth_user_info_raw", value)
        if not isinstance(value, dict):
            raise HTTPException(400, "凭据格式无效")
        target = realm(value.get("region") or value.get("realm") or body.get("region"))
        if value.get("personalToken") or value.get("personal_token"):
            return await self.import_pat(value.get("personalToken") or value["personal_token"], target, name)
        uid = value.get("uid") or value.get("userId") or value.get("id")
        token = value.get("accessToken") or value.get("securityOauthToken") or value.get("security_oauth_token") or value.get("token")
        if not isinstance(uid, str) or not uid.strip() or not isinstance(token, str) or not token.strip():
            raise HTTPException(400, "JSON 凭据需要 uid 和 accessToken，也可使用 PAT 或网页登录")
        account = self.transport.accounts.Account({"uid": uid, "accessToken": token,
            "refreshToken": value.get("refreshToken") or value.get("refresh_token") or "",
            "nickname": name or value.get("nickname") or value.get("name") or uid,
            "realm": target, "userType": value.get("userType", ""),
            "expiresAt": expiry(value.get("expiresAt") or value.get("expireTime")) / 1000,
            "source": "import"})
        return self.adopt(account)

    def adopt(self, account):
        previous = self.pool.get(account.uid, account.realm)
        if previous:
            account.unified_id = self.account_id(previous)
            account.enabled = previous.enabled
        self.account_id(account)
        self.pool.add(account)
        return self.public(account)

    async def import_pat(self, pat, target, name):
        if not isinstance(pat, str) or not pat.startswith("pt-"):
            raise HTTPException(400, "PAT 必须以 pt- 开头")
        account = self.transport.accounts.Account({"realm": target, "personalToken": pat, "source": "pat"})
        await self.transport.refresh(account, force=True)
        info = await self.transport.identify(account)
        account.nickname = name or info.get("name") or info.get("email") or account.uid
        result = self.adopt(account)
        try:
            await self.operate(result["id"], "refresh")
        except HTTPException:
            pass
        return result

    async def discover(self, account):
        rows = await self.transport.models(account)
        if rows:
            self.catalog[self.account_id(account)] = rows
            write_json(self.models_path, self.catalog)

    def model_rows(self, account):
        rows = self.catalog.get(self.account_id(account))
        return rows if rows is not None else self.transport.catalog.models_for_realm(account.realm)

    def legacy_model_ids(self):
        return {("qoder-cn/" if account.realm == "cn" else "qoder/") + row["key"]
                for account in list(self.pool.accounts) if account.enabled
                for row in self.model_rows(account) if row.get("key") and row.get("enable") is not False}

    async def models(self):
        result = {}
        for account in list(self.pool.accounts):
            if not account.enabled:
                continue
            rows = self.model_rows(account)
            cached = self.account_id(account) in self.catalog
            aliases = model_aliases(rows)
            prefix = "qoder-cn/" if account.realm == "cn" else "qoder/"
            for row in rows:
                key = row.get("key")
                if not key or row.get("enable") is False:
                    continue
                mid = prefix + aliases[key]
                result[mid] = {"id": mid, "object": "model",
                    "owned_by": "qoder_cn" if account.realm == "cn" else "qoder", "region": account.realm,
                    "vision": bool(row.get("is_vl")), "reasoning": bool(row.get("is_reasoning")),
                    "catalog_source": "live" if cached else "snapshot"}
        return list(result.values())

    async def operate(self, aid, action):
        async with self.locks.setdefault(aid, asyncio.Lock()):
            account = self.get(aid)
            await self.transport.refresh(account, force=action == "refresh")
            self.save(account)
            message, ok, earned_credit = "", True, None
            if action == "checkin":
                tasks = __import__("qoder_tasks")
                report = await asyncio.to_thread(tasks.run_checkin, account, only_daily=True)
                ok = report.get("ok", False)
                earned_credit = number(report.get("earned_credit"))
                message = report.get("campaign") or "；".join(report.get("logs", [])[-2:]) or "签到已执行"
            quota = await self.transport.request(account, "/api/v2/quota/usage")
            remaining = quota_balance(quota)
            account.credits = {"remain": remaining, "exceeded": quota.get("isQuotaExceeded") is True}
            account.clear_error()
            self.save(account)
            if action == "refresh":
                try:
                    await self.transport.identify(account)
                    self.save(account)
                    await self.discover(account)
                except HTTPException as exc:
                    message = "凭据和余额已更新；模型目录刷新失败：" + str(exc.detail)
            return {"id": aid, "ok": ok, "remaining": remaining, "status": self.public(account)["status"],
                **({"earned_credit": earned_credit} if action == "checkin" else {}),
                "message": safe_error_message(message or ("余额已更新" if remaining is not None else "上游未提供余额"),
                    200, secrets=(account.access_token, account.refresh_token, account.personal_token)).removeprefix("HTTP 200：")}

    async def refresh_all(self, action="refresh"):
        results = []
        for row in await self.accounts():
            if row["enabled"]:
                try:
                    results.append(await self.operate(row["id"], action))
                except HTTPException as exc:
                    results.append({"id": row["id"], "ok": False, "message": str(exc.detail)})
        return results

    async def maintain(self):
        await self.refresh_all()
        while True:
            await asyncio.sleep(60)
            for account in list(self.pool.accounts):
                if not account.enabled:
                    continue
                try:
                    async with self.locks.setdefault(self.account_id(account), asyncio.Lock()):
                        if await self.transport.refresh(account):
                            self.save(account)
                except HTTPException as exc:
                    account.last_error = str(exc.detail)

    async def chat_chunks(self, chat):
        target = realm(chat.get("qoder_region"))
        candidates = []
        for account in list(self.pool.accounts):
            if account.realm != target or not account.enabled or account.cooldown_until > time.time():
                continue
            rows = self.model_rows(account)
            aliases = model_aliases(rows)
            metadata = next((row for row in rows if row.get("key") == chat["model"]), None)
            if metadata is None:
                metadata = next((row for row in rows if aliases.get(row.get("key")) == chat["model"]), {})
            key = metadata.get("key") or chat["model"]
            if account.model_cooldowns.get(key, 0) <= time.time():
                candidates.append((account, key, metadata))
        if not candidates:
            raise HTTPException(503, "Qoder 当前站点没有可用账号，请登录或稍后重试")
        start = self.pool._cursor % len(candidates)
        self.pool._cursor += 1
        candidates = candidates[start:] + candidates[:start]
        for index, (account, key, metadata) in enumerate(candidates):
            emitted = False
            try:
                async with self.locks.setdefault(self.account_id(account), asyncio.Lock()):
                    if await self.transport.refresh(account):
                        self.save(account)
                async for chunk in self.transport.chunks(account, {**chat, "model": key}, metadata):
                    if any((c.get("delta") or {}).get(k) for c in chunk.get("choices", []) for k in ("content", "reasoning_content", "tool_calls")):
                        emitted = True
                    yield chunk
                return
            except HTTPException as exc:
                account.note_error(str(exc.detail), cooldown=60, model=key)
                if emitted or index == len(candidates) - 1 or exc.status_code in (400, 422):
                    raise error_for(exc.detail, exc.status_code, account) from None

    def owner_flow(self, fid, req):
        flow = self.flows.get(fid)
        if not flow or flow["owner"] != digest(req.cookies.get(COOKIE, "")) or flow["expires"] < time.monotonic():
            raise HTTPException(404, "登录会话不存在或已过期")
        return flow

    def register(self, app, body_json):
        @app.post("/admin/api/unified/qoder/accounts")
        async def add(req: Request):
            self.store.require_admin(req)
            return await self.import_account(await body_json(req))

        @app.api_route("/admin/api/unified/qoder/accounts/{aid}", methods=["PATCH", "DELETE"])
        async def edit(aid: str, req: Request):
            self.store.require_admin(req)
            body = await body_json(req) if req.method == "PATCH" else {}
            async with self.locks.setdefault(aid, asyncio.Lock()):
                account = self.get(aid)
                if req.method == "DELETE":
                    with self.pool._lock:
                        account.delete()
                        self.pool.accounts.remove(account)
                    self.catalog.pop(aid, None)
                    write_json(self.models_path, self.catalog)
                else:
                    if "enabled" in body:
                        if type(body["enabled"]) is not bool:
                            raise HTTPException(400, "启用状态必须是布尔值")
                        account.enabled = body["enabled"]
                        account.clear_error()
                    if "name" in body:
                        account.nickname = self.name(body["name"])
                    self.save(account)
            return {"ok": True}

        @app.post("/admin/api/unified/qoder/accounts/{aid}/{action}")
        async def operate(aid: str, action: str, req: Request):
            self.store.require_admin(req)
            if action not in ("refresh", "checkin"):
                raise HTTPException(404, "操作不存在")
            return await self.operate(aid, action)

        @app.post("/admin/api/unified/qoder/login")
        async def start(req: Request):
            self.store.require_admin(req)
            body = await body_json(req)
            owner, name = digest(req.cookies.get(COOKIE, "")), self.name(body.get("name", ""))
            for fid, flow in list(self.flows.items()):
                if flow["expires"] < time.monotonic() or flow["owner"] == owner:
                    self.pool.cancel_login(flow["hub_state"])
                    self.flows.pop(fid, None)
            if len(self.flows) >= 16:
                raise HTTPException(429, "登录会话过多，请稍后重试")
            started = self.pool.start_login(realm=realm(body.get("region")))
            fid = secrets.token_urlsafe(24)
            self.flows[fid] = {"owner": owner, "name": name, "hub_state": started["state"], "state": "pending",
                "expires": time.monotonic() + 600, "lock": asyncio.Lock()}
            return {"id": fid, "login_url": started["authUrl"], "state": "pending", "expires_in": 600}

        @app.api_route("/admin/api/unified/qoder/login/{fid}", methods=["GET", "DELETE"])
        async def poll(fid: str, req: Request):
            self.store.require_admin(req)
            flow = self.owner_flow(fid, req)
            if req.method == "DELETE":
                self.flows.pop(fid, None)
                self.pool.cancel_login(flow["hub_state"])
                return {"ok": True, "state": "cancelled"}
            async with flow["lock"]:
                if flow["state"] == "pending":
                    try:
                        info = self.pool.logins.get(flow["hub_state"])
                        if not info:
                            raise HTTPException(409, "登录已取消")
                        account = self.transport.accounts.Account({"realm": info["region"]})
                        query = urlencode({"nonce": info["nonce"], "verifier": info["verifier"], "challenge_method": "S256"})
                        try:
                            result = await self.transport.request(account, "/api/v1/deviceToken/poll?" + query)
                        except HTTPException as exc:
                            if exc.status_code in (202, 404):
                                return {"state": "pending"}
                            raise
                        token = result.get("token") or result.get("device_token")
                        if not token:
                            return {"state": "pending"}
                        account.access_token, account.refresh_token = token, result.get("refresh_token") or ""
                        account.expires_at = self.transport.accounts._device_expiry(result)
                        user = await self.transport.identify(account)
                        account.nickname = flow["name"] or user.get("name") or user.get("email") or account.uid
                        if self.flows.get(fid) is not flow or flow["expires"] < time.monotonic():
                            raise HTTPException(409, "登录已取消或过期")
                        row = self.adopt(account)
                        self.pool.cancel_login(flow["hub_state"])
                        flow.update(state="success", account_id=row["id"], message="Qoder 账号已添加")
                        try:
                            await self.operate(row["id"], "refresh")
                        except HTTPException:
                            pass
                    except HTTPException as exc:
                        flow.update(state="failed", message=str(exc.detail))
            return {k: flow[k] for k in ("state", "account_id", "message") if k in flow}

    async def close(self):
        for flow in self.flows.values():
            self.pool.cancel_login(flow["hub_state"])
        self.flows.clear()
        self.transport.sessions.clear()
