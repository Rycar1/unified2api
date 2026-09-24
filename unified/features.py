"""Persistent routing, observability, automation and encrypted backups."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import tempfile
import threading
import time
import zipfile
from collections import defaultdict
from contextlib import closing
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import HTTPException


ROUTE_ID = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")
BACKUP_MAGIC = b"U2API1\0"
MAX_BACKUP = 11 * 1024 * 1024


class UnifiedConfig:
    def __init__(self, store):
        self.store = store
        with store.lock:
            root = store.data.setdefault("unified", {})
            root.setdefault("routes", [])
            root.setdefault("route_health", {})
            root.setdefault("automation", {
                "auto_checkin": False, "checkin_time": "09:00",
                "auto_refresh": False, "refresh_minutes": 30,
                "webhook_url": "", "low_balance": 0, "notify_failures": True,
            })
            root.setdefault("automation_history", [])
            root.setdefault("console_settings", {"retention_days": 365, "auto_refresh_seconds": 0})
            store.save()

    def get(self, key, default=None):
        with self.store.lock:
            return json.loads(json.dumps(self.store.data["unified"].get(key, default)))

    def set(self, key, value):
        with self.store.lock:
            self.store.data["unified"][key] = value
            self.store.save()


class RouteManager:
    def __init__(self, config, clock=time.time):
        self.config, self.clock = config, clock
        self.cursors = defaultdict(int)
        self.lock = threading.RLock()
        self.health = config.get("route_health", {})
        self.last_persist = 0

    def rows(self):
        routes = self.config.get("routes", [])
        with self.lock:
            health = json.loads(json.dumps(self.health))
        for route in routes:
            route["health"] = {target: health.get(target, {}) for target in route["targets"]}
        return routes

    def public_models(self):
        return [{"id": "route/" + row["id"], "object": "model", "owned_by": "route",
                 "provider_name": row["name"]} for row in self.rows() if row.get("enabled", True)]

    def validate(self, value, existing_models, current_id=None):
        if not isinstance(value, dict):
            raise HTTPException(400, "路由配置格式无效")
        rid = value.get("id", current_id)
        if not isinstance(rid, str) or not ROUTE_ID.fullmatch(rid):
            raise HTTPException(400, "路由前缀只能使用小写字母、数字、下划线或连字符")
        name = value.get("name", rid)
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 60:
            raise HTTPException(400, "路由名称需要 1–60 个字符")
        targets = value.get("targets")
        if not isinstance(targets, list) or not 1 <= len(targets) <= 20:
            raise HTTPException(400, "请选择 1–20 个目标模型")
        targets = list(dict.fromkeys(str(item).strip() for item in targets))
        if any(t.startswith("route/") or t not in existing_models for t in targets):
            raise HTTPException(400, "路由目标必须是当前可用的非路由模型")
        strategy = value.get("strategy", "priority")
        if strategy not in {"priority", "round_robin", "latency"}:
            raise HTTPException(400, "不支持的路由策略")
        try:
            retries = int(value.get("retries", len(targets) - 1))
            cooldown = int(value.get("cooldown_seconds", 300))
        except (TypeError, ValueError):
            raise HTTPException(400, "重试次数和冷却时间必须是整数")
        if not 0 <= retries <= 10 or not 10 <= cooldown <= 86400:
            raise HTTPException(400, "重试次数需要 0–10，冷却时间需要 10–86400 秒")
        return {"id": rid, "name": name.strip(), "targets": targets, "strategy": strategy,
                "retries": retries, "cooldown_seconds": cooldown,
                "enabled": bool(value.get("enabled", True)), "updated": int(self.clock())}

    def save(self, value, existing_models, current_id=None):
        route = self.validate(value, existing_models, current_id)
        routes = self.config.get("routes", [])
        if current_id:
            routes = [item for item in routes if item["id"] != current_id]
        elif any(item["id"] == route["id"] for item in routes):
            raise HTTPException(409, "路由前缀已存在")
        routes.append(route)
        self.config.set("routes", routes)
        return route

    def delete(self, rid):
        routes = self.config.get("routes", [])
        filtered = [item for item in routes if item["id"] != rid]
        if len(filtered) == len(routes):
            raise HTTPException(404, "路由不存在")
        self.config.set("routes", filtered)
        return {"ok": True}

    def get(self, rid):
        route = next((item for item in self.config.get("routes", []) if item["id"] == rid), None)
        if not route or not route.get("enabled", True):
            raise HTTPException(404, "路由不存在或已停用")
        return route

    def candidates(self, rid):
        route = self.get(rid)
        with self.lock:
            health = json.loads(json.dumps(self.health))
        now = self.clock()
        ready = [target for target in route["targets"] if health.get(target, {}).get("cooldown_until", 0) <= now]
        targets = ready or list(route["targets"])
        if route["strategy"] == "round_robin" and targets:
            with self.lock:
                offset = self.cursors[rid] % len(targets)
                self.cursors[rid] += 1
            targets = targets[offset:] + targets[:offset]
        elif route["strategy"] == "latency":
            targets.sort(key=lambda target: health.get(target, {}).get("avg_duration_ms", float("inf")))
        return route, targets[:route["retries"] + 1]

    def record(self, target, ok, status, duration_ms, cooldown_seconds):
        with self.lock:
            item = self.health.setdefault(target, {})
            item["requests"] = item.get("requests", 0) + 1
            item["successes"] = item.get("successes", 0) + int(ok)
            item["last_status"] = status
            item["last_used"] = int(self.clock())
            if ok:
                old = item.get("avg_duration_ms")
                item["avg_duration_ms"] = round(duration_ms if old is None else old * .8 + duration_ms * .2)
                item["cooldown_until"] = 0
            elif status in {401, 402, 403, 408, 409, 429, 500, 502, 503, 504}:
                item["cooldown_until"] = int(self.clock()) + cooldown_seconds
            if not ok or self.clock() - self.last_persist >= 15:
                self.config.set("route_health", self.health)
                self.last_persist = self.clock()


class RequestLog:
    def __init__(self, path, retention_days=365):
        self.path = Path(path)
        self.retention_days = max(30, min(int(retention_days), 365))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        with closing(self.connect()) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT, created INTEGER NOT NULL, path TEXT NOT NULL,
                model TEXT, resolved_model TEXT, provider TEXT, status INTEGER, ok INTEGER NOT NULL,
                duration_ms INTEGER NOT NULL, prompt_tokens INTEGER NOT NULL DEFAULT 0,
                completion_tokens INTEGER NOT NULL DEFAULT 0, reasoning_tokens INTEGER NOT NULL DEFAULT 0,
                outcome TEXT NOT NULL)""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_requests_created ON requests(created DESC)")
            db.commit()

    def connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def record(self, row):
        with self.lock, closing(self.connect()) as db:
            db.execute("""INSERT INTO requests(created,path,model,resolved_model,provider,status,ok,duration_ms,
                prompt_tokens,completion_tokens,reasoning_tokens,outcome) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (int(time.time()), row["path"], row.get("model"), row.get("resolved_model"),
                 (row.get("model") or "").partition("/")[0], row.get("status"), int(row["ok"]),
                 round(row["duration_ms"]), row.get("prompt_tokens", 0), row.get("completion_tokens", 0),
                 row.get("reasoning_tokens", 0), row["outcome"]))
            db.execute("DELETE FROM requests WHERE created < ?", (int(time.time()) - self.retention_days * 86400,))
            db.commit()

    def set_retention_days(self, days):
        self.retention_days = max(30, min(int(days), 365))
        cutoff = int(time.time()) - self.retention_days * 86400
        with self.lock, closing(self.connect()) as db:
            db.execute("DELETE FROM requests WHERE created < ?", (cutoff,))
            db.commit()

    def usage(self):
        """Return per-day and per-model token usage without storing request content."""
        since = int(time.time()) - self.retention_days * 86400
        with self.lock, closing(self.connect()) as db:
            db.row_factory = sqlite3.Row
            daily = db.execute("""SELECT strftime('%Y-%m-%d', created, 'unixepoch', 'localtime') day,
                COUNT(*) requests, COALESCE(SUM(prompt_tokens),0) prompt_tokens,
                COALESCE(SUM(completion_tokens),0) completion_tokens,
                COALESCE(SUM(reasoning_tokens),0) reasoning_tokens
                FROM requests WHERE created>=? GROUP BY day ORDER BY day""", (since,)).fetchall()
            models = db.execute("""SELECT COALESCE(NULLIF(model,''),'未知模型') model, COUNT(*) requests,
                COALESCE(SUM(prompt_tokens),0) prompt_tokens,
                COALESCE(SUM(completion_tokens),0) completion_tokens,
                COALESCE(SUM(reasoning_tokens),0) reasoning_tokens
                FROM requests WHERE created>=? GROUP BY model ORDER BY (SUM(prompt_tokens)+SUM(completion_tokens)) DESC""",
                (since,)).fetchall()
            total = db.execute("""SELECT COUNT(*) requests, COALESCE(SUM(prompt_tokens),0) prompt_tokens,
                COALESCE(SUM(completion_tokens),0) completion_tokens,
                COALESCE(SUM(reasoning_tokens),0) reasoning_tokens FROM requests WHERE created>=?""",
                (since,)).fetchone()
        return {"retention_days": self.retention_days, "daily": [dict(row) for row in daily],
                "models": [dict(row) for row in models], "total": dict(total), "generated_at": int(time.time())}

    def query(self, limit=100, offset=0, model="", ok=None):
        try:
            limit, offset = max(1, min(int(limit), 500)), max(0, int(offset))
        except (TypeError, ValueError):
            limit, offset = 100, 0
        clauses, args = [], []
        if model:
            clauses.append("model LIKE ?")
            args.append("%" + model[:200] + "%")
        if ok in (True, False):
            clauses.append("ok = ?")
            args.append(int(ok))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self.lock, closing(self.connect()) as db:
            db.row_factory = sqlite3.Row
            total = db.execute("SELECT COUNT(*) FROM requests" + where, args).fetchone()[0]
            rows = db.execute("SELECT * FROM requests" + where + " ORDER BY id DESC LIMIT ? OFFSET ?",
                              args + [limit, offset]).fetchall()
        return {"total": total, "items": [dict(row) for row in rows]}

    def summary(self, hours=24):
        try:
            hours = max(1, min(int(hours), 24 * 90))
        except (TypeError, ValueError):
            hours = 24
        since = int(time.time()) - hours * 3600
        with self.lock, closing(self.connect()) as db:
            db.row_factory = sqlite3.Row
            total = db.execute("""SELECT COUNT(*) count, COALESCE(SUM(ok),0) succeeded,
                COALESCE(AVG(duration_ms),0) avg_duration_ms, COALESCE(SUM(prompt_tokens),0) prompt_tokens,
                COALESCE(SUM(completion_tokens),0) completion_tokens,
                COALESCE(SUM(reasoning_tokens),0) reasoning_tokens FROM requests WHERE created>=?""", (since,)).fetchone()
            models = db.execute("""SELECT model, COUNT(*) requests, COALESCE(SUM(ok),0) succeeded,
                ROUND(AVG(duration_ms)) avg_duration_ms FROM requests WHERE created>=?
                GROUP BY model ORDER BY requests DESC LIMIT 20""", (since,)).fetchall()
        data = dict(total)
        data["success_rate"] = round(data["succeeded"] * 100 / data["count"], 1) if data["count"] else None
        data["models"] = [dict(row) for row in models]
        return data

    def clear(self):
        with self.lock, closing(self.connect()) as db:
            db.execute("DELETE FROM requests")
            db.commit()


