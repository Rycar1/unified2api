"""Persisted, admin-managed OpenAI-compatible connections."""
import asyncio
import copy
import json
import re
import threading
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
from admin.server import write_json
from unified.errors import safe_error_message
from .custom_pool import CustomPool


async def _bounded_body(response, limit=64 * 1024):
    content = bytearray()
    async for chunk in response.aiter_bytes():
        content.extend(chunk[:limit - len(content)])
        if len(content) >= limit:
            break
    return content.decode("utf-8", errors="replace")


class Connections(CustomPool):
    def __init__(self, path, transport=None):
        self.path, self.transport = path, transport
        self.lock = threading.RLock()
        self.data = json.loads(path.read_text("utf-8")) if path.exists() else {}
        self._migrate_workbuddy()
        self.init_pool()
        from .opencode import OpenCodeSupport
        self.opencode = OpenCodeSupport(path.parent)

    def _migrate_workbuddy(self):
        # Older releases stored the built-in international pool as a custom
        # connection. Its credentials remain in the hub volume.
        updated = {cid: row for cid, row in self.data.items()
                   if not (row.get("kind") == "workbuddy_intl" and row.get("adapter") == "hub")}
        legacy = updated.get("workbuddy")
        if legacy:
            cid = next((name for name in ("workbuddy_legacy", *(f"workbuddy_legacy{n}" for n in range(2, 100)))
                        if name not in updated), None)
            if cid:
                updated.pop("workbuddy")
                updated[cid] = {**legacy, "id": cid}
        if updated != self.data:
            write_json(self.path, updated)
            self.data = updated

    def client(self, kind="openai"):
        network = getattr(self, 'network', None)
        read_timeout = max(network.get().values()) if network else 900 if kind == 'workbuddy_intl' else 300
        outbound = getattr(self, 'outbound', None)
        return httpx.AsyncClient(transport=self.transport, timeout=httpx.Timeout(None, read=read_timeout, connect=20),
                                 follow_redirects=False, **(outbound.options(transport=self.transport) if outbound else {'trust_env': False}))

    def get(self, cid):
        with self.lock:
            if cid not in self.data:
                raise HTTPException(404, "自定义服务不存在")
            return copy.deepcopy(self.data[cid])

    def rows(self):
        with self.lock:
            return [{k: copy.deepcopy(v) for k, v in row.items() if k not in {"key", "balance_key", "accounts"}} |
                    {"has_key": bool(row["key"]), "has_balance_key": bool(row.get('balance_key')),
                     "accounts": [self.public_account(row['id'], a) for a in self.ensure_accounts(row)]}
                    for row in self.data.values()]

    def validate(self, body, old=None):
        row = dict(old or {})
        for field in ("id", "name", "base_url", "key", "models", "enabled", "kind", "adapter", "balance_scheme", "balance_key", "balance_user_id", "opencode_support"):
            if field in body:
                if field in {"key", "balance_key"} and old and body[field] == "":
                    continue
                row[field] = body[field]
        if row.get('kind') == 'opencode_runtime':
            row.update(base_url='http://127.0.0.1:8789/v1', key='managed-opencode-runtime',
                       adapter='relay', opencode_support=False, balance_scheme='none', balance_key='', balance_user_id='')
            row['accounts'] = [{'id':'primary','name':'内置运行时','key':'managed-opencode-runtime',
                                'enabled':True,'max_concurrency':2}]
        cid = row.get("id", "")
        if not isinstance(cid, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,39}", cid) or cid in {"trae", "codebuddy", "monkeycode", "workbuddy", "qoder", "qoder-cn", "antigravity"}:
            raise HTTPException(400, "服务标识须为小写英文开头的 1–40 位字母、数字、下划线或短横线，不能使用内置平台名称")
        for field in ("name", "base_url", "key"):
            if not isinstance(row.get(field), str) or not row[field].strip():
                raise HTTPException(400, "请填写名称、Base URL 和 API Key")
            row[field] = row[field].strip()
        if len(row["name"]) > 60 or len(row["key"]) > 8192 or any(c in row["key"] for c in "\r\n"):
            raise HTTPException(400, "名称或 Key 格式无效")
        try:
            url = urlsplit(row["base_url"])
            valid = url.scheme in {"http", "https"} and url.hostname and not url.username and not url.password and not url.query and not url.fragment
            _ = url.port
            if not valid or len(row["base_url"]) > 2048 or any(c.isspace() for c in row["base_url"]):
                raise ValueError()
        except ValueError:
            raise HTTPException(400, "Base URL 应为 http(s) 地址，不能包含凭据、查询参数或片段")
        # A root URL gets /v1; an explicit path (e.g. /openai/v1) is preserved.
        row["base_url"] = row["base_url"].rstrip("/") + ("/v1" if not url.path.strip("/") else "")
        models = row.get("models", [])
        if not isinstance(models, list) or len(models) > 2000 or any(not isinstance(m, str) or not m.strip() or len(m) > 256 or any(ord(c) < 32 for c in m) for m in models):
            raise HTTPException(400, "模型列表格式无效")
        row["models"] = list(dict.fromkeys(m.strip() for m in models))
        if row.get('kind') == 'opencode_runtime':
            row['models'] = list(dict.fromkeys(m.removeprefix('opencode/') for m in row['models']))
            if any('/' in m or not m for m in row['models']):
                raise HTTPException(400, '内置 OpenCode2API 仅接入 OpenCode 模型，请填写模型名或读取目录')
        row.setdefault("kind", "openai")
        if not isinstance(row["kind"], str) or row["kind"] not in {"openai", "workbuddy_intl", "opencode_runtime"}:
            raise HTTPException(400, "不支持的服务类型")
        row.setdefault("adapter", "relay")
        if row["adapter"] not in {"relay", "hub"} or (row["adapter"] == "hub" and row["kind"] != "workbuddy_intl"):
            raise HTTPException(400, "不支持的 WorkBuddy 接入方式")
        row.setdefault("enabled", True)
        if type(row["enabled"]) is not bool:
            raise HTTPException(400, "启用状态必须为布尔值")
        row.setdefault('opencode_support', False)
        if type(row['opencode_support']) is not bool:
            raise HTTPException(400, 'OpenCode 支持开关必须为布尔值')
        if row.get('balance_scheme', 'none') not in {'none', 'deepseek', 'newapi', 'sub2api'}:
            raise HTTPException(400, '不支持的余额查询方案')
        for field in ('balance_key', 'balance_user_id'):
            if not isinstance(row.get(field, ''), str) or len(row.get(field, '')) > 8192 or any(c in row.get(field, '') for c in '\r\n\0'):
                raise HTTPException(400, '余额查询凭据格式无效')
        self.ensure_accounts(row)
        if body.get('key') and row['kind'] != 'opencode_runtime':
            row['accounts'][0]['key'] = row['key']
            row['accounts'][0].pop('balance', None)
        if old and any(row.get(k) != old.get(k) for k in ('base_url', 'balance_scheme', 'balance_key', 'balance_user_id')):
            for account in row['accounts']:
                account.pop('balance', None)
        return row

    def save(self, body, cid=None):
        with self.lock:
            old = self.get(cid) if cid else None
            row = self.validate(body, old)
            if cid and row["id"] != cid:
                raise HTTPException(400, "已有服务的标识不能修改")
            if not cid and row["id"] in self.data:
                raise HTTPException(409, "服务标识已存在")
            updated = {**self.data, row["id"]: row}
            write_json(self.path, updated)
            self.data = updated
            return {"id": row["id"], "ok": True}

    def delete(self, cid):
        with self.lock:
            self.get(cid)
            updated = dict(self.data)
            del updated[cid]
            write_json(self.path, updated)
            self.data = updated
        return {"ok": True}

    async def discover(self, body):
        old = self.get(body['existing_id']) if body.get('existing_id') else None
        if self.validate(body, old)['kind'] == 'opencode_runtime':
            async with self.runtime.session():
                return await self._discover(body)
        return await self._discover(body)

    async def _discover(self, body):
        old = self.get(body["existing_id"]) if body.get("existing_id") else None
        row = self.validate(body, old)
        if row['kind'] == 'opencode_runtime':
            if getattr(self.runtime,'backend','runtime') == 'zen':
                self.runtime.zen.transport = self.transport
                return {'models':await self.runtime.zen.models()}
            row = {**row, 'key':self.runtime.token}
        try:
            async with self.relay_client(row["kind"]) as client:
                async with client.stream("GET", row["base_url"] + "/models", headers={"Authorization": "Bearer " + row["key"],
                                          **({"X-Realm": "intl"} if row.get("adapter") == "hub" else {})},
                                         timeout=120 if row['kind']=='opencode_runtime' else 30) as response:
                    if response.status_code != 200:
                        detail = await _bounded_body(response)
                        raise HTTPException(502, "模型列表读取失败：" + safe_error_message(detail, response.status_code, (row["key"], *self.runtime_secrets(row))))
                    raw = await _bounded_body(response, 1024 * 1024)
                    try:
                        data = json.loads(raw)
                        if not isinstance(data, dict) or not isinstance(data.get("data"), list):
                            raise ValueError("missing model list")
                    except (ValueError, TypeError):
                        raise HTTPException(502, "模型列表响应格式无效：" + safe_error_message(raw, response.status_code, (row["key"], *self.runtime_secrets(row)))) from None
                if row["kind"] == "workbuddy_intl" and row.get("adapter") != "hub":
                    # The relay may expose CN and international catalogues together.
                    # Never silently import the CN models for an international connection.
                    models = [m.get("site_model_id") if isinstance(m.get("site_model_id"), str)
                              else m["id"].removeprefix("intl/")
                              for m in data["data"] if isinstance(m, dict) and m.get("site") == "intl"
                              and isinstance(m.get("id"), str)]
                    if not models:
                        raise HTTPException(502, "中转未返回国际站模型；请检查 WorkBuddy AI 已登录，并将中转站点设为 intl 或 both")
                else:
                    models = [m["id"] for m in data["data"] if isinstance(m, dict) and isinstance(m.get("id"), str)]
                if row['kind'] == 'opencode_runtime':
                    models = [m.removeprefix('opencode/') for m in models if m.startswith('opencode/') or '/' not in m]
                row["models"] = models
                return {"models": self.validate(row)["models"]}
        except httpx.HTTPError as exc:
            raise HTTPException(502, "模型列表读取失败：" + safe_error_message(str(exc) or type(exc).__name__, 502, (row["key"], *self.runtime_secrets(row)))) from None
        except (ValueError, KeyError, TypeError):
            raise HTTPException(502, "无法读取模型列表，请检查地址和 Key，或手动填写模型名")

    def models(self):
        return [{"id": row["id"] + "/" + model, "object": "model", "owned_by": row["id"],
                 "provider_name": row["name"],
                 **({'integration':'opencode_runtime'} if row['kind']=='opencode_runtime' else {})}
                for row in self.rows() if row["enabled"] for model in row["models"]]

    def runtime_secrets(self, row):
        if row.get('kind') == 'opencode_runtime':
            return (getattr(self.runtime, 'official_key', ''), getattr(self.runtime, 'applied_key', ''))
        return ()

    def relay_client(self, kind):
        if kind == 'opencode_runtime':
            return httpx.AsyncClient(transport=self.transport, trust_env=False,
                timeout=httpx.Timeout(None, read=900, connect=10))
        return self.client(kind)

    async def relay(self, cid, path, body, scope, receive, send):
        row = self.get(cid)
        if (row.get('kind') == 'opencode_runtime' and row['enabled'] and
                path in {'/v1/chat/completions', '/v1/responses', '/v1/messages'}):
            async with self.runtime.session():
                return await self.pooled_relay(cid, path, body, scope, receive, send)
        return await self.pooled_relay(cid, path, body, scope, receive, send)

    async def relay_row(self, row, path, body, scope, receive, send):
        if not row["enabled"]:
            raise HTTPException(503, "自定义服务已暂停")
        accepted={"/v1/chat/completions", "/v1/responses", "/v1/responses/compact"}
        if row.get('kind')=='opencode_runtime':
            if path=='/v1/responses/compact':
                raise HTTPException(400, 'OpenCode2API 提供 Responses 格式转换，不支持原生远程压缩')
            accepted.add('/v1/messages')
        if row.get('adapter')=='hub': accepted.add('/v1/messages')
        if path not in accepted:
            raise HTTPException(400, "自定义服务支持 Chat Completions / Responses，具体以该服务能力为准")
        if row.get("kind") == "workbuddy_intl" and row.get("adapter") != "hub" and path != "/v1/chat/completions":
            raise HTTPException(400, "WorkBuddy AI 中转仅支持 Chat Completions")
        started = False
        extra, session_context = self.opencode.prepare(row['id'], scope, body) if row.get('opencode_support') else ({}, None)
        if row.get('kind')=='opencode_runtime':
            if getattr(self.runtime,'backend','runtime') == 'zen':
                self.runtime.zen.transport = self.transport
                return await self.runtime.zen.relay(row['id'],path,body,scope,receive,send)
            row = {**row, 'base_url':self.runtime.base_url, 'key':self.runtime.token}
        try:
            async with self.relay_client(row.get("kind", "openai")) as client:
                async with client.stream("POST", row["base_url"] + path.removeprefix("/v1"), content=body,
                        headers={"Authorization": "Bearer " + row["key"], "Content-Type": "application/json",
                                 **extra,
                                 **({"X-Unified-Account-Uid":row["account_uid"]} if row.get("adapter")=="hub" and row.get("account_uid") else {}),
                                 **({"X-Realm": "intl"} if row.get("adapter") == "hub" else
                                    {"x-wb-site": "intl"} if row.get("kind") == "workbuddy_intl" else {})}) as response:
                    if response.status_code >= 300:
                        detail = await _bounded_body(response)
                        if row.get('adapter')=='hub' and row.get('account_uid'):
                            from .workbuddy_gates import REASONS
                            from .diagnostics import note_requested_account,note_account
                            note_requested_account('workbuddy',row['account_uid'])
                            actual=response.headers.get('X-Unified-Account')
                            if actual:note_account('workbuddy',actual)
                            reason=response.headers.get('X-Unified-Account-Rejection')
                            if response.status_code==503 and reason in REASONS:
                                raise HTTPException(503,{'code':reason,'message':REASONS[reason]})
                        from .diagnostics import CURRENT_TRACE
                        trace = CURRENT_TRACE.get()
                        if trace and trace.current_response:
                            try:
                                trace.current_response.value(json.loads(detail))
                            except (ValueError,TypeError):
                                pass
                        raise HTTPException(response.status_code if response.status_code >= 400 else 502,
                                            safe_error_message(detail, response.status_code, (row["key"], *self.runtime_secrets(row))))
                    content_type = response.headers.get("content-type", "application/json")
                    if row.get("adapter")=="hub" and response.headers.get("X-Unified-Account"):
                        from .diagnostics import note_account
                        note_account("workbuddy",response.headers["X-Unified-Account"])
                    async def chunks():
                        if "text/event-stream" in content_type and (path == "/v1/chat/completions" or session_context):
                            # Empty tool arrays must not split reasoning blocks in AI SDK clients.
                            async for line in response.aiter_lines():
                                if line.startswith("data:"):
                                    if session_context and path.startswith('/v1/responses'):
                                        self.opencode.observe(session_context, line[5:])
                                    try:
                                        event = json.loads(line[5:])
                                        for choice in event.get("choices", []):
                                            delta = choice.get("delta")
                                            if isinstance(delta, dict) and delta.get("tool_calls") == []:
                                                del delta["tool_calls"]
                                        line = "data: " + json.dumps(event, ensure_ascii=False)
                                    except (ValueError, AttributeError, TypeError):
                                        pass
                                yield (line + "\n").encode()
                        else:
                            observed = bytearray()
                            async for chunk in response.aiter_bytes():
                                if session_context and path.startswith('/v1/responses') and len(observed) <= 65536:
                                    observed.extend(chunk[:65537-len(observed)])
                                yield chunk
                            if session_context and len(observed) <= 65536:
                                self.opencode.observe(session_context, observed)
                    started = True
                    await StreamingResponse(chunks(), status_code=response.status_code,
                        headers={"Content-Type": content_type, "Cache-Control": "no-cache", "X-Accel-Buffering": "no"})(scope, receive, send)
        except httpx.HTTPError as exc:
            if started:
                raise  # Abort a broken stream; never append a second HTTP response.
            raise HTTPException(502, "无法连接自定义服务：" + safe_error_message(str(exc) or type(exc).__name__, 502, (row["key"], *self.runtime_secrets(row)))) from None
