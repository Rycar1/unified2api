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


class Connections:
    def __init__(self, path, transport=None):
        self.path, self.transport = path, transport
        self.lock = threading.RLock()
        self.data = json.loads(path.read_text("utf-8")) if path.exists() else {}

    def client(self):
        return httpx.AsyncClient(transport=self.transport, timeout=httpx.Timeout(300, connect=20),
                                 follow_redirects=False, trust_env=False)

    def get(self, cid):
        with self.lock:
            if cid not in self.data:
                raise HTTPException(404, "自定义服务不存在")
            return copy.deepcopy(self.data[cid])

    def rows(self):
        with self.lock:
            return [{k: v for k, v in row.items() if k != "key"} | {"has_key": bool(row["key"])}
                    for row in self.data.values()]

    def validate(self, body, old=None):
        row = dict(old or {})
        for field in ("id", "name", "base_url", "key", "models", "enabled"):
            if field in body:
                if field == "key" and old and body[field] == "":
                    continue
                row[field] = body[field]
        cid = row.get("id", "")
        if not isinstance(cid, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,39}", cid) or cid in {"trae", "codebuddy", "monkeycode"}:
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
        row.setdefault("enabled", True)
        if type(row["enabled"]) is not bool:
            raise HTTPException(400, "启用状态必须为布尔值")
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
        old = self.get(body["existing_id"]) if body.get("existing_id") else None
        row = self.validate(body, old)
        try:
            async with self.client() as client:
                response = await client.get(row["base_url"] + "/models", headers={"Authorization": "Bearer " + row["key"]}, timeout=30)
                if response.status_code != 200:
                    raise HTTPException(502, f"模型列表读取失败（上游 HTTP {response.status_code}），可手动填写模型名")
                data = response.json()
                models = [m["id"] for m in data["data"] if isinstance(m, dict) and isinstance(m.get("id"), str)]
                row["models"] = models
                return {"models": self.validate(row)["models"]}
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            raise HTTPException(502, "无法读取模型列表，请检查地址和 Key，或手动填写模型名")

    def models(self):
        return [{"id": row["id"] + "/" + model, "object": "model", "owned_by": row["id"],
                 "provider_name": row["name"]} for row in self.rows() if row["enabled"] for model in row["models"]]

    async def relay(self, cid, path, body, scope, receive, send):
        row = self.get(cid)
        if not row["enabled"]:
            raise HTTPException(503, "自定义服务已暂停")
        if path not in {"/v1/chat/completions", "/v1/responses"}:
            raise HTTPException(400, "自定义服务支持 Chat Completions / Responses，具体以该服务能力为准")
        started = False
        try:
            async with self.client() as client:
                async with client.stream("POST", row["base_url"] + path.removeprefix("/v1"), content=body,
                        headers={"Authorization": "Bearer " + row["key"], "Content-Type": "application/json"}) as response:
                    if response.status_code >= 300:
                        raise HTTPException(response.status_code if response.status_code >= 400 else 502,
                                            f"自定义服务返回 HTTP {response.status_code}，请检查配置、模型权限及额度")
                    content_type = response.headers.get("content-type", "application/json")
                    async def chunks():
                        if "text/event-stream" in content_type and path == "/v1/chat/completions":
                            # Empty tool arrays must not split reasoning blocks in AI SDK clients.
                            async for line in response.aiter_lines():
                                if line.startswith("data:"):
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
                            async for chunk in response.aiter_bytes():
                                yield chunk
                    started = True
                    await StreamingResponse(chunks(), status_code=response.status_code,
                        headers={"Content-Type": content_type, "Cache-Control": "no-cache", "X-Accel-Buffering": "no"})(scope, receive, send)
        except httpx.HTTPError:
            if started:
                raise  # Abort a broken stream; never append a second HTTP response.
            raise HTTPException(502, "无法连接自定义服务，请检查 Base URL 和网络")
