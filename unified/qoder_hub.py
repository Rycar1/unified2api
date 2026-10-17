"""Async boundary around the pinned Qoder2API-Hub libraries."""
import asyncio
import importlib
import json
from pathlib import Path
import sys
import time
import urllib.parse

import httpx
from fastapi import HTTPException
from .errors import safe_error_message


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def load_hub():
    source = Path(__file__).resolve().parents[1] / "vendor" / "qoder2api-hub"
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))
    accounts = importlib.import_module("qoder_accounts")
    proxy = importlib.import_module("qoder_proxy")
    # Unified owns logging and usage. Never enable the upstream server or history store.
    proxy.log = lambda *args, **kwargs: None
    return accounts, proxy, importlib.import_module("qoder_sign"), importlib.import_module("qoder_catalog")


def realm(value):
    if value in ("cn", "intl", "global", None, ""):
        return "cn" if value == "cn" else "intl"
    raise HTTPException(400, "Qoder 站点必须是 cn 或 global")


def secrets_for(account):
    return {"access_token": account.access_token, "refresh_token": account.refresh_token,
            "personal_token": account.personal_token}


def error_for(message, status=502, account=None):
    secrets = secrets_for(account).values() if account is not None else ()
    clean = safe_error_message(message, status, secrets=secrets)
    return HTTPException(status, "Qoder：" + clean)