def _usage_from(value):
    if not isinstance(value, dict):
        return {}
    usage = value.get("usage")
    if not isinstance(usage, dict) and isinstance(value.get("response"), dict):
        usage = value["response"].get("usage")
    if not isinstance(usage, dict):
        return {}
    details = usage.get("completion_tokens_details") or usage.get("output_tokens_details") or {}
    return {
        "prompt_tokens": int(usage.get("prompt_tokens", usage.get("input_tokens", 0)) or 0),
        "completion_tokens": int(usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0),
        "reasoning_tokens": int(details.get("reasoning_tokens", usage.get("completion_thinking_tokens", 0)) or 0),
    }


class RequestLogMiddleware:
    """Stores metadata and usage only; prompt and response content are never persisted."""
    def __init__(self, app, request_log):
        self.app, self.request_log = app, request_log

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or scope.get("method") != "POST" or scope.get("path") not in {
            "/v1/chat/completions", "/v1/responses", "/v1/messages"
        }:
            return await self.app(scope, receive, send)
        parts, size = [], 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body = message.get("body", b"")
            size += len(body)
            if size <= 8 * 1024 * 1024:
                parts.append(body)
            if not message.get("more_body", False):
                break
        raw = b"".join(parts)
        try:
            payload = json.loads(raw)
            model = payload.get("model") if isinstance(payload, dict) else None
        except (ValueError, UnicodeDecodeError):
            model = None
        replayed = False
        async def replay():
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": raw, "more_body": False}
            return await receive()
        start = time.monotonic()
        status, completed, disconnected = None, False, False
        response = bytearray()
        stream_buffer = bytearray()
        content_type = b""
        usage = {}
        async def observed_send(message):
            nonlocal status, completed, content_type, usage, stream_buffer
            if message["type"] == "http.response.start":
                status = message["status"]
                content_type = dict(message.get("headers", [])).get(b"content-type", b"")
            elif message["type"] == "http.response.body":
                chunk = message.get("body", b"")
                if len(response) < 512 * 1024:
                    response.extend(chunk[:512 * 1024 - len(response)])
                if b"text/event-stream" in content_type:
                    stream_buffer.extend(chunk)
                    while b"\n" in stream_buffer:
                        line, _, remainder = stream_buffer.partition(b"\n")
                        stream_buffer = bytearray(remainder)
                        if line.startswith(b"data:") and line[5:].strip() != b"[DONE]":
                            try:
                                usage.update(_usage_from(json.loads(line[5:].strip())))
                            except (ValueError, UnicodeDecodeError):
                                pass
                    if len(stream_buffer) > 65536:
                        stream_buffer.clear()
                if not message.get("more_body", False):
                    completed = True
            await send(message)
        try:
            await self.app(scope, replay, observed_send)
        finally:
            try:
                if b"text/event-stream" not in content_type:
                    usage.update(_usage_from(json.loads(response)))
            except (ValueError, UnicodeDecodeError):
                pass
            ok = status is not None and 200 <= status < 300 and completed and not disconnected
            self.request_log.record({"path": scope["path"], "model": model,
                "resolved_model": scope.get("state", {}).get("resolved_model"), "status": status, "ok": ok,
                "duration_ms": (time.monotonic() - start) * 1000,
                "outcome": "success" if ok else "interrupted" if not completed else "http_error", **usage})