class QoderHubTransport:
    def __init__(self, transport=None):
        self.http_transport = transport
        self.accounts, self.proxy, self.sign, self.catalog = load_hub()
        self.sessions = {}

    def client(self, short=True):
        outbound = getattr(self, 'outbound', None)
        return httpx.AsyncClient(transport=self.http_transport, follow_redirects=False,
            **(outbound.options(transport=self.http_transport) if outbound else {'trust_env': False}),
            timeout=httpx.Timeout(20 if short else 120, connect=15))

    async def request(self, account, path, data=None):
        url = self.accounts.get_realm_config(account.realm)["openapi"] + path
        headers = account.headers()
        if not account.access_token:
            headers.pop("Authorization", None)
        try:
            async with self.client() as client:
                response = await client.request("GET" if data is None else "POST", url,
                    headers=headers, json=data)
            if response.status_code >= 400:
                raise error_for(response.text, response.status_code, account)
            if response.status_code == 202:
                return {}
            result = response.json()
            if not isinstance(result, dict):
                raise ValueError("expected object")
            return result
        except (ValueError, TypeError):
            raise error_for("上游返回无效 JSON", account=account) from None
        except httpx.HTTPError as exc:
            raise error_for(str(exc) or type(exc).__name__, 504 if isinstance(exc, httpx.TimeoutException) else 502, account) from None

    async def identify(self, account):
        info = await self.request(account, "/api/v1/userinfo")
        uid = info.get("id") or info.get("uid") or info.get("userId")
        if not isinstance(uid, (str, int)) or not str(uid):
            raise error_for("官方接口未返回账号身份", account=account)
        account.uid = str(uid)
        account.user_type = info.get("user_type") or info.get("userType") or account.user_type
        account.organization_id = info.get("organization_id") or account.organization_id
        account.organization_name = info.get("organization_name") or account.organization_name
        return info

    async def refresh(self, account, force=False):
        if not force and (not account.expires_at or account.expires_at > time.time() + 4 * 3600):
            return False
        attempts = []
        if account.refresh_token:
            path = "/api/v1/deviceToken/refresh" if account.refresh_token.startswith("drt-") else "/api/v1/jobToken/refresh"
            attempts.append((path, {"refresh_token": account.refresh_token}))
        if account.personal_token:
            attempts.append(("/api/v1/jobToken/exchange", {"personal_token": account.personal_token}))
        if not attempts:
            if account.expires_at and account.expires_at <= time.time():
                raise error_for("登录已过期，请重新授权", 401, account)
            return False
        last = None
        for path, body in attempts:
            try:
                result = await self.request(account, path, body)
                token = result.get("token") or result.get("device_token")
                if not isinstance(token, str) or not token:
                    raise error_for("刷新接口未返回凭据", account=account)
                account.access_token = token
                account.refresh_token = result.get("refresh_token") or account.refresh_token
                if "/deviceToken/" in path:
                    account.expires_at = self.accounts._device_expiry(result)
                else:
                    duration = result.get("expires_in")
                    account.expires_at = int(time.time() + float(duration) / 1000) if duration else int(time.time()) + 86400
                account.clear_error()
                return True
            except HTTPException as exc:
                last = exc
        raise last

    def session(self, account):
        key = (account.realm, account.uid)
        stamp = (account.access_token, account.refresh_token, account.user_type)
        cached = self.sessions.get(key)
        if cached and cached[0] == stamp:
            return cached[1]
        session = self.sign.CosySession(account.uid, account.nickname, account.access_token,
            account.refresh_token, account.user_type, account.organization_id, account.organization_name)
        if len(self.sessions) >= 128:
            self.sessions.pop(next(iter(self.sessions)))
        self.sessions[key] = (stamp, session)
        return session

    async def models(self, account):
        signed = self.sign.qoder_encode(b"{}")
        last = None
        for host in self.accounts.gateway_candidates(account.realm):
            url = host + self.proxy.MODELS_PATH
            try:
                async with self.client() as client:
                    response = await client.request("GET", url, content=signed,
                        headers=self.session(account).headers(signed, url, sse=False, accept="application/json"))
                if response.status_code >= 400:
                    raise error_for(response.text, response.status_code, account)
                result = response.json()
                rows = result.get("chat") or result.get("assistant") or result.get("developer")
                if not isinstance(rows, list):
                    raise ValueError("missing catalog")
                return [row for row in rows if isinstance(row, dict) and isinstance(row.get("key"), str)]
            except HTTPException as exc:
                last = exc
            except (ValueError, httpx.HTTPError) as exc:
                last = error_for(str(exc), account=account)
        raise last or error_for("未返回模型目录", account=account)

    async def chunks(self, account, chat, metadata):
        model = chat["model"]
        payload = dict(chat)
        # Reuse upstream custom-tool and role handling, then its official body builder.
        payload["tools"] = self.proxy._tools_for_chat(payload.get("tools") or [])
        self.proxy.normalize_tools(payload)
        body = self.proxy.build_qoder_body(payload, account, model, realm=account.realm)
        if not (chat.get("reasoning_effort") or (chat.get("reasoning") or {}).get("effort")):
            body.get("parameters", {}).pop("reasoning_effort", None)
        if metadata:
            mc = body["model_config"]
            for field in ("display_name", "is_vl", "is_reasoning", "max_input_tokens"):
                if metadata.get(field) is not None:
                    mc[field] = metadata[field]
            body["chat_context"]["extra"]["modelConfig"] = dict(mc)
        signed = self.sign.qoder_encode(compact(body).encode())
        last = None
        for host in self.accounts.gateway_candidates(account.realm):
            url = host + self.proxy.CHAT_PATH
            emitted, finished, saw_finish, saw_tools = False, False, False, False
            try:
                async with self.client(False) as client:
                    async with client.stream("POST", url, content=signed,
                        headers=self.session(account).headers(signed, url, model_key=model)) as response:
                        if response.status_code >= 400:
                            raw = (await response.aread())[:65536].decode("utf-8", "replace")
                            raise error_for(raw, response.status_code, account)
                        async for line in response.aiter_lines():
                            if len(line) > 4 * 1024 * 1024:
                                raise error_for("流式数据超过大小限制", account=account)
                            if line.startswith("event:finish"):
                                finished = True
                                continue
                            if not line.startswith("data:"):
                                continue
                            raw = self.proxy.strip_data_prefix(line)
                            if raw == "[DONE]":
                                finished = True
                                break
                            try:
                                outer = json.loads(raw)
                                status = outer.get("statusCodeValue")
                                inner = outer.get("body", outer)
                                if status not in (None, 200, "200"):
                                    raise error_for(inner, int(status), account)
                                if inner == "[DONE]":
                                    finished = True
                                    break
                                frame = json.loads(inner) if isinstance(inner, str) else inner
                                if not isinstance(frame, dict):
                                    continue
                                if frame.get("error") or frame.get("code") not in (None, 0, "0", "SUCCESS"):
                                    raise error_for(compact(frame), account=account)
                                cleaned = self.proxy.clean_chunk(compact(frame))
                                if not cleaned:
                                    continue
                                frame = json.loads(cleaned)
                                if not (frame.get("choices") or frame.get("usage")):
                                    continue
                                for choice in frame.get("choices") or []:
                                    delta = choice.get("delta") or {}
                                    if any(delta.get(field) for field in ("content", "reasoning_content", "tool_calls")):
                                        emitted = True
                                    if delta.get("tool_calls"):
                                        saw_tools = True
                                    if choice.get("finish_reason") is not None:
                                        finished = True
                                        saw_finish = True
                                yield frame
                            except (ValueError, TypeError, AttributeError):
                                raise error_for("流式数据格式无效", account=account) from None
                        if not finished:
                            raise error_for("上游流中断，未收到完成标记", account=account)
                        if not saw_finish:
                            yield {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls" if saw_tools else "stop"}]}
                return
            except HTTPException as exc:
                last = exc
                if emitted or exc.status_code not in (502, 503, 504):
                    raise
            except httpx.HTTPError as exc:
                last = error_for(str(exc) or type(exc).__name__, 504 if isinstance(exc, httpx.TimeoutException) else 502, account)
                if emitted:
                    raise last from None
        raise last