class AutomationManager:
    def __init__(self, config, runner, clock=time.time):
        self.config, self.runner, self.clock = config, runner, clock
        self.running = asyncio.Lock()
        self.last_refresh = 0

    def settings(self):
        return self.config.get("automation", {})

    def update(self, value):
        old = self.settings()
        result = dict(old)
        for key in ("auto_checkin", "auto_refresh", "notify_failures"):
            if key in value:
                if type(value[key]) is not bool:
                    raise HTTPException(400, key + " 必须是布尔值")
                result[key] = value[key]
        checkin_time = value.get("checkin_time", result.get("checkin_time", "09:00"))
        if not isinstance(checkin_time, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", checkin_time):
            raise HTTPException(400, "签到时间格式应为 HH:MM")
        try:
            refresh_minutes = int(value.get("refresh_minutes", result.get("refresh_minutes", 30)))
            low_balance = float(value.get("low_balance", result.get("low_balance", 0)))
        except (TypeError, ValueError):
            raise HTTPException(400, "刷新间隔和余额阈值必须是数字")
        if not 5 <= refresh_minutes <= 1440 or not 0 <= low_balance <= 1e12:
            raise HTTPException(400, "刷新间隔需要 5–1440 分钟，余额阈值不能为负数")
        webhook = value.get("webhook_url", result.get("webhook_url", ""))
        if not isinstance(webhook, str) or len(webhook) > 2048:
            raise HTTPException(400, "Webhook 地址无效")
        if webhook and urlparse(webhook).scheme not in {"http", "https"}:
            raise HTTPException(400, "Webhook 地址必须使用 HTTP 或 HTTPS")
        result.update(checkin_time=checkin_time, refresh_minutes=refresh_minutes,
                      low_balance=low_balance, webhook_url=webhook.strip())
        self.config.set("automation", result)
        return result

    async def execute(self, action, manual=False):
        if action not in {"checkin", "refresh"}:
            raise HTTPException(400, "不支持的自动任务")
        if self.running.locked():
            raise HTTPException(409, "已有自动任务正在运行")
        async with self.running:
            started = int(self.clock())
            try:
                result = await self.runner(action)
                ok = result.get("summary", {}).get("failed", 0) == 0
            except Exception as exc:
                result, ok = {"error": str(exc)[:300]}, False
            record = {"time": started, "action": action, "manual": manual, "ok": ok,
                      "summary": result.get("summary", {}), "error": result.get("error")}
            history = self.config.get("automation_history", [])
            self.config.set("automation_history", ([record] + history)[:100])
            if action == "refresh":
                self.last_refresh = self.clock()
            await self.notify(record, result)
            return {"record": record, "result": result}

    async def notify(self, record, result):
        settings = self.settings()
        url = settings.get("webhook_url")
        if not url:
            return
        failures = result.get("summary", {}).get("failed", 0)
        low = []
        threshold = settings.get("low_balance", 0)
        if threshold:
            for group in result.get("providers", []):
                for item in group.get("results", []):
                    remaining = item.get("remaining")
                    if isinstance(remaining, (int, float)) and remaining < threshold:
                        low.append({"provider": group.get("provider"), "id": item.get("id"), "remaining": remaining})
        if not low and (not failures or not settings.get("notify_failures", True)):
            return
        payload = {"event": "unified2api.automation", "action": record["action"],
                   "ok": record["ok"], "failed": failures, "low_balance": low, "time": record["time"]}
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
                await client.post(url, json=payload)
        except httpx.HTTPError:
            pass

    async def loop(self):
        while True:
            await asyncio.sleep(30)
            settings = self.settings()
            now = datetime.fromtimestamp(self.clock())
            history = self.config.get("automation_history", [])
            checked_today = any(item.get("action") == "checkin" and
                datetime.fromtimestamp(item.get("time", 0)).date() == now.date() for item in history)
            if settings.get("auto_checkin") and now.strftime("%H:%M") >= settings.get("checkin_time", "09:00") and not checked_today:
                await self.execute("checkin")
            if settings.get("auto_refresh") and self.clock() - self.last_refresh >= settings.get("refresh_minutes", 30) * 60:
                await self.execute("refresh")


class BackupManager:
    def __init__(self, roots):
        self.roots = {name: Path(path) for name, path in roots.items()}

    @staticmethod
    def _key(password, salt):
        if not isinstance(password, str) or len(password) < 8:
            raise HTTPException(400, "备份密码至少需要 8 个字符")
        return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 300_000, dklen=32)

    def export(self, password, include_logs=True):
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("backup.json", json.dumps({"version": 1, "created": int(time.time()),
                "roots": sorted(self.roots)}, ensure_ascii=False))
            for name, root in self.roots.items():
                if not root.exists():
                    continue
                for path in root.rglob("*"):
                    if not path.is_file() or path.is_symlink():
                        continue
                    if not include_logs and path.name.startswith("request_logs.sqlite"):
                        continue
                    zf.write(path, (Path(name) / path.relative_to(root)).as_posix())
        plain = archive.getvalue()
        if len(plain) > MAX_BACKUP:
            raise HTTPException(413, "备份内容超过 11 MB，请取消包含调用记录后重试")
        salt, nonce = os.urandom(16), os.urandom(12)
        encrypted = AESGCM(self._key(password, salt)).encrypt(nonce, plain, BACKUP_MAGIC)
        return BACKUP_MAGIC + salt + nonce + encrypted

    def restore(self, encoded, password):
        try:
            blob = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError):
            raise HTTPException(400, "备份文件编码无效")
        if len(blob) > MAX_BACKUP or not blob.startswith(BACKUP_MAGIC):
            raise HTTPException(400, "不是有效的 Unified2API 加密备份")
        pos = len(BACKUP_MAGIC)
        salt, nonce, ciphertext = blob[pos:pos+16], blob[pos+16:pos+28], blob[pos+28:]
        try:
            plain = AESGCM(self._key(password, salt)).decrypt(nonce, ciphertext, BACKUP_MAGIC)
        except Exception:
            raise HTTPException(400, "备份密码错误或文件已损坏")
        if len(plain) > MAX_BACKUP:
            raise HTTPException(413, "备份解压内容超过 11 MB")
        stage = Path(tempfile.mkdtemp(prefix="unified-restore-"))
        try:
            with zipfile.ZipFile(io.BytesIO(plain)) as zf:
                names = zf.namelist()
                if "backup.json" not in names:
                    raise HTTPException(400, "备份缺少版本信息")
                total = sum(info.file_size for info in zf.infolist())
                if total > MAX_BACKUP:
                    raise HTTPException(413, "备份解压内容超过 11 MB")
                for info in zf.infolist():
                    path = Path(info.filename)
                    if path.is_absolute() or ".." in path.parts:
                        raise HTTPException(400, "备份包含不安全路径")
                    if path.parts and path.parts[0] != "backup.json" and path.parts[0] not in self.roots:
                        raise HTTPException(400, "备份包含未知数据目录")
                zf.extractall(stage)
            for name, destination in self.roots.items():
                source = stage / name
                if not source.exists():
                    continue
                destination.mkdir(parents=True, exist_ok=True)
                for child in list(destination.iterdir()):
                    if child.is_dir() and not child.is_symlink():
                        shutil.rmtree(child)
                    else:
                        child.unlink(missing_ok=True)
                shutil.copytree(source, destination, dirs_exist_ok=True)
            return {"ok": True, "restart_required": True}
        finally:
            shutil.rmtree(stage, ignore_errors=True)
