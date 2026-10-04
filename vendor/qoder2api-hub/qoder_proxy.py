#!/usr/bin/env python3
"""Qoder -> OpenAI-compatible reverse proxy (CN + Intl dual realm).

把 Qoder 国内版 (qoder.com.cn) 与国际版 (qoder.com) 的原生服务封装为标准
OpenAI 兼容接口。上游链路：

    客户端 OpenAI 请求
      -> build_qoder_body()      (官方 baseprompt 模板 + 会话压平)
      -> qoder_encode()          (Qoder 自定义 Base64 变体)
      -> COSY 签名               (RSA 包裹 AES 会话密钥 + MD5 请求签名)
      -> POST {gateway}/algo/api/v2/service/pro/sse/agent_chat_generation
      -> SSE 信封解包            ({"headers","body","statusCodeValue"} 嵌套帧)
      -> 标准 OpenAI SSE / chat.completion 回给客户端

暴露：
    GET  /v1/models
    POST /v1/chat/completions     (stream=true / false)
    POST /v1/responses            (Responses API 双向转换)
    GET  /health
    以及看板全套管理接口 (accounts / usage / tasks / scheduler / settings / logs)

仅使用 Python 标准库（AES/RSA/COSY 签名均为内置纯实现）。

    python qoder_proxy.py                    # bind 127.0.0.1:8790
    python qoder_proxy.py --port 9000
    python qoder_proxy.py --api-key sk-local  # require a bearer token
"""
import argparse
import hashlib
from collections import deque
import re
import json
import os
MAX_PAYLOAD_BYTES = int(os.environ.get("QD_MAX_PAYLOAD_BYTES", 50 * 1024 * 1024))  # 50MB limit
import ipaddress
import socket
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

import qoder_accounts
import qoder_catalog
import qoder_settings
import qoder_sign
from qoder_sign import qoder_encode, SESSIONS
from qoder_accounts import (get_realm_config, gateway_candidates, CLIENT_UA,
                            local_vm_status)
from pathlib import Path

VERSION = "1.2.3"

CURRENT_REALM = os.environ.get("QD_PROXY_DEFAULT_REALM", "cn")


# 198.18.0.0/15 (RFC 2544 benchmarking) 与 fdfe:dcba:9876::/48 被 Clash/mihomo
# 等本地代理用作 fake-IP DNS 段：开启透明代理的机器上所有公网域名都会解析到
# 这些网段。命中它说明 DNS 已被本机代理接管、真实 IP 不可见，此时跳过解析级
# 校验（名称级校验已完成）。
_FAKEIP_NETS = [
    ipaddress.ip_network("198.18.0.0/15"),
    ipaddress.ip_network("2001:2::/48"),
    ipaddress.ip_network("fdfe:dcba:9876::/48"),
]


def _host_boundary_violation(ip, allow_local):
    """True when the resolved/IP address must be refused."""
    if ip.is_loopback:
        return not allow_local
    if (ip.is_private or ip.is_reserved or ip.is_link_local
            or ip.is_multicast or ip.is_unspecified):
        return True
    return False


def validate_public_http_url(url, allow_local=False):
    """SSRF 防护：仅允许 http/https，且 host 不得指向本机/私有/保留网段。

    与 qoder_accounts.validate_public_http_url 同逻辑、独立实现防同错：
    本文件的 urlopen 调用点必须经过本文件的边界校验（签名链路上的 URL
    在 sess.headers() 之后再校验一次，确保进入 Request 的字符串是洁净的）。
    allow_local 仅供显式面向本机网关的开发/验证脚本使用。
    """
    parsed = urllib.parse.urlsplit(str(url or ""))
    if parsed.scheme not in ("http", "https"):
        raise ValueError("only http/https URLs are allowed")
    host = (parsed.hostname or "").strip().strip("[]").lower()
    if not host:
        raise ValueError("URL host is required")
    if host == "localhost" or host.endswith(".localhost") or host.endswith(".local"):
        if not allow_local:
            raise ValueError("requests to localhost are not allowed")
        return url
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if _host_boundary_violation(literal, allow_local):
            raise ValueError(
                "requests to private/reserved address %s are not allowed" % literal)
        return url
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError("cannot resolve URL host %r: %s" % (host, exc))
    addrs = []
    for info in infos:
        addr = str(info[4][0]).strip("[]")
        try:
            addrs.append(ipaddress.ip_address(addr))
        except ValueError:
            raise ValueError("URL host resolved to a non-IP address: %r" % addr)
    if addrs and all(any(ip in net for net in _FAKEIP_NETS) for ip in addrs):
        return url  # fake-IP DNS：真实 IP 不可见，名称级校验已通过
    for ip in addrs:
        if _host_boundary_violation(ip, allow_local):
            raise ValueError(
                "requests to private/reserved address %s are not allowed" % ip)
    return url


def detect_model_realm(model_id):
    """模型 -> 出口区域。区域独占模型强制路由到其归属出口，其余跟随全局开关。"""
    owner = exclusive_realm(model_id)
    if owner:
        return owner
    if not model_id:
        return CURRENT_REALM
    return CURRENT_REALM


# 目前双区模型清单不同（源自官方 catalog 快照差集），独占表给区域特供模型。
INTL_EXCLUSIVE_PREFIXES = getattr(qoder_catalog, "INTL_EXCLUSIVE_PREFIXES", ())
CN_EXCLUSIVE_PREFIXES = getattr(qoder_catalog, "CN_EXCLUSIVE_PREFIXES", ())
INTL_EXCLUSIVE = getattr(qoder_catalog, "INTL_EXCLUSIVE", set())
CN_EXCLUSIVE = getattr(qoder_catalog, "CN_EXCLUSIVE", set())


def exclusive_realm(model_id):
    """"intl"/"cn" when only that exit serves the model, else "".

    先把人类可读别名解析成上游 key 再判独占（glm-5.2 -> gm51model 是国内独占）。
    """
    if not model_id:
        return ""
    m = str(model_id).lower()
    resolved = qoder_catalog.resolve_upstream_key(model_id).lower()
    for cand in (resolved, m):
        if cand in INTL_EXCLUSIVE or cand.startswith(INTL_EXCLUSIVE_PREFIXES):
            return "intl"
        if cand in CN_EXCLUSIVE or cand.startswith(CN_EXCLUSIVE_PREFIXES):
            return "cn"
    return ""


from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs


def install_console_close_handler():
    """Release the port when the console window is closed by the user.

    Windows does not kill child processes when a console window closes, so
    the proxy (started by the .bat as a child of cmd.exe) would survive and
    keep the port bound - the next launch then wrongly reports "another
    proxy is already running". Registering an event-driven handler for
    CTRL_CLOSE_EVENT is the reliable signal. Harmless without a console.
    """
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes
        PHANDLER_ROUTINE = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)
        CTRL_CLOSE_EVENT = 2
        CTRL_LOGOFF_EVENT = 5
        CTRL_SHUTDOWN_EVENT = 6

        def _handler(event):
            if event in (CTRL_CLOSE_EVENT, CTRL_LOGOFF_EVENT, CTRL_SHUTDOWN_EVENT):
                try:
                    sys.stdout.flush()
                except Exception:
                    pass
                os._exit(0)
            return False

        handler = PHANDLER_ROUTINE(_handler)   # keep the callback referenced
        if not ctypes.windll.kernel32.SetConsoleCtrlHandler(handler, True):
            return None
        return handler
    except Exception:
        return None


CHAT_PATH = ("/algo/api/v2/service/pro/sse/agent_chat_generation"
             "?FetchKeys=llm_model_result&AgentId=agent_common&Encode=1")
MODELS_PATH = "/algo/api/v2/model/list?Encode=1"
DEFAULT_SYSTEM_PROMPT = "You are a helpful assistant."

# 官方 baseprompt 模板（与桌面端一致的请求体骨架）
BASEPROMPT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "baseprompt.json")
try:
    with open(BASEPROMPT_PATH, encoding="utf-8") as _bp_fh:
        BASEPROMPT = json.load(_bp_fh)
except Exception:
    BASEPROMPT = {}

NOISE_KEYS = ("extra_fields", "refusal", "reasoning_content")

# DeepSeek 上游 model key（官方目录：dmodel=DeepSeek-V4-Pro / dfmodel=DeepSeek-Flash）。
# 这族模型的多轮一致性与 reasoning_content 强相关，见 is_deepseek_model。
DEEPSEEK_MODEL_KEYS = ("dmodel", "dfmodel")


class BodyTooLarge(Exception):
    """Raised when a request body exceeds the configured cap."""

    def __init__(self, length):
        super(BodyTooLarge, self).__init__(length)
        self.length = length


class BadJSON(Exception):
    """Raised when a request body is present but not a JSON object."""


class UpstreamStatus(Exception):
    """Raised when the upstream SSE envelope carries a non-200 status."""

    def __init__(self, status, detail=""):
        super(UpstreamStatus, self).__init__("upstream status %s" % status)
        self.status = status
        self.detail = str(detail or "")


# CORS is only needed by browser-based chat clients that call the OpenAI-style
# API from another origin. Management routes serve the dashboard (same-origin)
# and get no ACAO header - that keeps a stray LAN page from reading them.
CORS_PATH_PREFIXES = ("/v1", "/chat", "/completions", "/models", "/responses")
MANAGEMENT_PATH_PREFIXES = ("/v1/usage", "/usage", "/accounts", "/settings",
                            "/tasks", "/scheduler", "/panel", "/logs")


def cors_origin_allowed(path):
    """True when the OpenAI-style API path should advertise CORS."""
    path = (path or "").split("?")[0]
    if path.startswith(MANAGEMENT_PATH_PREFIXES):
        return False
    return path.startswith(CORS_PATH_PREFIXES)


_lock = threading.Lock()
_login_lock = threading.Lock()
_login_attempts = {}  # ip -> list of timestamp


def _prune_login_attempts(now=None, window=60):
    """Drop stale per-IP entries so the dict cannot grow without bound.

    Caller must hold _login_lock.
    """
    now = now or time.time()
    for ip in list(_login_attempts.keys()):
        recent = [t for t in _login_attempts[ip] if now - t < window]
        if recent:
            _login_attempts[ip] = recent
        else:
            del _login_attempts[ip]


_models_cache = {"intl": {"at": 0.0, "data": None}, "cn": {"at": 0.0, "data": None}}

# Usage accounting: every upstream response carries a usage block, and the
# proxy also records one JSONL line per request.
USAGE_DIR = os.environ.get("QD_PROXY_USAGE_DIR") \
    or os.path.join(os.path.dirname(os.path.abspath(__file__)), "usage")
USAGE_LOG = os.path.join(USAGE_DIR, "usage.jsonl")
USAGE_SUMMARY = os.path.join(USAGE_DIR, "usage-summary.json")
DASHBOARD_HTML = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "dashboard.html")
USAGE_FIELDS = ("prompt_tokens", "completion_tokens", "reasoning_tokens",
                "cached_tokens", "total_tokens", "credit")

# Web-panel access control. The panel is gated by its own password (default
# "admin"), independent of the /v1 API key. Sessions live in memory only.
PANEL = qoder_settings.PanelSessions()
API_KEY_FILE_SET = False


def configured_keys():
    """Panel-managed API keys, always read fresh so panel edits apply at once."""
    try:
        return qoder_settings.api_keys(ACCOUNTS_DIR)
    except Exception as exc:
        log("could not read api keys: %s" % exc)
        return []


def auth_required():
    """Whether /v1 calls must present a key at all."""
    if qoder_settings.auth_disabled(ACCOUNTS_DIR):
        return False
    if any(entry.get("enabled") for entry in configured_keys()):
        return True
    return bool(API_KEY)


def identify_key(supplied):
    """Return the key entry a caller used, or None when nothing matches.

    Once the panel has at least one key, those keys are the only accepted
    credentials - otherwise a launcher key left in a .bat file would silently
    keep working after the panel was locked down.
    """
    extra = () if configured_keys() else (API_KEY,)
    return qoder_settings.match_api_key(ACCOUNTS_DIR, supplied, extra_keys=extra)


def _empty_stats():
    return {"requests": 0, "errors": 0, "prompt_tokens": 0,
            "completion_tokens": 0, "reasoning_tokens": 0, "cached_tokens": 0,
            "total_tokens": 0, "credit": 0.0, "started": time.time(),
            "by_model": {},
            "ttft_ms_sum": 0, "ttft_samples": 0,
            "gen_ms_sum": 0, "gen_samples": 0,
            "wall_ms_sum": 0, "wall_samples": 0}


_usage = _empty_stats()


def _extract_usage(usage):
    """Normalize the upstream usage block into the fields we track."""
    if not usage:
        return {}
    details = usage.get("completion_tokens_details") or {}
    prompt_details = usage.get("prompt_tokens_details") or {}
    return {
        "prompt_tokens": usage.get("prompt_tokens") or 0,
        "completion_tokens": usage.get("completion_tokens") or 0,
        "reasoning_tokens": details.get("reasoning_tokens") or 0,
        "cached_tokens": usage.get("prompt_cache_hit_tokens")
        or details.get("cached_tokens") or prompt_details.get("cached_tokens") or 0,
        "total_tokens": usage.get("total_tokens") or 0,
        "credit": usage.get("credit") or 0,
    }


def row_matches_realm(row, realm):
    if not realm:
        return True
    r = row.get("realm")
    if r:
        return r == realm
    acct_uid = row.get("account")
    if acct_uid and POOL:
        acc = POOL.get(acct_uid)
        if acc:
            return acc.realm == realm
    model = row.get("model")
    if model:
        return detect_model_realm(model) == realm
    return realm == "cn"


def record_usage(model, usage, stream=None, elapsed_ms=None, ttft_ms=None,
                 gen_ms=None, fp=None, account=None):
    """Accumulate stats, append a JSONL row, and persist the summary."""
    fields = _extract_usage(usage)
    if not fields:
        return None
    row = {
        "at": time.time(),
        "iso": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "model": model,
        "stream": bool(stream),
        "elapsed_ms": elapsed_ms,
        "ttft_ms": ttft_ms,
        "gen_ms": gen_ms,
    }
    row.update(fields)
    if fp:
        row.update(fp)
    if account:
        row["account"] = account
    acc = POOL.get(account) if (account and POOL) else None
    row["realm"] = acc.realm if acc else CURRENT_REALM
    if gen_ms and gen_ms > 0:
        row["tokens_per_sec"] = round(
            fields["completion_tokens"] / (gen_ms / 1000.0), 2)
    if fields["prompt_tokens"] > 0:
        row["cache_hit_pct"] = round(
            fields["cached_tokens"] * 100.0 / fields["prompt_tokens"], 1)
    with _lock:
        _usage["requests"] += 1
        for k in USAGE_FIELDS:
            if k in fields:
                _usage[k] += fields[k]
        if ttft_ms is not None:
            _usage["ttft_ms_sum"] += ttft_ms
            _usage["ttft_samples"] += 1
        if gen_ms is not None:
            _usage["gen_ms_sum"] += gen_ms
            _usage["gen_samples"] += 1
        if elapsed_ms is not None:
            _usage["wall_ms_sum"] += elapsed_ms
            _usage["wall_samples"] += 1
        per = _usage["by_model"].setdefault(
            model, {"requests": 0, **{k: 0 for k in USAGE_FIELDS}})
        per["requests"] += 1
        for k in USAGE_FIELDS:
            if k in fields:
                per[k] += fields[k]
        summary = json.loads(json.dumps(_usage))
    _persist_usage(row, summary, "usage persist failed")
    try:
        t_tokens = fields.get("total_tokens", 0)
        dur = " %dms" % elapsed_ms if elapsed_ms is not None else ""
        acc_tag = " acct=%s" % account[:8] if account else ""
        speed_tag = " %st/s" % row.get("tokens_per_sec", 0) \
            if row.get("tokens_per_sec") else ""
        log("chat done: model=%s%s%s tokens=%d (in=%d out=%d)%s"
            % (model, acc_tag, dur, t_tokens, fields.get("prompt_tokens", 0),
               fields.get("completion_tokens", 0), speed_tag), tag="chat")
    except Exception:
        pass
    return row


def _persist_usage(row, summary, fail_label):
    """Append one JSONL row and atomically rewrite the summary.

    The temp file carries a unique suffix: two threads writing the same
    "<summary>.tmp" race, and the loser's os.replace() fails with ENOENT
    because the winner already renamed the file away.
    """
    try:
        usage_dir = Path(USAGE_DIR).resolve()
        usage_dir.mkdir(parents=True, exist_ok=True)
        log_path = usage_dir / os.path.basename(USAGE_LOG)
        summary_path = usage_dir / os.path.basename(USAGE_SUMMARY)
        if not (log_path.is_relative_to(usage_dir)
                and summary_path.is_relative_to(usage_dir)):
            raise ValueError("usage path escapes base directory")
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        tmp_summary = usage_dir / (
            os.path.basename(USAGE_SUMMARY)
            + ".%d.%d.tmp" % (os.getpid(), threading.get_ident()))
        if not tmp_summary.is_relative_to(usage_dir):
            raise ValueError("usage path escapes base directory")
        try:
            tmp_summary.write_text(json.dumps(summary, ensure_ascii=False,
                                              indent=2), encoding="utf-8")
            os.replace(tmp_summary, summary_path)
        except Exception:
            try:
                os.unlink(tmp_summary)
            except Exception:
                pass
            raise
    except Exception as exc:
        log("%s: %s" % (fail_label, exc))


def record_error(model, status, message, elapsed_ms=None):
    """Count a failed request and append it to the log so errors are visible."""
    row = {
        "at": time.time(),
        "iso": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "model": model,
        "error": True,
        "status": status,
        # 保留更完整的上游错误（provider_error 的内层 details 常在 200+ 字节）
        "message": str(message)[:400],
        "elapsed_ms": elapsed_ms,
    }
    with _lock:
        _usage["errors"] += 1
        if elapsed_ms is not None:
            _usage["wall_ms_sum"] += elapsed_ms
            _usage["wall_samples"] += 1
        summary = json.loads(json.dumps(_usage))
    _persist_usage(row, summary, "error persist failed")
    dur = " %dms" % elapsed_ms if elapsed_ms is not None else ""
    log("request error: model=%s%s status=%s msg=%s"
        % (model, dur, status, str(message)[:360]),
        level="ERROR", tag="chat")
    return row


def _pct(values, q):
    """Nearest-rank percentile (no interpolation) - good enough for latency."""
    if not values:
        return None
    ordered = sorted(values)
    idx = int(round((q / 100.0) * (len(ordered) - 1)))
    return ordered[max(0, min(len(ordered) - 1, idx))]


_perf_cache = {}
_perf_lock = threading.Lock()


def perf_stats(sample=5000, realm=None, ttl=10):
    """Cached wrapper: parsing thousands of rows is CPU-heavy, and the
    dashboard polls this endpoint every few seconds."""
    r = realm or CURRENT_REALM
    try:
        key = (int(sample), r)
    except Exception:
        key = (5000, r)
    now = time.time()
    with _perf_lock:
        hit = _perf_cache.get(key)
        if hit is not None and (now - hit[0]) < ttl:
            return hit[1]
    data = _perf_stats_uncached(sample, realm)
    with _perf_lock:
        _perf_cache[key] = (time.time(), data)
    return data


def _perf_stats_uncached(sample=5000, realm=None):
    """Latency percentiles + derived rates, computed from the JSONL log."""
    ttfts, gens, walls, rates, hits, tok_rates = [], [], [], [], [], []
    total = ok = err = 0
    m_buckets = {}
    # 只读日志末尾 sample 行：readlines() 会把整个日志读成字符串列表。
    rows = [raw.decode("utf-8", "replace") for raw in _tail_lines(USAGE_LOG, sample)]
    for line in rows:
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if realm and not row_matches_realm(r, realm):
            continue
        total += 1
        if r.get("error"):
            err += 1
            if r.get("elapsed_ms"):
                walls.append(r["elapsed_ms"])
            continue
        ok += 1
        if r.get("ttft_ms") is not None:
            ttfts.append(r["ttft_ms"])
        if r.get("gen_ms") is not None:
            gens.append(r["gen_ms"])
        if r.get("elapsed_ms") is not None:
            walls.append(r["elapsed_ms"])
        if r.get("tokens_per_sec"):
            tok_rates.append(r["tokens_per_sec"])
        if r.get("cache_hit_pct") is not None:
            hits.append(r["cache_hit_pct"])
        m_id = r.get("model") or "unknown"
        mb = m_buckets.setdefault(m_id, {"total": 0, "ok": 0, "err": 0,
                                         "ttfts": [], "gens": [], "walls": [],
                                         "tok_rates": [], "hits": []})
        mb["total"] += 1
        if r.get("error"):
            mb["err"] += 1
        else:
            mb["ok"] += 1
        if r.get("ttft_ms") is not None:
            mb["ttfts"].append(r["ttft_ms"])
        if r.get("gen_ms") is not None:
            mb["gens"].append(r["gen_ms"])
        if r.get("elapsed_ms") is not None:
            mb["walls"].append(r["elapsed_ms"])
        if r.get("tokens_per_sec"):
            mb["tok_rates"].append(r["tokens_per_sec"])
        if r.get("cache_hit_pct") is not None:
            mb["hits"].append(r["cache_hit_pct"])

    def block(vals):
        if not vals:
            return None
        return {
            "avg": round(sum(vals) / len(vals), 1),
            "p50": _pct(vals, 50),
            "p90": _pct(vals, 90),
            "p99": _pct(vals, 99),
            "max": max(vals),
            "samples": len(vals),
        }

    return {
        "sampled": total,
        "success": ok,
        "errors": err,
        "success_rate_pct": round(ok * 100.0 / total, 1) if total else None,
        "ttft_ms": block(ttfts),
        "generation_ms": block(gens),
        "wall_ms": block(walls),
        "tokens_per_sec": block(tok_rates),
        "cache_hit_pct": block(hits),
        "by_model": {
            mid: {
                "requests": mb["total"],
                "errors": mb["err"],
                "success_rate_pct": round(mb["ok"] * 100.0 / mb["total"], 1)
                if mb["total"] else None,
                "ttft_ms": block(mb["ttfts"]),
                "generation_ms": block(mb["gens"]),
                "wall_ms": block(mb["walls"]),
                "tokens_per_sec": block(mb["tok_rates"]),
                "cache_hit_pct": block(mb["hits"]),
            } for mid, mb in m_buckets.items()
        },
    }


_snap_cache = {}
_snap_lock = threading.Lock()


def usage_snapshot(realm=None, ttl=10):
    """Cached wrapper: the dashboard polls this every few seconds."""
    r = realm or CURRENT_REALM
    now = time.time()
    with _snap_lock:
        hit = _snap_cache.get(r)
        if hit is not None and (now - hit[0]) < ttl:
            return hit[1]
    data = _usage_snapshot_uncached(r)
    with _snap_lock:
        _snap_cache[r] = (time.time(), data)
    return data


def _usage_snapshot_uncached(realm=None):
    r = realm or CURRENT_REALM
    rep = POOL.representative(realm=r) if POOL else current_account()
    snap = _empty_stats()
    snap["started"] = _usage.get("started", time.time())
    try:
        with open(USAGE_LOG, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if r and not row_matches_realm(row, r):
                    continue
                if row.get("error"):
                    snap["errors"] += 1
                else:
                    snap["requests"] += 1
                    for k in USAGE_FIELDS:
                        if k in row:
                            snap[k] += (row[k] or 0)
                    m = row.get("model") or "unknown"
                    per = snap["by_model"].setdefault(
                        m, {"requests": 0, "accounts": {},
                            **{k: 0 for k in USAGE_FIELDS}})
                    per["requests"] += 1
                    for k in USAGE_FIELDS:
                        if k in row:
                            per[k] += (row[k] or 0)
                    acct_id = row.get("account")
                    if acct_id:
                        per.setdefault("accounts", {})
                        per["accounts"][acct_id] = per["accounts"].get(acct_id, 0) + 1
    except FileNotFoundError:
        pass
    except Exception as exc:
        log("usage snapshot read failed: %s" % exc)
    snap["since"] = time.strftime("%Y-%m-%d %H:%M:%S",
                                  time.localtime(snap.get("started", time.time())))
    snap["log_file"] = USAGE_LOG
    snap["realm"] = r
    snap["accounts_map"] = {a.uid: {"nickname": a.nickname, "realm": a.realm}
                            for a in POOL.accounts} if POOL else {}
    snap["account"] = {
        "uid": (rep.uid if rep else ""),
        "domain": (rep.domain if rep else ""),
        "issuer": ("qoder" if rep else ""),
        "credential_file": (os.path.basename(rep.path) if rep and rep.path else ""),
        "expires_at": (rep.expires_at if rep else 0),
        "accounts": (len(POOL.accounts) if POOL else 0),
        "accounts_ready": (POOL.count_ready() if POOL else 0),
    }
    return snap


def _tail_lines(path, max_lines, chunk=256 * 1024):
    """Return up to the last `max_lines` non-empty lines, oldest first.

    The usage log passes 20MB within a day. Scanning it end to end on every
    dashboard poll was the dominant cost behind slow /usage/* responses.
    """
    lines = []
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            pos = fh.tell()
            buf = b""
            while pos > 0 and len(lines) < max_lines:
                step = min(chunk, pos)
                pos -= step
                fh.seek(pos)
                buf = fh.read(step) + buf
                parts = buf.split(b"\n")
                buf = parts[0]
                for raw in reversed(parts[1:]):
                    if not raw.strip():
                        continue
                    lines.append(raw)
                    if len(lines) >= max_lines:
                        break
            if len(lines) < max_lines and buf.strip():
                lines.append(buf)
    except FileNotFoundError:
        return []
    except Exception as exc:
        log("tail read failed: %s" % exc)
        return []
    lines.reverse()
    return lines


def count_usage_rows(realm=None):
    """Cheap row count - substring match instead of a full JSON parse."""
    needles = ()
    if realm:
        needles = ('"realm": "%s"' % realm, '"realm":"%s"' % realm)
    n = 0
    try:
        with open(USAGE_LOG, encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                if not needles:
                    n += 1
                    continue
                if any(x in line for x in needles):
                    n += 1
                    continue
                if '"realm"' in line:
                    continue
                try:
                    if row_matches_realm(json.loads(line), realm):
                        n += 1
                except Exception:
                    pass
    except FileNotFoundError:
        pass
    except Exception:
        pass
    return n


def recent_usage(limit=100, realm=None, page=1):
    """Paginated rows from the tail of the log (page 1 is latest)."""
    try:
        limit = max(1, int(limit))
    except Exception:
        limit = 100
    try:
        page = max(1, int(page))
    except Exception:
        page = 1
    total = count_usage_rows(realm)
    total_pages = max(1, (total + limit - 1) // limit) if total > 0 else 1
    page = min(page, total_pages)
    target_count = page * limit
    matching = []
    chunk = 256 * 1024
    try:
        with open(USAGE_LOG, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            pos = fh.tell()
            buf = b""
            while pos > 0 and len(matching) < target_count:
                step = min(chunk, pos)
                pos -= step
                fh.seek(pos)
                buf = fh.read(step) + buf
                parts = buf.split(b"\n")
                buf = parts[0]
                for raw in reversed(parts[1:]):
                    st = raw.strip()
                    if not st:
                        continue
                    try:
                        item = json.loads(st.decode("utf-8", "replace"))
                    except Exception:
                        continue
                    if realm and not row_matches_realm(item, realm):
                        continue
                    matching.append(item)
                    if len(matching) >= target_count:
                        break
            if len(matching) < target_count and buf.strip():
                try:
                    item = json.loads(buf.strip().decode("utf-8", "replace"))
                    if not realm or row_matches_realm(item, realm):
                        matching.append(item)
                except Exception:
                    pass
    except FileNotFoundError:
        pass
    except Exception as exc:
        log("recent_usage read failed: %s" % exc)
    start_idx = (page - 1) * limit
    end_idx = start_idx + limit
    page_rows = matching[start_idx:end_idx]
    return {
        "total": total,
        "page": page,
        "limit": limit,
        "total_pages": total_pages,
        "rows": page_rows,
    }


POOL = None
SCHEDULER = None
ACCOUNTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "accounts")
REALM_STATE_FILE = os.path.join(ACCOUNTS_DIR, "active_realm.json")


def load_persisted_realm():
    global CURRENT_REALM
    if os.path.isfile(REALM_STATE_FILE):
        try:
            with open(REALM_STATE_FILE, "r", encoding="utf-8") as fh:
                d = json.load(fh)
                r = d.get("realm")
                if r in ("intl", "cn"):
                    CURRENT_REALM = r
                    return CURRENT_REALM
        except Exception as e:
            log("could not load active realm: %s" % e)
    return CURRENT_REALM


def save_persisted_realm(realm):
    global CURRENT_REALM
    if realm in ("intl", "cn"):
        CURRENT_REALM = realm
        try:
            state_dir = Path(ACCOUNTS_DIR).resolve()
            state_dir.mkdir(parents=True, exist_ok=True)
            state_file = state_dir / os.path.basename(REALM_STATE_FILE)
            if not state_file.is_relative_to(state_dir):
                raise ValueError("realm state path escapes base directory")
            state_file.write_text(
                json.dumps({"realm": realm, "updated_at": time.time(),
                            "updated_iso": time.strftime("%Y-%m-%d %H:%M:%S")},
                           indent=2),
                encoding="utf-8")
            log("persisted active realm '%s' to disk" % realm)
        except Exception as exc:
            log("failed to persist active realm: %s" % exc)
    return CURRENT_REALM


API_KEY = None
SYSTEM_PROMPT = DEFAULT_SYSTEM_PROMPT


def account_views(realm=None):
    """List view of every account, including a live readiness flag."""
    if not POOL:
        return []
    return POOL.list_public(realm=realm)


_byacct_cache = {"at": 0.0, "data": None}
_byacct_lock = threading.Lock()


def usage_by_account(ttl=10):
    """Cached wrapper: full aggregation over the whole log is expensive."""
    now = time.time()
    with _byacct_lock:
        if _byacct_cache["data"] is not None and (now - _byacct_cache["at"]) < ttl:
            return _byacct_cache["data"]
    data = _usage_by_account_uncached()
    with _byacct_lock:
        _byacct_cache["at"] = time.time()
        _byacct_cache["data"] = data
    return data


def _usage_by_account_uncached():
    """Aggregate the JSONL log per account id."""
    buckets = {}
    try:
        with open(USAGE_LOG, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if row.get("error"):
                    continue
                key = row.get("account") or "(unattributed)"
                bucket = buckets.setdefault(key, {
                    "account": key, "requests": 0, "prompt_tokens": 0,
                    "completion_tokens": 0, "reasoning_tokens": 0,
                    "cached_tokens": 0, "total_tokens": 0, "models": {},
                })
                bucket["requests"] += 1
                for field in ("prompt_tokens", "completion_tokens",
                              "reasoning_tokens", "cached_tokens",
                              "total_tokens"):
                    bucket[field] += row.get(field) or 0
                model = row.get("model") or "?"
                bucket["models"][model] = bucket["models"].get(model, 0) + 1
    except FileNotFoundError:
        pass
    except Exception as exc:
        log("usage_by_account failed: %s" % exc)
    out = sorted(buckets.values(), key=lambda b: -b["total_tokens"])
    for item in out:
        item["models"] = sorted(item["models"].items(), key=lambda kv: -kv[1])[:5]
    return out


def compute_usage_analytics():
    """Detailed analytics for Token, Cache, and Reasoning metrics page."""
    now = time.localtime()
    today_ts = time.mktime((now.tm_year, now.tm_mon, now.tm_mday, 0, 0, 0, 0, 0, -1))

    def new_stat():
        return {
            "requests": 0, "errors": 0,
            "prompt_tokens": 0, "completion_tokens": 0,
            "reasoning_tokens": 0, "cached_tokens": 0, "total_tokens": 0,
            "ttft_sum": 0.0, "ttft_n": 0,
            "speed_sum": 0.0, "speed_n": 0,
            "elapsed_sum": 0.0, "elapsed_n": 0,
        }

    all_summary = new_stat()
    today_summary = new_stat()
    acct_map = {}
    model_map = {}
    if os.path.exists(USAGE_LOG):
        try:
            with open(USAGE_LOG, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        r = json.loads(line)
                    except Exception:
                        continue
                    is_err = bool(r.get("error"))
                    at = r.get("at", 0)
                    is_today = (at >= today_ts)
                    acct_uid = r.get("account") or "(unattributed)"
                    m_id = r.get("model") or "(unknown)"

                    def feed(stat_obj, is_error):
                        if is_error:
                            stat_obj["errors"] += 1
                        else:
                            stat_obj["requests"] += 1
                            stat_obj["prompt_tokens"] += (r.get("prompt_tokens") or 0)
                            stat_obj["completion_tokens"] += (r.get("completion_tokens") or 0)
                            stat_obj["reasoning_tokens"] += (r.get("reasoning_tokens") or 0)
                            stat_obj["cached_tokens"] += (r.get("cached_tokens") or 0)
                            stat_obj["total_tokens"] += (r.get("total_tokens") or 0)
                            if r.get("ttft_ms"):
                                stat_obj["ttft_sum"] += r["ttft_ms"]
                                stat_obj["ttft_n"] += 1
                            if r.get("tokens_per_sec"):
                                stat_obj["speed_sum"] += r["tokens_per_sec"]
                                stat_obj["speed_n"] += 1
                            if r.get("elapsed_ms"):
                                stat_obj["elapsed_sum"] += r["elapsed_ms"]
                                stat_obj["elapsed_n"] += 1

                    feed(all_summary, is_err)
                    if is_today:
                        feed(today_summary, is_err)
                    if acct_uid not in acct_map:
                        acct_map[acct_uid] = {
                            "uid": acct_uid,
                            "nickname": acct_uid,
                            "realm": r.get("realm", ""),
                            "domain": "",
                            "today": new_stat(),
                            "all_time": new_stat(),
                            "today_models": {},
                            "all_models": {},
                        }
                    feed(acct_map[acct_uid]["all_time"], is_err)
                    if is_today:
                        feed(acct_map[acct_uid]["today"], is_err)
                    if not is_err:
                        tm = acct_map[acct_uid]["all_models"].setdefault(
                            m_id, {"requests": 0, "tokens": 0, "reasoning": 0})
                        tm["requests"] += 1
                        tm["tokens"] += (r.get("total_tokens") or 0)
                        tm["reasoning"] += (r.get("reasoning_tokens") or 0)
                        if is_today:
                            tdm = acct_map[acct_uid]["today_models"].setdefault(
                                m_id, {"requests": 0, "tokens": 0, "reasoning": 0})
                            tdm["requests"] += 1
                            tdm["tokens"] += (r.get("total_tokens") or 0)
                            tdm["reasoning"] += (r.get("reasoning_tokens") or 0)
                    if m_id not in model_map:
                        model_map[m_id] = {"model": m_id, "today": new_stat(),
                                           "all_time": new_stat()}
                    feed(model_map[m_id]["all_time"], is_err)
                    if is_today:
                        feed(model_map[m_id]["today"], is_err)
        except Exception as exc:
            log("compute_usage_analytics failed: %s" % exc)
    if POOL:
        for a in POOL.accounts:
            if a.uid in acct_map:
                acct_map[a.uid]["nickname"] = a.nickname
                acct_map[a.uid]["realm"] = a.realm
                acct_map[a.uid]["domain"] = a.domain
                acct_map[a.uid]["credits"] = getattr(a, "credits", None) or {}
            else:
                acct_map[a.uid] = {
                    "uid": a.uid,
                    "nickname": a.nickname,
                    "realm": a.realm,
                    "domain": a.domain,
                    "credits": getattr(a, "credits", None) or {},
                    "today": new_stat(),
                    "all_time": new_stat(),
                    "today_models": {},
                    "all_models": {},
                }

    def finalize(stat_obj):
        p = stat_obj["prompt_tokens"]
        c = stat_obj["cached_tokens"]
        out = stat_obj["completion_tokens"]
        reas = stat_obj["reasoning_tokens"]
        stat_obj["cache_hit_pct"] = round((c / p * 100), 1) if p > 0 else 0.0
        stat_obj["reasoning_ratio"] = round((reas / out * 100), 1) if out > 0 else 0.0
        stat_obj["ttft_ms_avg"] = round(stat_obj["ttft_sum"] / stat_obj["ttft_n"]) \
            if stat_obj["ttft_n"] > 0 else 0
        stat_obj["speed_avg"] = round(stat_obj["speed_sum"] / stat_obj["speed_n"], 1) \
            if stat_obj["speed_n"] > 0 else 0.0
        stat_obj["elapsed_ms_avg"] = round(stat_obj["elapsed_sum"] / stat_obj["elapsed_n"]) \
            if stat_obj["elapsed_n"] > 0 else 0
        return stat_obj

    finalize(all_summary)
    finalize(today_summary)
    for a in acct_map.values():
        finalize(a["today"])
        finalize(a["all_time"])
    for m in model_map.values():
        finalize(m["today"])
        finalize(m["all_time"])
    accts_list = sorted(acct_map.values(),
                        key=lambda a: (-a["today"]["total_tokens"],
                                       -a["all_time"]["total_tokens"]))
    models_list = sorted(model_map.values(),
                         key=lambda m: (-m["today"]["total_tokens"],
                                        -m["all_time"]["total_tokens"]))
    return {
        "today_ts": today_ts,
        "summary": {"today": today_summary, "all_time": all_summary},
        "accounts": accts_list,
        "models": models_list,
    }


def runtime_settings_view():
    """Current panel-visible settings (never returns the password or the key)."""
    key = API_KEY or ""
    if len(key) > 8:
        masked = key[:4] + "*" * 6 + key[-4:]
    else:
        masked = "*" * len(key)
    keys = []
    for entry in configured_keys():
        raw = entry.get("key") or ""
        keys.append({
            "id": entry.get("id") or "",
            "name": entry.get("name") or "",
            "realm": entry.get("realm") or "",
            "enabled": entry.get("enabled", True) is not False,
            "masked": (raw[:4] + "*" * 6 + raw[-4:]) if len(raw) > 8
            else "*" * len(raw),
            "source": entry.get("source") or "panel",
            "created_at": entry.get("created_at") or "",
        })
    return {
        "panel_password_is_default":
            qoder_settings.panel_password_is_default(ACCOUNTS_DIR),
        "api_key_set": bool(key),
        "api_key_set_by_panel": API_KEY_FILE_SET,
        "api_key_masked": masked,
        "auth_required": auth_required(),
        "api_keys": keys,
        "accounts_dir": ACCOUNTS_DIR,
        "usage_dir": USAGE_DIR,
        "settings_file": qoder_settings.settings_path(ACCOUNTS_DIR),
        "version": VERSION,
    }


def current_account():
    """Account used for display purposes (health / usage summaries)."""
    return POOL.representative() if POOL else None


# ---------------------------------------------------------------------------
# 前缀会话亲和（同一对话固定同一账号，命中上游按账号的 prompt 缓存）
# ---------------------------------------------------------------------------
AFFINITY_BY_PREFIX = os.environ.get("QD_AFFINITY_BY_PREFIX", "1").lower() not in (
    "0", "false", "no", "off")
AFFINITY_DEBUG = os.environ.get("QD_AFFINITY_DEBUG", "0").lower() in (
    "1", "true", "yes", "on")


def derive_affinity_key(messages):
    """Derive a stable affinity key from a conversation's stable prefix.

    前两条消息（system + 首轮 user）在整段对话生命周期内不变，因此同一对话
    每一轮都落到同一上游账号 —— 正是 prompt 缓存需要的；不同对话首轮不同，
    依旧分散到各账号，负载均衡不受影响。
    """
    if not AFFINITY_BY_PREFIX:
        return None
    try:
        msgs = messages or []
        if not msgs:
            return None
        head = msgs[:2]
        blob = json.dumps(head, ensure_ascii=False,
                          sort_keys=True).encode("utf-8")
        return "pfx-" + hashlib.sha256(blob).hexdigest()[:16]
    except Exception:
        return None


def prompt_fingerprint(messages):
    """Privacy-safe fingerprint of the outgoing prompt.

    Cache hits need a byte-identical prefix, so these hashes answer "is my
    prefix stable / is my conversation continuous?" without storing any text.
    """
    try:
        def h(obj):
            blob = json.dumps(obj, ensure_ascii=False,
                              sort_keys=True).encode("utf-8")
            return hashlib.sha256(blob).hexdigest()[:12]
        msgs = messages or []
        out = {"msgs_sha": h(msgs), "n_msgs": len(msgs)}
        if msgs:
            out["system_sha"] = h(msgs[0]) if msgs[0].get("role") == "system" else ""
            out["prefix_sha"] = h(msgs[:-1]) if len(msgs) > 1 else ""
        return out
    except Exception:
        return {}


LOG_BUFFER = deque(maxlen=2000)
_LOG_LOCK = threading.Lock()
_LOG_COUNTER = 0


def add_log_entry(msg, level=None, tag=None):
    global _LOG_COUNTER
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    t_short = time.strftime("%H:%M:%S")
    msg_str = str(msg).rstrip()
    if not level:
        lower = msg_str.lower()
        if any(k in lower for k in ("error", "exception", "failed", "traceback",
                                    "errno", "fatal", "token_expire", "12153")):
            level = "ERROR"
        elif any(k in lower for k in ("warn", "warning", "retry", "timeout")):
            level = "WARN"
        else:
            level = "INFO"
    if not tag:
        lower = msg_str.lower()
        if "chat:" in lower or "chat done" in lower or "/v1/chat" in lower \
                or "/chat/completions" in lower or "responses" in lower:
            tag = "chat"
        elif "scheduler" in lower or "调度器" in lower:
            tag = "scheduler"
        elif "task" in lower or "任务" in lower or "签到" in lower \
                or "checkin" in lower or "福利" in lower:
            tag = "tasks"
        elif "account" in lower or "账号" in lower or "pool" in lower \
                or "imported" in lower:
            tag = "accounts"
        elif "model" in lower or "catalog" in lower or "模型" in lower:
            tag = "catalog"
        elif "auth" in lower or "token" in lower or "oauth" in lower:
            tag = "auth"
        elif "settings" in lower or "设置" in lower:
            tag = "settings"
        else:
            tag = "system"
    with _LOG_LOCK:
        _LOG_COUNTER += 1
        entry = {
            "id": _LOG_COUNTER,
            "ts": ts,
            "time": t_short,
            "level": level,
            "tag": tag,
            "msg": msg_str,
        }
        LOG_BUFFER.append(entry)
    return entry


def log(msg, level=None, tag=None):
    sys.stderr.write("[qd-proxy] %s %s\n" % (time.strftime("%H:%M:%S"), msg))
    sys.stderr.flush()
    add_log_entry(msg, level=level, tag=tag)


def get_logs(limit=200, level="", tag="", search="", since_id=0):
    with _LOG_LOCK:
        items = list(LOG_BUFFER)
    if since_id > 0:
        items = [x for x in items if x["id"] > since_id]
    if level:
        items = [x for x in items if x["level"] == level.upper()]
    if tag:
        items = [x for x in items if x["tag"].lower() == tag.lower()]
    if search:
        s = search.lower()
        items = [x for x in items if s in x["msg"].lower() or s in x["tag"].lower()]
    total = len(items)
    if limit and limit > 0 and since_id == 0:
        items = items[-limit:]
    max_id = items[-1]["id"] if items else since_id
    return {"total": total, "logs": items, "max_id": max_id}


def clear_logs():
    with _LOG_LOCK:
        LOG_BUFFER.clear()


# ---------------------------------------------------------------------------
# 模型目录（动态 COSY model/list + 静态兜底）
# ---------------------------------------------------------------------------
NON_CHAT_MODELS = {"lite"}
NON_CHAT_PREFIXES = ("codewise-", "completion-")
NON_CHAT_SUFFIXES = ("-image-alpha", "-image-alpha-edit", "-taco-completion")


def is_chat_model(mid):
    if not mid:
        return False
    if mid in NON_CHAT_MODELS:
        return False
    if mid.startswith(NON_CHAT_PREFIXES):
        return False
    if mid.endswith(NON_CHAT_SUFFIXES):
        return False
    return True


CN_UI_ORDER = [m["key"] for m in qoder_catalog.STATIC_CN_MODELS]
INTL_UI_ORDER = [m["key"] for m in qoder_catalog.STATIC_INTL_MODELS]


def merge_catalog(primary, realm=None):
    """静态目录打底（补元数据），按官方 UI 顺序输出。

    **清单以 primary（动态接口或本机官方目录）为准**：桌面版此刻显示什么
    这里就显示什么（如动态返回 15 条就不额外塞静态独有的 2 条）；
    primary 为空时回退静态快照全量。enable/strategies 等状态不做任何裁剪。
    """
    r = realm or CURRENT_REALM
    merged = {}
    source_static = getattr(qoder_catalog, "STATIC_CN_MODELS" if r == "cn"
                            else "STATIC_INTL_MODELS", qoder_catalog.STATIC_MODELS)
    for item in source_static:
        mid = item.get("key") or item.get("id")
        if mid and is_chat_model(mid):
            merged[mid] = dict(item)
    primary = list(primary or [])
    primary_keys = None
    for mid, meta in primary:
        if not is_chat_model(mid):
            continue
        if meta:
            base = merged.get(mid) or {}
            # None 值不覆盖：动态接口对部分条目返回 null（如 context_config/
            # thinking_config/description），静默 null 会把静态快照里的真实
            # 值抹掉——只用有值字段做增量覆盖。
            base.update({k: v for k, v in meta.items() if v is not None})
            merged[mid] = base
        elif mid not in merged:
            merged[mid] = {}
    if primary:
        primary_keys = {mid for mid, _ in primary if is_chat_model(mid)}
    order = CN_UI_ORDER if r == "cn" else INTL_UI_ORDER
    out = []
    seen = set()

    def wanted(mid):
        if not is_chat_model(mid):
            return False
        if primary_keys is not None and mid not in primary_keys:
            return False      # 静态独有、官方此刻未列出的条目不外塞
        return True

    for mid in order:
        if mid in merged and wanted(mid):
            out.append((mid, merged[mid]))
            seen.add(mid)
    for mid, meta in merged.items():
        if mid not in seen and wanted(mid):
            out.append((mid, meta))
    return out


def read_local_models(realm=None):
    """读取本机官方客户端的模型目录缓存（QMC 解密），无网络也能跟官方对齐。

    路径：~/.qoder/.models/<uid>/catalog-v6（intl）/ ~/.qoder-cn/...（cn）
    返回 [(key, meta)]；任何失败返回 []（回退静态快照）。
    """
    r = realm or CURRENT_REALM
    home = os.path.join(os.path.expanduser("~"),
                        get_realm_config(r)["home_dir"], ".models")
    try:
        default_path = os.path.join(home, "default")
        uid = ""
        with open(default_path, encoding="utf-8") as fh:
            uid = str(json.load(fh).get("uid") or "")
        subdirs = [uid] if uid and os.path.isdir(os.path.join(home, uid)) else \
            [d for d in os.listdir(home) if os.path.isdir(os.path.join(home, d))]
        for sub in subdirs:
            cat = os.path.join(home, sub, "catalog-v6")
            if not os.path.isfile(cat):
                continue
            with open(cat, "rb") as fh:
                blob = fh.read().decode("ascii", "replace")
            from qoder_sign import qmc_decrypt
            plain = json.loads(qmc_decrypt(blob, sub).decode("utf-8"))
            chat = plain.get("chat") or []
            out = []
            for m in chat:
                if not isinstance(m, dict) or not m.get("key"):
                    continue
                # 官方本地目录条目全字段原样
                row = dict(m)
                row["id"] = m["key"]
                row.setdefault("name", m.get("display_name") or m["key"])
                row.setdefault("display_name", m.get("display_name") or m["key"])
                out.append((m["key"], row))
            if out:
                log("model catalog read from local client cache: %d models (%s)"
                    % (len(out), r))
                return out
    except Exception as exc:
        log("local model catalog read skipped: %s" % exc)
    return []


def fetch_models(realm=None):
    r = realm or CURRENT_REALM
    with _lock:
        c = _models_cache.get(r) or {"at": 0.0, "data": None}
        if c["data"] and time.time() - c["at"] < 300:
            return c["data"]
    # 1) 动态接口（需要账号） 2) 本机官方目录缓存 3) 内嵌静态快照
    live = read_dynamic_models(realm=r)
    if not live:
        live = read_local_models(realm=r)
    entries = merge_catalog(live, realm=r)
    with _lock:
        _models_cache[r] = {"at": time.time(), "data": entries}
    return entries


def read_dynamic_models(realm=None):
    """COSY 签名拉取 /algo/api/v2/model/list（chat scene）。失败返回 []。

    注意：签名的 body 是 qoder_encode("{}")，请求必须**带同款 body**发出
    （服务端校验签名与 body 一致，裸 GET 会 403）。

    主机按官方的候选顺序尝试（国际版 api1 → api2 → api3）：切换主机不影响
    签名（签名只覆盖 path），单个域名故障不再导致模型清单整体拉取失败。
    """
    r = realm or CURRENT_REALM
    account = POOL.pick(realm=r) if POOL else None
    if account is None:
        return []
    # 签名以 qoder_encode("{}") 作为 body 参与 MD5；请求同样携带该 body。
    sign_body = qoder_encode(b"{}")
    payload = None
    for host in gateway_candidates(r):
        raw_url = host + MODELS_PATH
        try:
            sess = SESSIONS.get(account)
            headers = sess.headers(sign_body, raw_url, model_key="", sse=False,
                                   accept="application/json")
            headers["User-Agent"] = CLIENT_UA
            url = validate_public_http_url(raw_url)
            req = urllib.request.Request(url, data=sign_body.encode("utf-8"),
                                         method="GET", headers=headers)
            with urllib.request.urlopen(req, timeout=15) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            break
        except Exception as exc:
            log("model discovery failed on %s: %s" % (host, exc))
    if payload is None:
        return []
    chat = payload.get("chat") or []
    out = []
    for m in chat:
        if not isinstance(m, dict):
            continue
        mid = m.get("key")
        if not mid:
            continue
        # 官方原始条目**全字段原样**透传（enable/strategies/is_editable/
        # minimal_version/... 一律不裁剪），仅补 id/name 兼容键。
        row = dict(m)
        row["id"] = mid
        row.setdefault("name", m.get("display_name") or mid)
        row.setdefault("display_name", m.get("display_name") or mid)
        out.append((mid, row))
    if out:
        log("model discovery ok: %d chat models from %s" % (len(out), r))
    return out


def _parse_hhmm(value):
    """'22:00' -> 1320 分钟数；解析失败返回 None。"""
    try:
        parts = str(value).split(":")
        return int(parts[0]) * 60 + int(parts[1])
    except Exception:
        return None


def off_peak_active_now(window_start, window_end, tz=None, now=None):
    """判断当前时刻是否处于官方低谷（Off-Peak）时段。

    官方 promotion.window 为 22:00-08:00（跨午夜），文案标注 UTC+8；
    官方时区 Asia/Shanghai / Asia/Singapore 均为 UTC+8，因此按固定 +8
    计算（无 tzdata 依赖），未知时区也按官方标注的 UTC+8 兜底。
    返回 True（窗口内）/ False（窗口外）/ None（无有效窗口）。
    """
    s = _parse_hhmm(window_start)
    e = _parse_hhmm(window_end)
    if s is None or e is None:
        return None
    if now is None:
        now = time.time()
    import datetime
    offset_hours = 8
    if isinstance(tz, str):
        if tz.upper().startswith("UTC"):
            m = re.search(r"UTC([+-]\d{1,2})", tz.upper())
            if m:
                offset_hours = int(m.group(1))
        # Asia/Shanghai / Asia/Singapore -> +8（默认即 8）
    utc = datetime.datetime.fromtimestamp(now, datetime.timezone.utc)
    local = utc + datetime.timedelta(hours=offset_hours)
    cur = local.hour * 60 + local.minute
    if s == e:
        return True
    if s < e:
        return s <= cur < e
    return cur >= s or cur < e      # 跨午夜窗口（22:00-08:00）


def model_entry(mid, meta):
    """Build a rich /v1/models entry.

    - `id` = **官方模型名 display_name**（如 `Qwen3.8-Max`）——客户端唯一需要
      填的值，与官方桌面版选择器显示一致；`upstream_key` 保留缩写 key，
      `aliases` 列出所有可接受形式（key / 「key (Name)」/ 人类别名）。
    - `description` = 官方桌面版介绍文案（dynamic-text zh.detail）。
    - `name_local` = 官方本地化名（zh.label 与 display_name 不同时，如
      Ultimate -> 极致、旧文案 Kimi-K2.7-Code）。
    - 上下文窗口来自官方 context_config；思考档位来自 thinking_config。
    - 峰谷价来自 promotion（peak -> valley + 时段窗口与官方错峰文案）。
    - `enabled=false` 时给出 `disabled_reason`（官方原文：需要升级或购买
      千问官方套餐开放）与上游 `disabled_message_key`。
    - **最大输出**：官方（catalog / 动态接口原始响应）均无此字段，因此
      仅当来源真实携带时才输出，绝不编造。
    """
    meta = meta or {}
    display_name = meta.get("name") or meta.get("display_name") or mid
    item = {
        "id": display_name,
        "object": "model",
        "created": int(time.time()),
        "owned_by": "qoder",
        "upstream_key": mid,
        "enabled": meta.get("enable", True) is not False,
    }
    item["name"] = display_name
    # 描述：来源自带动态/本地 description，否则取官方桌面版文案
    desc = meta.get("description") or ""
    if not desc:
        try:
            desc = qoder_catalog.official_description(mid)
        except Exception:
            desc = ""
    if desc:
        item["description"] = desc
    # 官方本地化显示名（与 display_name 不同时透出）
    try:
        local = qoder_catalog.official_local_name(mid)
    except Exception:
        local = ""
    if local:
        item["name_local"] = local
    # 所有可接受的填写形式
    aliases = [mid, "%s (%s)" % (mid, display_name)] if display_name != mid else [mid]
    for ak, av in qoder_catalog.MODEL_ALIASES.items():
        if av == mid and ak not in aliases:
            aliases.append(ak)
    item["aliases"] = aliases
    # 禁用态：官方原因文案（未开通千问套餐时官方桌面版所示）
    if not item["enabled"]:
        item["disabled_reason"] = getattr(
            qoder_catalog, "OFFICIAL_DISABLED_REASON",
            "需要升级或购买千问官方套餐开放")
        strategies = meta.get("strategies")
        if isinstance(strategies, list):
            for st in strategies:
                if isinstance(st, dict) and st.get("disabled_message_key"):
                    item["disabled_message_key"] = st["disabled_message_key"]
                    break
            item["strategies"] = strategies
    vision = bool(meta.get("is_vl") or meta.get("supportsImages")) \
        and not meta.get("disabledMultimodal")
    tools = bool(meta.get("supportsToolCall", True))
    thinking_cfg = meta.get("thinking_config") if isinstance(
        meta.get("thinking_config"), dict) else {}
    thinks = bool(meta.get("is_reasoning") or meta.get("supportsReasoning")
                  or isinstance(thinking_cfg.get("enabled"), dict))
    inputs = ["text"] + (["image"] if vision else [])
    item["capabilities"] = {"vision": vision, "tool_calls": tools,
                            "reasoning": thinks}
    item["supports_vision"] = vision
    item["supports_images"] = vision
    item["supports_tool_calls"] = tools
    item["supports_reasoning"] = thinks
    item["vision"] = vision
    item["multimodal"] = vision
    item["abilities"] = {"vision": vision, "functionCall": tools,
                         "function_call": tools, "reasoning": thinks}
    item["input_modalities"] = inputs
    item["output_modalities"] = ["text"]
    item["modalities"] = {"input": inputs, "output": ["text"]}
    item["architecture"] = {
        "input_modalities": inputs,
        "output_modalities": ["text"],
        "modality": "+".join(inputs) + "->text",
    }
    # ---- limits：官方 context_config 多窗口 + 默认输入上限 ----
    max_in = meta.get("maxInputTokens") or meta.get("max_input_tokens")
    ctx_cfg = meta.get("context_config") if isinstance(
        meta.get("context_config"), dict) else {}
    windows, labels, default_label = [], [], ""
    for label, conf in ctx_cfg.items():
        if not isinstance(conf, dict):
            continue
        tok = conf.get("token_count")
        if tok:
            labels.append(str(label))
            windows.append(tok)
            if conf.get("is_default"):
                default_label = str(label)
    if not default_label and labels:
        default_label = labels[0]
    if max_in:
        item["context_length"] = max_in
        item["max_input_tokens"] = max_in
    if windows:
        item["context_windows"] = windows
        item["context_window_labels"] = labels
        item["context_window_default"] = default_label
    # 最大输出：官方（catalog/动态接口）均无此字段——不输出、不展示（用户
    # 已确认不再测量该值）。
    max_out = meta.get("maxOutputTokens") or meta.get("max_output_tokens")
    if max_out:
        item["max_output_tokens"] = max_out
        item["max_completion_tokens"] = max_out
    # ---- 思考档位：官方 thinking_config ----
    enabled_think = thinking_cfg.get("enabled")
    if isinstance(enabled_think, dict):
        order = ["low", "medium", "high", "xhigh", "max"]
        eff_map = enabled_think.get("efforts") or {}
        efforts = [e for e in order if e in eff_map]
        if efforts:
            item["reasoning_efforts"] = efforts
        for e in efforts:
            if isinstance(eff_map.get(e), dict) and eff_map[e].get("is_default"):
                item["reasoning_default_effort"] = e
                break
    if "disabled" in thinking_cfg:
        item["reasoning_can_disable"] = True
    # ---- 兼容旧 catalog 形态 ----
    effort_fixed = (meta.get("reasoning") or {}).get("effort")
    if effort_fixed:
        item["reasoning_fixed_effort"] = effort_fixed
    efforts_old = (meta.get("reasoning") or {}).get("supportedEfforts")
    if efforts_old and "reasoning_efforts" not in item:
        item["reasoning_efforts"] = efforts_old
    if (meta.get("reasoning") or {}).get("defaultEffort") \
            and "reasoning_default_effort" not in item:
        item["reasoning_default_effort"] = meta["reasoning"]["defaultEffort"]
    # ---- 计费：峰谷价（官方 promotion） ----
    price_now = meta.get("price_factor")
    promo = meta.get("promotion") if isinstance(meta.get("promotion"), dict) else {}
    if price_now is not None:
        item["price_factor"] = price_now
    peak = None
    if promo.get("before_promotion_price_factor") is not None:
        peak = promo.get("before_promotion_price_factor")
    elif meta.get("original_price_factor") is not None:
        peak = meta.get("original_price_factor")
    if peak is not None:
        item["price_factor_peak"] = peak
    # 低谷价 = 峰值 × 折扣（官方 promotion 的定义，与快照抓取时刻无关）。
    # 旧实现直接把"当前 price_factor"当成低谷价：快照若在非低谷时段抓取，
    # 报出的低谷价就会等于峰价（看板/客户端据此算错费用）。
    discount = promo.get("discount_factor")
    valley = None
    try:
        if peak is not None and discount and 0 < float(discount) <= 1:
            valley = round(float(peak) * float(discount), 4)
    except (TypeError, ValueError):
        valley = None
    if valley is None and price_now is not None:
        valley = price_now
    if valley is not None:
        item["price_factor_valley"] = valley
    # 只要官方给了 promotion 就下发峰谷元数据（不限于"此刻正在打折"）：
    # 高峰期抓取的快照里 promo.active=False，旧实现会整块跳过，客户端于是
    # 既看不到低谷窗口、也看不到峰价。active 如实透传官方值，"现在是否处于
    # 低谷"另由 off_peak_active_now（按本地时间复算）给出。
    if promo:
        badge = promo.get("badge") or {}
        desc_p = promo.get("description") or {}
        link = promo.get("link_url") or {}
        item["off_peak"] = {
            "active": bool(promo.get("active")),
            "window_start": promo.get("window_start"),
            "window_end": promo.get("window_end"),
            "timezone": promo.get("timezone"),
            "discount_factor": promo.get("discount_factor"),
            "badge": badge.get("zh") or badge.get("en") or "",
            "badge_en": badge.get("en") or "",
            "description": desc_p.get("zh") or desc_p.get("en") or "",
            "description_en": desc_p.get("en") or "",
            "link": link.get("zh") or link.get("en") or "",
        }
        item["off_peak_window"] = "%s-%s" % (promo.get("window_start"),
                                             promo.get("window_end"))
        item["promotion"] = promo
        # 当前是否正处于低谷时段（供看板做视觉高亮；前端亦会按本地时间复算）
        active = off_peak_active_now(promo.get("window_start"),
                                     promo.get("window_end"),
                                     tz=promo.get("timezone"))
        item["off_peak_active_now"] = bool(active)
    if meta.get("original_price_factor") is not None:
        item["original_price_factor"] = meta.get("original_price_factor")
    if meta.get("is_free") is not None:
        item["is_free"] = bool(meta.get("is_free"))
    if meta.get("is_new"):
        item["is_new"] = True
    if meta.get("icon"):
        item["icon"] = meta.get("icon")
    if meta.get("credits"):
        item["credits"] = meta["credits"]
    return item


# ---------------------------------------------------------------------------
# 请求清洗与归一化
# ---------------------------------------------------------------------------
def strip_data_prefix(line):
    line = line.strip()
    if not line or line.startswith(":"):
        return ""
    while line.startswith("data:"):
        line = line[5:].strip()
    if not line or line.startswith(":"):
        return ""
    return line


def clean_chunk(raw):
    """Drop the empty noise fields the gateway pads deltas with."""
    try:
        obj = json.loads(raw)
    except Exception:
        return raw
    changed = False
    for choice in obj.get("choices") or []:
        delta = choice.get("delta")
        if not isinstance(delta, dict):
            continue
        fc = delta.get("function_call")
        if fc is not None:
            fc_empty = (not fc) if not isinstance(fc, dict) else (not fc.get("name"))
            if fc_empty:
                delta.pop("function_call", None)
                changed = True
        if isinstance(delta.get("tool_calls"), list) and not delta["tool_calls"]:
            delta.pop("tool_calls")
            changed = True
        for key in NOISE_KEYS:
            if key in delta and not delta.get(key):
                delta.pop(key)
                changed = True
        if not delta and not choice.get("finish_reason"):
            return ""
    return json.dumps(obj, ensure_ascii=False) if changed else raw


def _strip_empty_fc(obj):
    """递归剔除空 function_call 占位（Responses/chat 通用）。"""
    changed = False
    if isinstance(obj, dict):
        fc = obj.get("function_call")
        if isinstance(fc, dict) and not fc.get("name"):
            obj.pop("function_call", None)
            changed = True
        tc = obj.get("tool_calls")
        if isinstance(tc, list) and not tc:
            obj.pop("tool_calls")
            changed = True
        for v in list(obj.values()):
            if _strip_empty_fc(v):
                changed = True
    elif isinstance(obj, list):
        for v in obj:
            if _strip_empty_fc(v):
                changed = True
    return changed


def clean_responses_frame(frame):
    """清洗 Responses SSE 帧（bytes）。只改写 data: 行，event: 行原样保留。"""
    if not frame:
        return frame
    try:
        text = frame.decode("utf-8")
    except Exception:
        return frame
    out, changed = [], False
    for line in text.splitlines():
        st = line.strip()
        if st.startswith("data:"):
            payload = st[5:].strip()
            if payload and payload != "[DONE]":
                try:
                    obj = json.loads(payload)
                    if _strip_empty_fc(obj):
                        line = "data: " + json.dumps(obj, ensure_ascii=False)
                        changed = True
                except Exception:
                    pass
        out.append(line)
    return ("\n".join(out) + "\n\n").encode("utf-8") if changed else frame


def normalize_roles(messages):
    """Map role names the upstream rejects onto ones it accepts.

    Qoder 只认识 system / user / assistant（+ 模板自带）。OpenAI 新的
    "developer" 角色等价于 "system"。
    """
    out = []
    for m in messages or []:
        if not isinstance(m, dict):
            out.append(m)
            continue
        item = m
        if m.get("role") == "developer":
            item = dict(m)
            item["role"] = "system"
        out.append(item)
    return out


# ---------------------------------------------------------------------------
# 出站提示词脱敏（防御性保留：过滤常见客户端指纹串）
# ---------------------------------------------------------------------------
SANITIZE_FEATURES = (
    "x-anthropic-billing-header",
    "cc_entrypoint=",
    "You are Claude Code, Anthropic's official CLI for Claude",
    "You are a coding agent running in the Codex CLI",
    "github.com/anthropics/",
)
SANITIZE_REWRITES = (
    ("You are Claude Code, Anthropic's official CLI for Claude",
     "You are Claude Code, Anthropic's official CLI tool for Claude"),
    ("You are a coding agent running in the Codex CLI, a terminal-based coding assistant.",
     "You are a coding agent running in the Codex CLI tool, a terminal-based coding assistant."),
)
SANITIZE_HDR_RE = re.compile(r"(?i)x-anthropic-billing-header:[^;\r\n]*;?\s*")
SANITIZE_BARE_HDR_RE = re.compile(r"(?i)x-anthropic-billing-header")
SANITIZE_KV_RE = re.compile(r"(?i)\bcc_[a-z0-9_]+=[^;\r\n]*;?\s*")


def has_fingerprint(text):
    if not isinstance(text, str) or not text:
        return False
    for f in SANITIZE_FEATURES:
        if f in text:
            return True
    return bool(SANITIZE_BARE_HDR_RE.search(text))


def sanitize_text(text):
    if not isinstance(text, str) or not text:
        return text
    if not has_fingerprint(text):
        return text
    for old, new in SANITIZE_REWRITES:
        text = text.replace(old, new)
    text = SANITIZE_HDR_RE.sub("", text)
    if "cc_" in text:
        prev = ""
        while prev != text:
            prev = text
            text = SANITIZE_KV_RE.sub("", text)
    text = SANITIZE_BARE_HDR_RE.sub("x-anthropic-billing-hdr", text)
    return text.strip()


def sanitize_content(content):
    if isinstance(content, str):
        return sanitize_text(content)
    if isinstance(content, list):
        out = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text" \
                    and "text" in part:
                p = dict(part)
                p["text"] = sanitize_text(p["text"])
                out.append(p)
            else:
                out.append(part)
        return out
    return content


def sanitize_tool_calls(tool_calls):
    if not isinstance(tool_calls, list):
        return tool_calls
    out = []
    for tc in tool_calls:
        if isinstance(tc, dict):
            item = dict(tc)
            fn = item.get("function")
            if isinstance(fn, dict) and isinstance(fn.get("arguments"), str):
                fn = dict(fn)
                fn["arguments"] = sanitize_text(fn["arguments"])
                item["function"] = fn
            out.append(item)
        else:
            out.append(tc)
    return out


def sanitize_messages(messages):
    out = []
    for m in messages or []:
        if isinstance(m, dict):
            item = dict(m)
            if "content" in item:
                item["content"] = sanitize_content(item["content"])
            if isinstance(item.get("reasoning_content"), str):
                item["reasoning_content"] = sanitize_text(item["reasoning_content"])
            if "tool_calls" in item:
                item["tool_calls"] = sanitize_tool_calls(item["tool_calls"])
            out.append(item)
        else:
            out.append(m)
    return out


# 思考档位的官方词表顺序（用于把客户端档位映射到模型支持档位）
EFFORT_RANK = {"none": 0, "minimal": 1, "low": 2, "medium": 3, "high": 4,
               "xhigh": 5, "max": 6}
_EFFORT_OFF = ("none", "off", "disabled", "disable", "false", "0", "no")


def supported_efforts(meta):
    """该模型官方支持的思考档位（取自目录 thinking_config.enabled.efforts）。

    空列表表示该模型只有"开/关"没有档位（如 cn 的 qmodel）。
    """
    tc = (meta or {}).get("thinking_config") or {}
    enabled = tc.get("enabled") if isinstance(tc.get("enabled"), dict) else {}
    efforts = enabled.get("efforts") if isinstance(enabled.get("efforts"), dict) else {}
    return [e for e in EFFORT_RANK if e in efforts]


def default_effort(meta):
    """该模型的默认思考档位（is_default 标注项），无则取支持表中间档。"""
    tc = (meta or {}).get("thinking_config") or {}
    enabled = tc.get("enabled") if isinstance(tc.get("enabled"), dict) else {}
    efforts = enabled.get("efforts") if isinstance(enabled.get("efforts"), dict) else {}
    for e, cfg in efforts.items():
        if isinstance(cfg, dict) and cfg.get("is_default"):
            return e
    sup = supported_efforts(meta)
    return sup[len(sup) // 2] if sup else ""


def normalize_reasoning_effort(effort, meta):
    """把请求里的思考档位归一化到该模型**官方支持**的档位集合。

    为什么必须做：上游对不支持的档位**不报错、直接忽略**（回落到模型默认档）。
    实测：dfmodel 官方只支持 low/high/max（默认 max），传 medium/xhigh 时输出
    与默认档一致——客户端以为"思考档位没生效"；cn 的 qmodel 只有开/关，传任何
    档位都被忽略（只有 none 能关掉思考）。

    规则：
      - 关闭类取值（none/off/disabled/...）统一映射为 "none"（官方通用关闭值）；
      - 模型有档位表：命中则原样透传；未命中取"最近的合法档位"（同距时偏向
        模型默认档），并给出说明性 note；
      - 模型有 thinking_config 但无档位表（仅开/关）：除 none 外不下发该参数
        （避免给上游发它不认识的档位）；
      - 模型没有 thinking_config（如路由器 `auto`）：**原样透传**，不做猜测。

    返回 (value|None, note)：value=None 表示不下发 reasoning_effort。
    """
    e = str(effort or "").strip().lower()
    if not e:
        return None, ""
    if e in _EFFORT_OFF:
        return "none", ""
    tc = (meta or {}).get("thinking_config")
    if not isinstance(tc, dict):
        # 目录里没有该模型的思考配置（路由器/未知模型）：不猜测，原样透传
        return e, ""
    sup = supported_efforts(meta)
    if not sup:
        return None, "dropped (model has no effort levels; use none to disable)"
    if e in sup:
        return e, ""
    if e not in EFFORT_RANK:
        return default_effort(meta) or sup[0], \
            "unknown level -> %s" % (default_effort(meta) or sup[0])
    d = default_effort(meta)
    want = EFFORT_RANK[e]

    def key(cand):
        return (abs(EFFORT_RANK[cand] - want),
                abs(EFFORT_RANK[cand] - EFFORT_RANK.get(d, want)))

    best = sorted(sup, key=key)[0]
    return best, "unsupported level %s -> %s" % (e, best)


# ---------------------------------------------------------------------------
# 项目新版本检测（对比 GitHub 最新 release；结果缓存 6 小时）
# ---------------------------------------------------------------------------
UPDATE_CHECK_URL = ("https://api.github.com/repos/shuishuipingan/"
                    "qoder2api-hub/releases/latest")
UPDATE_CHECK_TTL = 6 * 3600
_update_cache = {"at": 0.0, "data": None}


def version_tuple(value):
    """'v1.2.3' -> (1, 2, 3)；容忍前缀与不足位数。"""
    out = []
    for chunk in str(value or "").lstrip("vV").split("."):
        digits = ""
        for ch in chunk:
            if ch.isdigit():
                digits += ch
            else:
                break
        out.append(int(digits or 0))
    while len(out) < 3:
        out.append(0)
    return tuple(out[:3])


def check_for_update(force=False):
    """检查项目是否有新版本（对比当前 VERSION 与 GitHub 最新 release）。

    返回 {ok, current, latest, has_update, url, published_at, name, checked_at, error}
    网络不可用/被墙时 ok=False + error，看板据此显示"检查失败"而不是误报。
    """
    now = time.time()
    cached = _update_cache.get("data")
    if cached and not force and now - _update_cache.get("at", 0) < UPDATE_CHECK_TTL:
        return cached
    info = {"ok": False, "current": VERSION, "latest": "", "has_update": False,
            "url": "", "published_at": "", "name": "", "checked_at": int(now),
            "error": ""}
    try:
        url = validate_public_http_url(UPDATE_CHECK_URL)
        req = urllib.request.Request(url, headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "qoder-proxy-update-check",
        })
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        tag = str(data.get("tag_name") or "").strip()
        info.update({
            "ok": True,
            "latest": tag,
            "url": str(data.get("html_url") or ""),
            "published_at": str(data.get("published_at") or ""),
            "name": str(data.get("name") or ""),
            "has_update": bool(tag) and version_tuple(tag) > version_tuple(VERSION),
        })
    except Exception as exc:
        info["error"] = str(exc)[:160]
    _update_cache.update({"at": now, "data": info})
    return info


def is_deepseek_model(model="", model_key=""):
    """判断这次请求最终打到的是不是一个 DeepSeek 上游模型。

    必须同时看**上游 key**（dmodel / dfmodel）和客户端写的名字：
    客户端完全可能直接写 `dfmodel`/`dmodel`（仓库文档里就把 DeepSeek-Flash
    标为「内部 key：dfmodel」），而展示名是 `DeepSeek-Flash`。此前只按名字
    前缀 "deepseek" 判断，走 key 的请求拿不到 reasoning_content 兼容处理，
    多轮会话会被上游拒绝——表现为"偶发失败、重试有时能过"。
    """
    key = str(model_key or "").strip().lower()
    if key in DEEPSEEK_MODEL_KEYS:
        return True
    low = str(model or "").strip().lower()
    if not low:
        return False
    if low in DEEPSEEK_MODEL_KEYS or low.startswith("deepseek"):
        return True
    # 展示 id「dfmodel (DeepSeek-Flash)」/ 别名 deepseek-v4-flash 等
    return "deepseek" in low


def backfill_reasoning_content(messages, model, model_key=""):
    """DeepSeek 多轮一致性：assistant 历史补 reasoning_content。

    触发条件只看"上游是不是 DeepSeek"，与客户端用 key 还是展示名无关。
    """
    if not is_deepseek_model(model, model_key):
        return messages
    has_trace = False
    for m in messages:
        if isinstance(m, dict):
            if m.get("reasoning") or "reasoning_content" in m:
                has_trace = True
                break
    if not has_trace:
        return messages
    out = []
    for m in messages:
        if isinstance(m, dict) and m.get("role") == "assistant":
            item = dict(m)
            if "reasoning_content" not in item:
                if item.get("reasoning"):
                    item["reasoning_content"] = str(item["reasoning"])
                else:
                    item["reasoning_content"] = ""
            out.append(item)
        else:
            out.append(m)
    return out


def normalize_tool_choice(obj):
    """把 OpenAI tool_choice 归一成上游可接受的形态（避免 400）。"""
    if "tool_choice" not in obj:
        return
    tc = obj["tool_choice"]
    if isinstance(tc, str):
        val = tc.strip().lower()
        if val == "none":
            obj.pop("tool_choice", None)
            obj.pop("tools", None)
        return
    if isinstance(tc, dict):
        typ = (tc.get("type") or "").strip().lower()
        if typ == "none":
            obj.pop("tool_choice", None)
            obj.pop("tools", None)
        elif typ in ("auto", "required"):
            obj["tool_choice"] = typ
        elif typ == "function":
            name = (tc.get("function") or {}).get("name") or tc.get("name") or ""
            obj["tool_choice"] = name.strip() or "auto"
        else:
            obj.pop("tool_choice", None)
    else:
        obj.pop("tool_choice", None)


def normalize_tools(obj):
    """把顶层 name 型工具定义包成 Chat Completions function schema。"""
    tools = obj.get("tools")
    if not tools or not isinstance(tools, list):
        return
    norm = []
    for t in tools:
        if not isinstance(t, dict):
            continue
        if "name" in t and "function" not in t and t.get("type") == "function":
            fn = {
                "name": t.get("name") or "",
                "description": t.get("description") or "",
                "parameters": t.get("parameters") or {},
            }
            if "strict" in t:
                fn["strict"] = t["strict"]
            norm.append({"type": "function", "function": fn})
        else:
            norm.append(t)
    obj["tools"] = norm


def translate_max_completion_tokens(obj):
    alias = obj.pop("max_completion_tokens", None)
    if alias is None:
        return
    if "max_tokens" in obj:
        return
    try:
        val = int(alias)
        if val > 0:
            obj["max_tokens"] = val
    except (TypeError, ValueError):
        pass


# ---------------------------------------------------------------------------
# DeepSeek DSML 工具调用回退解析
# ---------------------------------------------------------------------------
TAG_START = r"<[^>]*DSML[^>]*"
DSML_CALLS_RE = re.compile(TAG_START + r"calls>(.*?)</[^>]*DSML[^>]*calls>",
                           re.DOTALL)
DSML_INVOKE_RE = re.compile(
    TAG_START + r"invoke\s+name=[\x22\x27]([^\x22\x27]+)[\x22\x27]>(.*?)</[^>]*invoke>",
    re.DOTALL)
DSML_PARAM_RE = re.compile(
    TAG_START + r"parameter\s+name=[\x22\x27]([^\x22\x27]+)[\x22\x27][^>]*>(.*?)</[^>]*parameter>",
    re.DOTALL)


def parse_dsml_tool_calls(text):
    if not text or "DSML" not in text:
        return None, text
    match = DSML_CALLS_RE.search(text)
    if not match:
        return None, text
    calls_block = match.group(1)
    tool_calls = []
    for inv_match in DSML_INVOKE_RE.finditer(calls_block):
        func_name = inv_match.group(1)
        params_block = inv_match.group(2)
        params = {}
        for p_match in DSML_PARAM_RE.finditer(params_block):
            p_name = p_match.group(1)
            p_val = p_match.group(2).strip()
            params[p_name] = p_val
        tool_calls.append({
            "id": _new_id("call_"),
            "name": func_name,
            "arguments": json.dumps(params, ensure_ascii=False),
        })
    clean = (text[:match.start()].strip() + " " + text[match.end():].strip()).strip()
    return tool_calls, clean


# ---------------------------------------------------------------------------
# 历史工具调用文本形态的「回读」（issue #8）
# ---------------------------------------------------------------------------
# flatten_messages() 会把 assistant 历史里的 tool_calls 序列化成
#   marker（LEAK_MARKER，见下）+ 一个 JSON 数组 [{name, arguments}, ...]
# 给上游模型当上下文。长会话里模型偶发**照格式复述**成正文（没有结构化
# tool_calls），客户端就会把这段 JSON 当正文显示、本轮工具调用不执行。
# 这里补齐回读：在严格守卫下还原成结构化 tool_calls 并从正文移除。
LEAK_MARKER = "[assistant 请求调用工具]"


def parse_leaked_tool_calls(text, allowed_names=None):
    """把模型复述的「marker + JSON 数组」还原成结构化 tool_calls。

    严格守卫（避免把"讨论该标记的普通回复"误判为工具调用）：
      1) 整段正文 strip 后必须**恰好**是 marker + JSON 数组（可带 ``` 围栏）；
      2) 数组非空，每项 name 为非空字符串、arguments 为合法 JSON（字符串或对象）；
      3) 本次请求声明了 tools 时，所有 name 必须命中声明集合。

    返回 (tool_calls|None, clean_text)：识别成功时 clean_text 为空串
    （整段都是该块）；未识别时原样返回 text。
    """
    t = (text or "").strip()
    # 整段被 ``` 围栏包裹时先剥壳（模型复述时常带围栏）
    if t.startswith("```") and t.endswith("```") and len(t) > 6:
        inner = t[3:]
        if inner[:4].lower() == "json":
            inner = inner[4:]
        t = inner[:-3].strip()
    if not t.startswith(LEAK_MARKER):
        return None, text
    rest = t[len(LEAK_MARKER):].strip()
    if not (rest.startswith("[") and rest.endswith("]")):
        return None, text
    try:
        arr = json.loads(rest)
    except Exception:
        return None, text
    if not isinstance(arr, list) or not arr:
        return None, text
    calls = []
    for item in arr:
        if not isinstance(item, dict):
            return None, text
        name = item.get("name")
        args = item.get("arguments")
        if not isinstance(name, str) or not name.strip():
            return None, text
        if isinstance(args, (dict, list)):
            args = json.dumps(args, ensure_ascii=False)
        if not isinstance(args, str):
            return None, text
        if args.strip():
            try:
                json.loads(args)
            except Exception:
                return None, text
        calls.append({
            "id": _new_id("call_"),
            "type": "function",
            "function": {"name": name.strip(), "arguments": args},
        })
    if allowed_names:
        if not all(c["function"]["name"] in allowed_names for c in calls):
            return None, text
    return calls, ""


def _leak_body_offset(text):
    """把「marker 之前的围栏前缀 / marker 之后的正文」解析出来。

    返回 (state, body)：state='pre'（还处在围栏/前缀区，未到 marker）、
    state='body'（已越过 marker，body 为其后文本）、state='no'（显然不是）。
    流式暂存与判真共用同一套前缀规则（含 ``` 与 ```json 围栏）。
    """
    t = (text or "").lstrip()
    if not t or LEAK_MARKER.startswith(t):
        return "pre", None
    if t.startswith(LEAK_MARKER):
        return "body", t[len(LEAK_MARKER):]
    if not t.startswith("`"):
        return "no", None
    if len(t) < 3:
        return ("pre", None) if "```".startswith(t) else ("no", None)
    if not t.startswith("```"):
        return "no", None
    rest = t[3:]
    low = rest.lstrip().lower()
    if low.startswith("json"):
        after = rest.lstrip()[4:].lstrip()
    elif LEAK_MARKER.startswith(low) or low.startswith(LEAK_MARKER):
        after = rest.lstrip()          # 无语言标记的 ``` 围栏
    elif "json".startswith(low):
        return "pre", None             # 语言标记还没打完（j/js/json）
    else:
        return "no", None
    if LEAK_MARKER.startswith(after):
        return "pre", None
    if after.startswith(LEAK_MARKER):
        return "body", after[len(LEAK_MARKER):]
    return "no", None


def _leak_prefix_hold(text):
    """文本仍可能是泄漏块的前缀？（流式 hold-back 用）"""
    return _leak_body_offset(text)[0] != "no"


def _tool_names_from_payload(payload):
    """取客户端本次请求声明的工具名集合（回读守卫用）。"""
    names = set()
    for t in (payload or {}).get("tools") or []:
        if not isinstance(t, dict):
            continue
        fn = t.get("function") if isinstance(t.get("function"), dict) else t
        name = fn.get("name") or ""
        if name:
            names.add(str(name))
    return names or None


def _json_array_prefix_ok(text):
    """text 是否仍可能扩展成合法 JSON 数组字面量（流式暂存判定用）。"""
    s = (text or "").strip()
    if not s:
        return True
    if not s.startswith("["):
        return False
    try:
        json.loads(s)
        return True
    except Exception as exc:
        msg = str(exc)
        # 未闭合字符串的报错位置在字符串开头，但仍可继续扩展
        if msg.startswith("Unterminated string"):
            return True
        pos = getattr(exc, "pos", None)
        if pos is None:
            return False
        if msg.startswith("Extra data"):
            # 数组已完整，尾随的只能是（可能还没打完的）``` 收尾围栏
            tail = s[pos:].strip()
            return (not tail) or "```".startswith(tail)
        # 语法错误点落在末尾（未闭合括号等）→ 还允许继续扩展
        return pos >= len(s) - 1


def _still_leak_candidate(text):
    """整段文本目前是否仍是「marker + JSON 数组」候选？（流式暂存判定用）"""
    state, body = _leak_body_offset(text)
    if state == "pre":
        return True
    if state == "no":
        return False
    rest = (body or "").lstrip()
    if not rest:
        return True
    if rest.endswith("```"):
        rest = rest[:-3]
    return _json_array_prefix_ok(rest)


def _leaked_partial_droppable(text, allowed_names=None):
    """issue #9：正文是网关自己写入历史的工具调用回声、但被截断（JSON 不完整/
    非法）时，应吞掉整段而不是透传给终端用户。三个条件同时成立才吞：
      1) strip 后以 LEAK_MARKER 开头（真实散文回复几乎不会这样开头）；
      2) 本次请求声明了 tools（allowed_names 非空）——与恢复路径同前置条件；
      3) marker 之后（剥掉围栏后）仍是 JSON 数组字面量的前缀
         （复用现成的 _json_array_prefix_ok）——即"本该是数组，只是没写完"。
    返回 True = 应吞掉正文（输出空 content）。

    完整且合法的 JSON 数组不算：那种情况下恢复失败另有原因（工具名未声明 /
    arguments 非法），保持既有 fail-open 透传语义（见 _test_qoder.py 的 [27]
    断言"未声明工具名 -> 不吞正文"），只有"数组本身没写完 / 非法"才吞。
    """
    if not text or not allowed_names:
        return False
    t = text.strip()
    if not t:
        return False
    if t == LEAK_MARKER:
        # 只有 marker、连数组都没开始写：判据 3 的退化情形，同样是回声。
        return True
    state, body = _leak_body_offset(t)
    if state != "body":
        return False
    rest = (body or "").strip()
    if rest.endswith("```"):
        rest = rest[:-3].strip()
    if not _json_array_prefix_ok(rest):
        return False
    try:
        json.loads(rest)
        return False
    except Exception:
        return True


def _leak_chunk_frame(meta, delta, finish_reason=None):
    """按流式 chat.completion.chunk 形态合成一帧（沿用上游的 id/model）。"""
    payload = {
        "id": meta.get("id") or "chatcmpl-qoder",
        "object": "chat.completion.chunk",
        "created": meta.get("created") or int(time.time()),
        "model": meta.get("model") or "",
        "choices": [{"index": 0, "delta": delta,
                     "finish_reason": finish_reason}],
    }
    return ("data: " + json.dumps(payload, ensure_ascii=False)
            + "\n\n").encode("utf-8")


def _frame_without_content(payload):
    """复制一帧并去掉 delta.content（保留 tool_calls/reasoning/收尾信息）。"""
    out = dict(payload)
    choices = []
    for ch in payload.get("choices") or []:
        if not isinstance(ch, dict):
            choices.append(ch)
            continue
        ch2 = dict(ch)
        delta = ch2.get("delta")
        if isinstance(delta, dict) and "content" in delta:
            d2 = dict(delta)
            d2.pop("content", None)
            ch2["delta"] = d2
        choices.append(ch2)
    out["choices"] = choices
    return out


def recover_leaked_tool_calls(frames, allowed_names=None):
    """流式回读（issue #8）：把模型逐块吐出的「marker + JSON 数组」正文恢复
    成结构化 tool_calls 增量帧；其余内容原样透传（fail-open）。

    与 parse_leaked_tool_calls 同一套严格守卫，另加：
      - 只有当已输出的正文仍是该块的「前缀/候选」时才暂存（期间不发正文）；
      - 候选被证伪（出现别的字 / JSON 非法）→ 立刻把暂存整段补发为正文；
      - 收尾帧暂存到流末，识别成功时改写 finish_reason=tool_calls；
      - 上游中断时暂存直接丢弃（正文未发，交给上游重试/错误帧处理）。
    """
    hold = None          # 正在暂存的候选正文（None = 未暂存）
    finished = None      # 暂存的收尾帧：(payload, 原 finish_reason)
    recovered = False
    meta = {"id": None, "model": None, "created": None}

    for raw in frames:
        text = (raw.decode("utf-8", "replace")
                if isinstance(raw, bytes) else raw)
        data = strip_data_prefix(text)
        payload = None
        if data and data != "[DONE]":
            try:
                payload = json.loads(data)
            except Exception:
                payload = None
        if not isinstance(payload, dict):
            yield raw
            continue
        if payload.get("id"):
            meta["id"] = payload["id"]
        if payload.get("model"):
            meta["model"] = payload["model"]
        if payload.get("created"):
            meta["created"] = payload["created"]
        choices = payload.get("choices") or []
        choice = (choices[0] if choices and isinstance(choices[0], dict)
                  else {})
        delta = choice.get("delta") or {}
        piece_raw = delta.get("content")
        piece = piece_raw if isinstance(piece_raw, str) else ""
        fin = choice.get("finish_reason")
        if piece_raw is not None and not isinstance(piece_raw, str):
            # 非字符串 content（罕见）：不参与暂存判断，原样透传
            if hold is not None:
                yield _leak_chunk_frame(meta, {"content": hold})
                hold = None
            yield raw
            continue
        if hold is not None:
            hold += piece
            if _still_leak_candidate(hold):
                if fin:
                    finished = (payload, fin)
                elif (delta.get("tool_calls")
                      or delta.get("reasoning_content")
                      or delta.get("role")):
                    other = _frame_without_content(payload)
                    yield ("data: "
                           + json.dumps(other, ensure_ascii=False)
                           + "\n\n").encode("utf-8")
                continue
            # 证伪：暂存整段补发为普通正文
            yield _leak_chunk_frame(meta, {"content": hold})
            hold = None
            if fin:
                finished = (payload, fin)
            continue
        if fin and not piece:
            finished = (payload, fin)
            continue
        if piece and _leak_prefix_hold(piece):
            hold = piece
            if fin:
                finished = (payload, fin)
            continue
        if piece and fin:
            yield _leak_chunk_frame(meta, {"content": piece}, fin)
            continue
        if piece:
            yield _leak_chunk_frame(meta, {"content": piece})
            continue
        yield raw

    if hold is not None:
        calls, clean = parse_leaked_tool_calls(hold, allowed_names)
        if calls:
            recovered = True
            for i, call in enumerate(calls):
                yield _leak_chunk_frame(meta, {"tool_calls": [{
                    "index": i,
                    "id": call.get("id"),
                    "type": call.get("type") or "function",
                    "function": call.get("function") or {},
                }]})
            if clean:
                yield _leak_chunk_frame(meta, {"content": clean})
        elif _leaked_partial_droppable(hold, allowed_names):
            # issue #9：截断的 marker+JSON 回声无法还原成结构化调用，吞掉整段，
            # 绝不把网关内部协议文本透传给终端用户。
            log("dropped truncated leaked tool-call echo (%d chars) — issue #9"
                % len(hold), level="WARN", tag="chat")
        else:
            yield _leak_chunk_frame(meta, {"content": hold})
    if finished is not None:
        payload, fin = finished
        if recovered:
            payload = json.loads(json.dumps(payload))
            for ch in payload.get("choices") or []:
                if isinstance(ch, dict) and ch.get("finish_reason"):
                    ch["finish_reason"] = "tool_calls"
        yield ("data: " + json.dumps(payload, ensure_ascii=False)
               + "\n\n").encode("utf-8")
    elif recovered:
        yield _leak_chunk_frame(meta, {}, "tool_calls")


# ---------------------------------------------------------------------------
# Qoder 数据面：请求体构造
# ---------------------------------------------------------------------------
def _flatten_content_text(content):
    """把 OpenAI content（str 或 parts 列表）压成纯文本；图片另行收集。"""
    if content is None:
        return "", []
    if isinstance(content, str):
        return content, []
    if not isinstance(content, list):
        return str(content), []
    texts, images = [], []
    for piece in content:
        if isinstance(piece, str):
            texts.append(piece)
            continue
        if not isinstance(piece, dict):
            continue
        ptype = piece.get("type") or ""
        if ptype in ("text", "input_text", "output_text", "summary_text"):
            texts.append(piece.get("text") or "")
        elif ptype in ("image_url", "image", "input_image") or "image_url" in piece:
            url = piece.get("image_url") or piece.get("url")
            if isinstance(url, dict):
                url = url.get("url")
            if not url and piece.get("data"):
                mime = piece.get("mimeType") or piece.get("mime_type") or "image/png"
                url = "data:%s;base64,%s" % (mime, piece["data"])
            if url:
                images.append(url)
    return "\n".join(t for t in texts if t), images


def flatten_messages(messages, keep_reasoning=False):
    """把客户端会话压平成 Qoder 上游可接受的 {role, content} 序列。

    返回 (system_text, flat_msgs, images)：
      - 首个 system/developer 消息抽为 system_text（模板 system 的替换源）
      - user/assistant 原位保留（content 压平为字符串）
      - tool 结果降级为 user 消息（上游不认识 tool 角色）
      - assistant 的 tool_calls 序列化进 content，保证上下文不丢
      - assistant 的 reasoning_content 仅在 keep_reasoning=True（DeepSeek 族）
        时保留：这族模型的多轮一致性与该字段绑定，其它模型不带（避免上游
        因未知字段拒答）。此前 flatten 无条件丢弃该字段，使
        backfill_reasoning_content 的兼容处理在真实请求路径上完全失效。
    """
    system_text = None
    flat, images = [], []
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        text, imgs = _flatten_content_text(m.get("content"))
        images.extend(imgs)
        text = sanitize_text(text) if isinstance(text, str) else text
        if role in ("system", "developer"):
            if system_text is None and text:
                system_text = text
            continue
        if role == "tool":
            name = ""
            tc = m.get("name") or ""
            if tc:
                name = " (%s)" % tc
            flat.append({"role": "user",
                         "content": "[工具结果%s]\n%s" % (name, text or "")})
            continue
        if role == "assistant" and m.get("tool_calls"):
            calls = []
            for tc in m["tool_calls"]:
                fn = (tc or {}).get("function") or {}
                calls.append({"name": fn.get("name") or "",
                              "arguments": fn.get("arguments") or ""})
            if calls:
                text = (text or "") + "\n\n" + LEAK_MARKER + "\n" + \
                    json.dumps(calls, ensure_ascii=False)
            item = {"role": "assistant", "content": text or ""}
            if keep_reasoning and m.get("reasoning_content") is not None:
                item["reasoning_content"] = m.get("reasoning_content") or ""
            flat.append(item)
            continue
        if role in ("user", "assistant"):
            item = {"role": role, "content": text or ""}
            if role == "assistant" and keep_reasoning \
                    and m.get("reasoning_content") is not None:
                item["reasoning_content"] = m.get("reasoning_content") or ""
            flat.append(item)
        elif role:  # 未知角色一律降级为 user，不静默丢弃
            flat.append({"role": "user", "content": text or ""})
    return system_text, flat, images


def build_qoder_body(payload, account, model_key, realm=None):
    """构造 agent_chat_generation 上游请求体（返回 dict，随后 qoder_encode）。

    以官方 baseprompt.json 为骨架：每次覆写 request/session id、时间戳、
    模型配置（按出口区域的官方清单取元数据）、系统提示词与会话、工具、
    参数与 business 会话名。
    """
    r = realm or (account.realm if account else CURRENT_REALM)
    body = json.loads(json.dumps(BASEPROMPT))   # deep copy
    messages = normalize_roles(payload.get("messages") or [])
    messages = sanitize_messages(messages)
    model = payload.get("model") or ""
    messages = backfill_reasoning_content(messages, model, model_key)

    system_text, flat, images = flatten_messages(
        messages, keep_reasoning=is_deepseek_model(model, model_key))
    # 模板只保留 system 基座（其自带的示例 user 轮次是样例内容，必须丢弃），
    # 真实会话随后追加。
    tmpl_system = [m for m in (body.get("messages") or [])
                   if (m or {}).get("role") == "system"]
    if system_text is None:
        # 客户端没给 system：优先用 --system-prompt 覆盖值（非默认时），
        # 否则保留模板自带的 Qoder 系统提示词。
        if SYSTEM_PROMPT and SYSTEM_PROMPT != DEFAULT_SYSTEM_PROMPT:
            system_text = SYSTEM_PROMPT
        else:
            body["messages"] = tmpl_system
    if system_text is not None:
        body["messages"] = [{"role": "system", "content": system_text}]
    # 追加真实会话
    body["messages"].extend(flat)

    # 最新用户提示词（上游 chat_context 高亮 + business 会话名）
    prompt = ""
    for m in reversed(flat):
        if m["role"] == "user" and m.get("content"):
            prompt = m["content"]
            break

    nid = str(uuid.uuid4())
    body["request_id"] = nid
    body["chat_record_id"] = nid
    body["request_set_id"] = str(uuid.uuid4())
    body["session_id"] = str(uuid.uuid4())
    body["stream"] = True
    body["agent_id"] = "agent_common"
    body["aliyun_user_type"] = getattr(account, "user_type", "") or \
        qoder_sign.DEFAULT_USER_TYPE

    # model_config（按出口区域的官方清单取元数据 + 动态 key）
    catalog_meta = {}
    for m in qoder_catalog.models_for_realm(r):
        if m.get("key") == model_key:
            catalog_meta = m
            break
    if not catalog_meta:
        for m in qoder_catalog.STATIC_INTL_MODELS + qoder_catalog.STATIC_CN_MODELS:
            if m.get("key") == model_key:
                catalog_meta = m
                break
    mc = body.get("model_config") or {}
    mc["key"] = model_key
    mc["display_name"] = catalog_meta.get("display_name") or model_key
    mc["model"] = ""
    mc["format"] = "openai"
    mc["is_vl"] = bool(catalog_meta.get("is_vl"))
    mc["is_reasoning"] = bool(catalog_meta.get("is_reasoning"))
    mc["api_key"] = ""
    mc["url"] = ""
    mc["source"] = "system"
    mc["max_input_tokens"] = catalog_meta.get("max_input_tokens") or 180000
    body["model_config"] = mc

    # chat_context 高亮与模型配置副本
    cc = body.get("chat_context") or {}
    txt = cc.get("text") or {}
    txt["text"] = prompt
    cc["text"] = txt
    extra = cc.get("extra") or {}
    oc = extra.get("originalContent") or {}
    oc["text"] = prompt
    extra["originalContent"] = oc
    extra["modelConfig"] = dict(mc)
    cc["extra"] = extra
    if images:
        cc["imageUrls"] = images
        body["image_urls"] = images
    body["chat_context"] = cc

    # parameters：max_tokens / reasoning_effort
    params = body.get("parameters") or {}
    max_tokens = payload.get("max_tokens") or payload.get("max_completion_tokens")
    if max_tokens:
        try:
            params["max_tokens"] = int(max_tokens)
        except (TypeError, ValueError):
            pass
    effort = payload.get("reasoning_effort")
    if not effort and isinstance(payload.get("reasoning"), dict):
        effort = (payload.get("reasoning") or {}).get("effort")
    if not effort and isinstance(payload.get("thinking"), dict):
        # 兼容 thinking.effort / thinking_level 风格客户端
        effort = (payload.get("thinking") or {}).get("effort") \
            or (payload.get("thinking") or {}).get("level")
    if effort:
        norm, note = normalize_reasoning_effort(effort, catalog_meta)
        if note:
            log("reasoning_effort %s on '%s' (supported=%s)"
                % (note, model_key or model, supported_efforts(catalog_meta)), tag="chat")
        if norm:
            params["reasoning_effort"] = norm
    body["parameters"] = params

    # tools：客户端给了就用客户端的（custom freeform 已降级），否则置空，
    # 避免把 Qoder 桌面端自带的 agent 工具（Bash/Edit/...）泄漏给普通客户端。
    tools = payload.get("tools")
    if tools:
        body["tools"] = tools
    else:
        body["tools"] = []

    # business 会话卡片
    biz = body.get("business") or {}
    biz["id"] = str(uuid.uuid4())
    biz["begin_at"] = int(time.time() * 1000)
    biz["name"] = (prompt[:30] if prompt else "chat")
    biz.setdefault("product", "cli")
    biz.setdefault("type", "agent")
    biz.setdefault("stage", "start")
    body["business"] = biz
    return body


# ---------------------------------------------------------------------------
# Qoder 数据面：上游打开与 SSE 信封解包
# ---------------------------------------------------------------------------
class RateLimited(Exception):
    """Upstream throttled this request (429 / 频控)。"""

    def __init__(self, http_error=None, detail="", wait=60):
        self.http_error = http_error
        self.detail = detail or ""
        self.wait = max(1, int(wait or 60))
        super(RateLimited, self).__init__(
            "upstream rate limit: %s" % (self.detail[:200] or "429"))


# 账号级错误短冷却的“等待续上”上限：单账号池在传输/瞬时故障后只有
# 3s（多账号 15s）冷却，此时**等待**比报 429 更正确——429 只留给真正的
# 上游频控（model_cooldowns，由上游 HTTP 429 设置）。
ERROR_COOLDOWN_WAIT_MAX = 10.0


def _short_error_cooldown_wait(realm, model, exclude=None):
    """账号级（错误/传输）短冷却的最短剩余秒数；不适用则返回 0。

    - 只看 cooldown_until（错误冷却）；若该模型正被上游频控
      （model_cooldowns）则返回 0，交给429路径处理——两者语义严格分开。
    - exclude（本请求已试过的账号）中的冷却账号**不算**：等它冷却好了也
      不会再被本请求使用，纯属浪费。
    """
    if not POOL:
        return 0.0
    exclude = exclude or set()
    now = time.time()
    waits = []
    for a in POOL.accounts:
        if a.realm != realm or not a.enabled or not a.access_token:
            continue
        if a.uid in exclude:
            continue
        if a.model_cooldowns.get(model, 0.0) > now:
            return 0.0          # 上游频控生效中 -> 正当429，不等待
        remain = a.cooldown_until - now
        if 0 < remain <= ERROR_COOLDOWN_WAIT_MAX:
            waits.append(remain)
    return min(waits) if waits else 0.0


def retry_after_seconds(model, realm):
    """上游频控的最短等待（仅 model_cooldowns——429 的正当语义）。"""
    if not POOL:
        return 60
    now = time.time()
    waits = [a.model_cooldowns.get(model, 0.0) - now
             for a in POOL.accounts
             if a.realm == realm and a.enabled and a.access_token]
    active = [w for w in waits if w > 0]
    return int(min(active)) if active else 60


def realm_model_throttled(realm, model):
    """True 仅当该区域账号全部被**上游频控**（model_cooldowns）挡住。

    账号级错误冷却（cooldown_until）不算频控——否则传输故障后客户端会连续
    收到误导性的 `429 usage exceeds frequency limit`。
    """
    if not POOL:
        return (False, 0)
    existing = [a for a in POOL.accounts
                if a.realm == realm and a.enabled and a.access_token]
    if not existing:
        return (False, 0)
    now = time.time()
    waits = [a.model_cooldowns.get(model, 0.0) - now for a in existing]
    if waits and all(w > 0 for w in waits):
        return (True, max(1, int(min(waits))))
    return (False, 0)


def extract_session_key(headers, payload):
    key = (
        headers.get("X-Conversation-Id") or
        headers.get("Conversation-Id") or
        headers.get("X-Session-Id") or
        headers.get("Session-Id") or
        payload.get("conversation_id") or
        payload.get("session_id") or
        (payload.get("metadata") or {}).get("conversation_id")
    )
    if key:
        return str(key).strip()
    return None


# 上游瞬时故障（与客户端参数无关，值得同账号快速重试）
TRANSIENT_HTTP_CODES = (418, 500, 502, 503, 504)
TRANSIENT_MAX_RETRIES = 2          # 同账号额外重试次数（1s、2s 退避）
CHECKIN_MIN_GAP = 1.0              # 签到防风控：账号之间的最小间隔（秒）
_CLIENT_FAULT_MARKERS = (
    "invalid_parameter_error",     # 如 Range of max_tokens 校验失败
    "invalid_request_error",
    "authentication_error",
    "permission_error",
    '"Range of ',
    # 上游内容安全审核：确定性拒绝，重试无效（本轮日志实证
    # InternalError.Algo.DataInspectionFailed: Input text data may contain
    # inappropriate content.）
    "DataInspectionFailed",
    "inappropriate content",
    "input text data may contain",
    "ContentFilter",
    "SensitiveContent",
)

# 内容审核类错误（用户输入侧问题，需专门的中文解释）
_CONTENT_POLICY_MARKERS = (
    "DataInspectionFailed",
    "inappropriate content",
    "input text data may contain",
    "ContentFilter",
    "SensitiveContent",
)


def _is_transient_upstream(code, detail):
    """判断一次上游 HTTP 错误是否属于瞬时故障（可重试）。

    - 客户端参数/权限类错误（invalid_parameter_error 等）→ 永不重试
    - 418（上游把自己的 provider 故障包装成 418+provider_error）与 5xx → 瞬时
    - 其余 4xx 带 provider_error（"Error in upstream response"）→ 瞬时
    """
    detail = detail or ""
    if code in (401, 403, 429):
        return False        # 凭证/频控各自有专门处理路径，不属瞬时重试类
    if any(m in detail for m in _CLIENT_FAULT_MARKERS):
        return False
    if code in TRANSIENT_HTTP_CODES or code >= 500:
        return True
    if 400 <= code < 500 and "provider_error" in detail:
        return True
    return False


def _is_transient_transport(exc):
    """传输层瞬时故障（对 qoder.sh 的 TLS/连接抖动很常见）：可同账号重试。

    覆盖 SSL EOF/重置、连接重置/中止、超时、以及 URLError 包装的上述原因
    （含按字符串描述判断的情形，如 "SSL: UNEXPECTED_EOF_WHILE_READING"）。
    """
    if isinstance(exc, urllib.error.URLError):
        return _is_transient_transport(getattr(exc, "reason", None))
    if isinstance(exc, (ssl.SSLError, ConnectionResetError,
                        ConnectionAbortedError, TimeoutError,
                        ConnectionError, OSError)):
        return True
    if isinstance(exc, str):
        low = exc.lower()
        return any(k in low for k in ("ssl", "eof", "reset", "timed out",
                                      "broken pipe", "connection"))
    return False


def friendly_upstream_error(code, detail):
    """把上游错误转成对客户端可读的消息；瞬时故障给出重试指引。

    返回 (message, err_type)。
    """
    detail = (detail or "").strip()
    try:
        code_i = int(code)
    except Exception:
        code_i = 502
    # 1) 上游内容安全审核（确定性拒绝，先于瞬时判断——重试无效）
    if any(m in detail for m in _CONTENT_POLICY_MARKERS):
        return ("上游内容安全审核未通过 (DataInspectionFailed)：输入可能含不当内容，"
                "属确定性拒绝、重试无效。请检查/缩短输入（系统提示词、超长历史、"
                "工具定义或粘贴的代码/文本）后重试。上游详情：%s"
                % detail[:300],
                "content_policy_rejected")
    if _is_transient_upstream(code_i, detail) or (
            "provider_error" in detail and "invalid_" not in detail):
        return ("上游瞬时故障 (HTTP %s)：网关已对同账号自动重试仍失败，"
                "请稍后重试。上游详情：%s"
                % (code_i, detail[:300] or "(无详情)"),
                "upstream_transient_error")
    return ("upstream %s: %s" % (code_i, detail)), "upstream_error"


def _to_int_status(status):
    """信封 statusCodeValue 可能是 int 或 str，统一成 int（失败回 502）。"""
    try:
        return int(status)
    except Exception:
        return 502


def _handle_envelope_account_cooldown(account, exc, model=None, session_key=None):
    """信封层非 200（如 403 队列满/Token被拒、429 等）冷却该账号并解绑会话，触发下次选号轮换。"""
    if not account or not hasattr(account, "note_error"):
        return
    status_int = _to_int_status(getattr(exc, "status", 502))
    detail = getattr(exc, "detail", "") or ""
    if session_key and POOL:
        try:
            POOL.affinity.unbind(session_key)
        except Exception:
            pass
    total = len(POOL.accounts) if POOL else 1
    # 针对 10605 / retryAfterSeconds 解析冷却时长
    retry_secs = 30
    m = re.search(r'retryAfterSeconds\D+(\d+)', detail)
    if m:
        try:
            retry_secs = int(m.group(1))
        except Exception:
            pass
    if status_int == 429:
        account.note_error("envelope 429", model=model, cooldown=retry_secs)
        log("account %s throttled via envelope (429), cooling for %ds"
            % (account.uid[:8], retry_secs), level="WARN", tag="chat")
    elif status_int in (401, 403):
        dead = qoder_accounts.session_dead(detail)
        # 10605 排队冷却为模型级或账号级
        is_queue = "10605" in detail or "isQueued" in detail
        cd = 300 if dead else (retry_secs if is_queue else 60)
        account.note_error("envelope HTTP %s: %s" % (status_int, detail[:80]),
                           cooldown=cd, single_account=(total <= 1),
                           model=model if is_queue else None)
        log("account %s rejected via envelope (HTTP %s, queue=%s), cooling for %ds and rotating"
            % (account.uid[:8], status_int, is_queue, cd), level="WARN", tag="chat")
        if dead:
            account.enabled = False
            account.save(ACCOUNTS_DIR) if account.path else None
            log("account %s session dead via envelope (TOKEN_EXPIRE) - disabled"
                % account.uid[:8], level="ERROR")
    else:
        account.note_error("envelope HTTP %s: %s" % (status_int, detail[:80]),
                           cooldown=15, single_account=(total <= 1))

def should_retry_envelope(exc, emitted_bytes, attempt):
    """流内错误信封是否值得**重开上游**再试。

    关键前提（access log 记 200 而业务错 418 的根因）：上游先以 HTTP200
    建流，provider 故障以 SSE 信封 statusCodeValue=418 投递——此时 urlopen
    层重试覆盖不到。只要 **尚未向客户端发出任何字节**、未超预算、且错误属
    瞬时类（418/5xx/provider_error，且非客户端参数错），就值得重开。
    """
    if emitted_bytes:
        return False
    if attempt >= TRANSIENT_MAX_RETRIES:
        return False
    status_int = _to_int_status(getattr(exc, "status", 502))
    detail = getattr(exc, "detail", "") or ""
    # 401/403 (含 10605 排队被拒) 与 429 在未吐出字节前均允许通过 reopen 换号重试
    if status_int in (401, 403, 429):
        return True
    return _is_transient_upstream(status_int, detail)


def aggregate_with_envelope_retry(resp, payload, session_key, realm, model,
                                  holder, account):
    """非流式：流内瞬时错误信封 / 传输抖动 -> 重开上游重新聚合。

    仅用于客户端尚未收到任何字节的非流式路径。返回 (chat_obj, account)：
      - 最终信封错误以 UpstreamStatus 抛出（调用方既有分支处理：记账+友好提示）
      - 重开时 open_upstream 的 RateLimited/HTTPError 记日志后仍以**原信封**
        错误上抛（原错误才是本次请求的真实结果，且其分支已具备友好映射）
      - 传输层瞬时错误（TLS EOF 等）同样触发重开
    传入的 resp 由调用方的 with/finally 关闭；本函数只负责关闭重开的新连接。
    """
    cur = resp
    try:
        for attempt in range(TRANSIENT_MAX_RETRIES + 1):
            try:
                obj = aggregate_stream(cur, model, None, holder=holder,
                                       allowed_names=_tool_names_from_payload(payload))
                return obj, account
            except UpstreamStatus as exc:
                _handle_envelope_account_cooldown(account, exc, model=model, session_key=session_key)
                if not should_retry_envelope(exc, False, attempt):
                    raise
                log("in-stream envelope status %s on model '%s' "
                    "(try %d/%d), reopening upstream"
                    % (exc.status, model, attempt + 1,
                       TRANSIENT_MAX_RETRIES + 1), level="WARN", tag="chat")
                time.sleep(attempt + 1)
                try:
                    new_resp, account, _ = open_upstream(
                        payload, session_key=session_key, target_realm=realm)
                except Exception as reopen_exc:
                    log("reopen after envelope error failed: %s"
                        % str(reopen_exc)[:160], level="WARN", tag="chat")
                    raise exc      # 以原信封错误进入既有处理路径
                if cur is not resp:
                    try:
                        cur.close()
                    except Exception:
                        pass
                cur = new_resp
            except Exception as exc:
                if attempt >= TRANSIENT_MAX_RETRIES or \
                        not _is_transient_transport(exc):
                    raise
                log("in-stream transport error on model '%s' (try %d/%d): "
                    "%s - reopening upstream"
                    % (model, attempt + 1, TRANSIENT_MAX_RETRIES + 1,
                       str(exc)[:120]), level="WARN", tag="chat")
                time.sleep(attempt + 1)
                try:
                    new_resp, account, _ = open_upstream(
                        payload, session_key=session_key, target_realm=realm)
                except Exception as reopen_exc:
                    log("reopen after transport error failed: %s"
                        % str(reopen_exc)[:160], level="WARN", tag="chat")
                    raise exc
                if cur is not resp:
                    try:
                        cur.close()
                    except Exception:
                        pass
                cur = new_resp
    finally:
        if cur is not resp:
            try:
                cur.close()
            except Exception:
                pass


def open_upstream(payload, session_key=None, target_realm=None):
    """构造 COSY 签名请求并打开上游 SSE。返回 (resp, account, encoded)。

    账号轮换规则：
      - 401/403：凭证被拒 -> 冷却该账号（单账号池时短冷却）换号
      - 429    ：模型粒度频控 -> 解析 reset 时间做模型冷却换号
      - 418/5xx/provider_error（瞬时上游故障）-> 同账号快速重试 2 次
                （1s/2s 退避），仍失败短冷却(15s/单账号3s)换号
      - 其他 4xx（含客户端参数错）-> 快速失败，冷却换号，不重试
    全部账号失败后抛 RateLimited 或最后一个错误。
    """
    realm = target_realm or detect_model_realm(payload.get("model")) or CURRENT_REALM
    model = str(payload.get("model") or "")
    model_key = qoder_catalog.resolve_upstream_key(model, realm=realm)

    representative = POOL.pick(realm=realm) if POOL else None
    body_obj = build_qoder_body(payload, representative, model_key, realm=realm)
    encoded = qoder_encode(json.dumps(body_obj, ensure_ascii=False).encode("utf-8"))

    if not session_key:
        session_key = derive_affinity_key(body_obj.get("messages"))
        if session_key and AFFINITY_DEBUG:
            log("affinity: derived %s for %d msgs"
                % (session_key, len(body_obj.get("messages") or [])))

    total = max(1, POOL.count_ready(realm, model=model)) if POOL else 1
    tried = set()
    last_error = None
    last_429 = None
    last_429_detail = ""
    waited_cool = False

    # 额外 +2 次迭代预算：只供“短错误冷却等待续上”使用（正常轮换仍由
    # tried 集合自然终止）。
    for _ in range(total + 2):
        account = POOL.pick_for_session(realm=realm, session_key=session_key,
                                        exclude=tried, model=model) if POOL else None
        if account is None:
            if not waited_cool:
                wait = _short_error_cooldown_wait(realm, model,
                                                  exclude=tried)
                if wait > 0:
                    waited_cool = True
                    log("accounts in short error-cooldown for '%s' "
                        "(%.1fs left) - waiting instead of failing"
                        % (model, wait), level="WARN", tag="chat")
                    time.sleep(wait + 0.25)
                    continue     # 冷却到期后重新 pick，服务该请求
            break
        if account.realm != realm:
            if session_key and POOL:
                POOL.affinity.unbind(session_key)
            continue
        tried.add(account.uid)
        # 身份相关的 aliyun_user_type 需要真实账号
        body_obj["aliyun_user_type"] = account.user_type or \
            qoder_sign.DEFAULT_USER_TYPE
        encoded = qoder_encode(
            json.dumps(body_obj, ensure_ascii=False).encode("utf-8"))
        # 官方候选推理主机（国际版 api1/api2/api3）：传输层失败时切换域名，
        # 签名只覆盖 path，换主机不影响签名有效性。
        hosts = gateway_candidates(account.realm)
        host_i = 0
        raw_url = hosts[host_i] + CHAT_PATH

        # ---- 发起请求（瞬时上游故障同账号快速重试） ----
        resp = None
        last_exc = None
        detail = ""
        for tries in range(TRANSIENT_MAX_RETRIES + 1):
            try:
                sess = SESSIONS.get(account)
                headers = sess.headers(encoded, raw_url, model_key=model_key,
                                       sse=True)
                headers["User-Agent"] = CLIENT_UA
                chat_url = validate_public_http_url(raw_url)
                req = urllib.request.Request(chat_url,
                                             data=encoded.encode("utf-8"),
                                             method="POST", headers=headers)
                resp = urllib.request.urlopen(req, timeout=600)
                break
            except urllib.error.HTTPError as exc:
                try:
                    detail = exc.read(600).decode("utf-8", "replace")
                except Exception:
                    detail = ""
                # 错误体只能读一次：把已读详情挂到异常上，处理器二次 read
                # 会拿到残缺/空内容，统一从 qoder_detail 取。
                try:
                    exc.qoder_detail = detail
                except Exception:
                    pass
                last_exc = exc
                # 429/401/403 与客户端参数错：立即进入分类，不重试
                if exc.code in (429, 401, 403):
                    break
                if _is_transient_upstream(exc.code, detail) \
                        and tries < TRANSIENT_MAX_RETRIES:
                    backoff = tries + 1      # 1s, 2s（tries 从 0 计）
                    log("transient upstream HTTP %d on '%s' model '%s' "
                        "(try %d/%d), retry in %ds"
                        % (exc.code, account.uid[:8], model, tries + 1,
                           TRANSIENT_MAX_RETRIES + 1, backoff),
                        level="WARN", tag="chat")
                    time.sleep(backoff)
                    continue
                break
            except Exception as exc:
                last_exc = exc
                detail = ""
                # 传输层瞬时故障（TLS EOF / 连接重置 / 超时）同样原地重试；
                # 若还有官方备用推理域名，优先换域名（对整域故障更有效）。
                if _is_transient_transport(exc) \
                        and tries < TRANSIENT_MAX_RETRIES:
                    if host_i + 1 < len(hosts):
                        host_i += 1
                        raw_url = hosts[host_i] + CHAT_PATH
                        log("transient transport error on '%s' model '%s' "
                            "(try %d/%d): %s - switching gateway host to %s"
                            % (account.uid[:8], model, tries + 1,
                               TRANSIENT_MAX_RETRIES + 1, str(exc)[:120],
                               hosts[host_i]),
                            level="WARN", tag="chat")
                    else:
                        log("transient transport error on '%s' model '%s' "
                            "(try %d/%d): %s - retry in %ds"
                            % (account.uid[:8], model, tries + 1,
                               TRANSIENT_MAX_RETRIES + 1,
                               str(exc)[:120], tries + 1),
                            level="WARN", tag="chat")
                    time.sleep(tries + 1)
                    continue
                break

        if resp is not None:
            account.clear_error(model=model)
            return resp, account, encoded

        exc = last_exc
        # ---- 错误分类（与原有轮换语义一致） ----
        if isinstance(exc, urllib.error.HTTPError):
            if exc.code == 429:
                account.note_error("HTTP 429 (model throttled)", model=model,
                                   cooldown=60)
                log("account %s throttled on '%s' (429), retry in 60s"
                    % (account.uid[:8], model))
                if session_key and POOL:
                    POOL.affinity.unbind(session_key)
                last_error = exc
                last_429 = exc
                last_429_detail = detail
                continue
            if exc.code in (401, 403):
                log("account %s rejected (HTTP %s), rotating"
                    % (account.uid[:8], exc.code))
                if session_key and POOL:
                    POOL.affinity.unbind(session_key)
                dead = qoder_accounts.session_dead(detail)
                account.note_error("HTTP %s %s" % (exc.code, detail[:80]),
                                   cooldown=300 if dead else 60,
                                   single_account=(total <= 1))
                if dead:
                    account.enabled = False
                    account.save(ACCOUNTS_DIR) if account.path else None
                    log("account %s session dead (TOKEN_EXPIRE) - disabled"
                        % account.uid[:8], level="ERROR")
                last_error = exc
                continue
            if _is_transient_upstream(exc.code, detail):
                # 重试后仍是瞬时故障：上游侧问题，短冷却换号（不重罚账号）
                if session_key and POOL:
                    POOL.affinity.unbind(session_key)
                account.note_error(
                    "HTTP %s upstream transient: %s" % (exc.code, detail[:80]),
                    cooldown=15, single_account=(total <= 1))
                log("upstream still transient (HTTP %d) after %d tries on "
                    "'%s' - short cooldown, rotating"
                    % (exc.code, TRANSIENT_MAX_RETRIES + 1, account.uid[:8]),
                    level="WARN", tag="chat")
                last_error = exc
                continue
            # 其他 4xx（客户端参数/请求形态问题）：快速失败，冷却换号
            if 400 <= exc.code < 500:
                if session_key and POOL:
                    POOL.affinity.unbind(session_key)
                account.note_error("HTTP %s: %s" % (exc.code, detail[:80]),
                                   cooldown=60, single_account=(total <= 1))
                last_error = exc
                continue
            raise
        else:
            if session_key and POOL:
                POOL.affinity.unbind(session_key)
            if exc is not None:
                if _is_transient_transport(exc):
                    # 传输抖动重试耗尽：短冷却换号（不重罚账号）
                    account.note_error("transport transient: %s" % str(exc)[:100],
                                       cooldown=15, single_account=(total <= 1))
                    log("upstream transport still failing after %d tries on "
                        "'%s' - short cooldown"
                        % (TRANSIENT_MAX_RETRIES + 1, account.uid[:8]),
                        level="WARN", tag="chat")
                else:
                    account.note_error(str(exc)[:120], cooldown=60,
                                       single_account=(total <= 1))
                last_error = exc
            continue

    if last_error is not None:
        if last_429 is not None:
            raise RateLimited(last_429, last_429_detail,
                              wait=retry_after_seconds(model, realm))
        raise last_error
    throttled, wait = realm_model_throttled(realm, model)
    if throttled:
        raise RateLimited(None, "usage exceeds frequency limit", wait=wait)
    raise RuntimeError(
        "no usable account for realm '%s': all are disabled, cooling down, or expired"
        % realm)


def sse_with_heartbeat(source, send, interval=None, idle_limit=None):
    """在等待上游数据的空档里发送 SSE 注释心跳（`: ping`），并原样转发数据。

    背景：`xhigh` 长上下文请求的上游首字延迟可达 40–71 秒（实测日志），
    这段时间网关对客户端一言不发；客户端/中间代理一旦空闲超时就会断开并
    重连（表现为“一直重连”）。SSE 规范允许以 `:` 开头的注释行，客户端会
    忽略，因此用它做保活。

    source     : 上游数据迭代器（iter_inner_sse 的输出，产出 bytes）
    send       : 发送函数（接收 bytes）
    interval   : 空闲多久发一次心跳（秒），默认 5（可用 QD_SSE_HEARTBEAT 覆盖）
    idle_limit : 单次等待上限（秒），默认 900；超过抛 TimeoutError

    读线程把上游数据放入队列，主线程只在“队列空闲”时补心跳，保证顺序与
    错误传播（上游异常原样在主线程抛出）。
    """
    if interval is None:
        try:
            interval = float(os.environ.get("QD_SSE_HEARTBEAT", "5") or 5)
        except Exception:
            interval = 5.0
    if interval <= 0:
        # 显式关闭心跳：退化为直通
        for item in source:
            yield item
        return
    if idle_limit is None:
        idle_limit = 900.0

    import queue as _queue
    import threading as _threading

    box = _queue.Queue()
    done = object()

    def _reader():
        try:
            for item in source:
                box.put(item)
        except BaseException as exc:          # 上游错误/断连也要送主线程
            box.put(exc)
        finally:
            box.put(done)

    reader = _threading.Thread(target=_reader, daemon=True)
    reader.start()

    waited = 0.0
    while True:
        try:
            item = box.get(timeout=interval)
        except _queue.Empty:
            send(b": ping\n\n")               # SSE 注释：客户端忽略
            waited += interval
            if waited >= idle_limit:
                raise TimeoutError(
                    "upstream produced no data for %.0fs" % waited)
            continue
        if item is done:
            return
        if isinstance(item, BaseException):
            raise item
        waited = 0.0
        yield item


def iter_inner_sse(resp, holder=None):
    """把上游 SSE 信封流解包成标准 OpenAI chunk 的 "data: ..." 行。

    上游帧形态：
        data:{"headers":{...},"body":"<内层 OpenAI chunk JSON 字符串>","statusCodeValue":200}
        data:{"body":"[DONE]"}
        event:finish{...}          <- 计时元数据，忽略

    规则：
      - statusCodeValue != 200 -> 抛 UpstreamStatus（body 为错误详情）
      - body == "[DONE]"       -> 结束迭代
      - 内层 chunk 里的 usage 记入 holder
    """
    for raw in resp:
        if isinstance(raw, bytes):
            try:
                line = raw.decode("utf-8")
            except Exception:
                continue
        else:
            line = raw
        data = strip_data_prefix(line)
        if not data:
            continue
        try:
            outer = json.loads(data)
        except Exception:
            continue
        if not isinstance(outer, dict):
            continue
        status = outer.get("statusCodeValue")
        body = outer.get("body")
        if status not in (None, 200, "200"):
            raise UpstreamStatus(status, body if isinstance(body, str)
                                 else json.dumps(outer, ensure_ascii=False)[:400])
        if not isinstance(body, str):
            continue
        if body == "[DONE]":
            break
        try:
            inner = json.loads(body)
        except Exception:
            continue
        if holder is not None and inner.get("usage") and not holder.get("usage"):
            holder["usage"] = inner["usage"]
        cleaned = clean_chunk(body)
        if not cleaned:
            continue
        yield ("data: " + cleaned + "\n\n").encode("utf-8")


def aggregate_stream(resp, model, resp_id=None, holder=None, allowed_names=None):
    """把上游信封流折叠成一个非流式 chat.completion 对象。

    allowed_names：本次请求声明的工具名集合；用于「泄漏文本回读」的守卫
    （见 parse_leaked_tool_calls / issue #8）。
    """
    content, reasoning, finish = [], [], "stop"
    tool_calls_map = {}
    usage = holder.get("usage") if holder else None
    started = time.time()
    first_chunk_at = None
    created = None

    for line in iter_inner_sse(resp, holder=holder):
        data = strip_data_prefix(line.decode("utf-8", "replace"))
        if not data or data == "[DONE]":
            continue
        try:
            chunk = json.loads(data)
        except Exception:
            continue
        if first_chunk_at is None:
            first_chunk_at = time.time()
        if chunk.get("id"):
            resp_id = chunk["id"]
        if chunk.get("model"):
            model = chunk["model"]
        if chunk.get("created"):
            created = chunk["created"]
        if chunk.get("usage"):
            usage = chunk["usage"]
            if holder is not None:
                holder["usage"] = usage
        for choice in chunk.get("choices") or []:
            delta = choice.get("delta") or {}
            if delta.get("content"):
                _c = delta["content"]
                if isinstance(_c, str):
                    content.append(_c)
                elif isinstance(_c, list):
                    for _p in _c:
                        if isinstance(_p, str):
                            content.append(_p)
                        elif (isinstance(_p, dict)
                              and isinstance(_p.get("text"), str)):
                            content.append(_p["text"])
            if delta.get("reasoning_content"):
                reasoning.append(delta["reasoning_content"])
            for tc in delta.get("tool_calls") or []:
                idx = tc.get("index")
                if idx is None:
                    idx = len(tool_calls_map)
                fn = tc.get("function") or {}
                call_id = tc.get("id")
                fn_name = fn.get("name") or ""
                fn_args = fn.get("arguments") or ""
                if idx not in tool_calls_map:
                    tool_calls_map[idx] = {
                        "id": call_id or _new_id("call_"),
                        "type": tc.get("type") or "function",
                        "function": {"name": fn_name, "arguments": fn_args},
                    }
                else:
                    entry = tool_calls_map[idx]
                    if call_id:
                        entry["id"] = call_id
                    if fn_name:
                        entry["function"]["name"] = \
                            (entry["function"]["name"] or "") + fn_name
                    if fn_args:
                        entry["function"]["arguments"] = \
                            (entry["function"]["arguments"] or "") + fn_args
            fc = delta.get("function_call")
            if fc and isinstance(fc, dict) and fc.get("name"):
                idx = 0
                if idx not in tool_calls_map:
                    tool_calls_map[idx] = {
                        "id": _new_id("call_"),
                        "type": "function",
                        "function": {"name": fc.get("name") or "",
                                     "arguments": fc.get("arguments") or ""},
                    }
                else:
                    entry = tool_calls_map[idx]
                    if fc.get("name") and not entry["function"]["name"]:
                        entry["function"]["name"] = fc["name"]
                    if fc.get("arguments"):
                        entry["function"]["arguments"] += fc["arguments"]
            if choice.get("finish_reason"):
                finish = choice["finish_reason"]

    message = {"role": "assistant", "content": "".join(content)}
    if reasoning:
        message["reasoning_content"] = "".join(reasoning)
    # 回读（issue #8）：上游未给结构化 tool_calls，但正文恰好是网关自己写入
    # 历史的「LEAK_MARKER + JSON 数组」形态（模型照格式复述）——还原为结构化
    # 调用并从正文移除，避免客户端把 JSON 当正文显示、本轮调用不执行。
    if not tool_calls_map and message.get("content"):
        _rec, _clean = parse_leaked_tool_calls(message["content"], allowed_names)
        if _rec:
            message["content"] = _clean
            tool_calls_map = {i: c for i, c in enumerate(_rec)}
        elif _leaked_partial_droppable(message["content"], allowed_names):
            # issue #9：截断的 marker+JSON 回声不还原、也不透传，直接清空正文。
            log("dropped truncated leaked tool-call echo (%d chars) — issue #9"
                % len(message["content"]), level="WARN", tag="chat")
            message["content"] = ""
    # 二次防御：剔除「无函数名」的空 tool_call，防止客户端死等
    if tool_calls_map:
        tool_calls_map = {k: v for k, v in tool_calls_map.items()
                          if (v.get("function") or {}).get("name")}
    if tool_calls_map:
        ordered = [tool_calls_map[k] for k in sorted(tool_calls_map.keys())]
        message["tool_calls"] = ordered
        if finish in ("stop", None):
            finish = "tool_calls"
    elif finish == "tool_calls":
        # 占位被全部过滤掉 -> 降级为正常结束，防止客户端无限挂起
        finish = "stop"
    out = {
        "id": resp_id or "chatcmpl-qoder",
        "object": "chat.completion",
        "created": created or int(time.time()),
        "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
    }
    if usage:
        out["usage"] = usage
    out["elapsed_ms"] = int((time.time() - started) * 1000)
    out["first_chunk_at"] = first_chunk_at
    return out


# ---------------------------------------------------------------------------
# Responses API (/v1/responses) <-> Chat Completions translation
# ---------------------------------------------------------------------------
def local_ip_addresses():
    """Every non-loopback IPv4 address this machine answers on."""
    found = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in found and not ip.startswith("127."):
                found.append(ip)
    except Exception:
        pass
    if not found:
        try:
            probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            probe.connect(("8.8.8.8", 80))
            found.append(probe.getsockname()[0])
            probe.close()
        except Exception:
            pass
    return found


def _new_id(prefix):
    return prefix + uuid.uuid4().hex


def _flatten_content(content):
    """Flatten Responses-style content into text, or OpenAI vision parts."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content)
    texts, parts = [], []
    for piece in content:
        if isinstance(piece, str):
            texts.append(piece)
            parts.append({"type": "text", "text": piece})
            continue
        if not isinstance(piece, dict):
            continue
        ptype = piece.get("type") or ""
        if ptype in ("input_text", "output_text", "text", "summary_text"):
            t = piece.get("text") or ""
            texts.append(t)
            parts.append({"type": "text", "text": t})
        elif ptype in ("input_image", "image_url", "image") or "image_url" in piece:
            url = piece.get("image_url") or piece.get("url")
            if isinstance(url, dict):
                url = url.get("url")
            if not url and piece.get("data"):
                mime = piece.get("mimeType") or piece.get("mime_type") or "image/png"
                url = "data:%s;base64,%s" % (mime, piece["data"])
            if url:
                parts.append({"type": "image_url", "image_url": {"url": url}})
    if any(p.get("type") == "image_url" for p in parts):
        return parts          # multimodal: keep structured parts
    return "\n".join(t for t in texts if t)


# ---------------------------------------------------------------------------
# Responses API "custom" (freeform) tools
#
# Codex 等客户端把文件编辑工具声明为 custom freeform tool：
#     {"type": "custom", "name": "apply_patch", "format": {...grammar...}}
# 上游 chat 端点没有 custom 概念 -> 出站降级为单 "input" 字符串参数的
# function tool，入站再还原为 custom_tool_call。否则工具被静默忽略。
# ---------------------------------------------------------------------------
CUSTOM_TOOL_HINT = (
    "This is a freeform tool. Put the COMPLETE raw payload into the single "
    "'input' string parameter, verbatim. Do not wrap it in JSON, do not wrap "
    "it in markdown code fences, do not add commentary."
)


def _is_custom_tool(tool):
    return isinstance(tool, dict) and str(tool.get("type") or "").lower() == "custom"


def custom_tool_names(tools):
    """Names of tools declared as freeform/custom in a Responses request."""
    names = set()
    for t in tools or []:
        if _is_custom_tool(t) and t.get("name"):
            names.add(str(t["name"]))
    return names


def _downgrade_custom_tool(tool):
    """Rewrite a Responses custom tool into a Chat function tool."""
    desc = tool.get("description") or ""
    fmt = tool.get("format") or {}
    extra = ""
    if isinstance(fmt, dict) and fmt.get("definition"):
        extra = "\n\nGrammar:\n" + str(fmt["definition"])
    elif isinstance(fmt, dict) and fmt.get("syntax"):
        extra = "\n\nGrammar syntax: %s" % str(fmt["syntax"])
    return {
        "type": "function",
        "name": tool.get("name") or "",
        "description": (desc + "\n\n" + CUSTOM_TOOL_HINT + extra).strip(),
        "parameters": {
            "type": "object",
            "properties": {
                "input": {
                    "type": "string",
                    "description": "Complete raw payload for this tool, verbatim.",
                }
            },
            "required": ["input"],
        },
    }


def _tools_for_chat(tools):
    """Downgrade custom tools; leave everything else untouched."""
    out = []
    for t in tools or []:
        if not isinstance(t, dict):
            continue
        out.append(_downgrade_custom_tool(t) if _is_custom_tool(t) else t)
    return out


def _unwrap_custom_input(args):
    """Pull the freeform string back out of an {"input": "..."} argument blob."""
    if not isinstance(args, str):
        return json.dumps(args or "", ensure_ascii=False)
    try:
        parsed = json.loads(args)
    except Exception:
        return args
    if isinstance(parsed, dict):
        val = parsed.get("input")
        if isinstance(val, str):
            return val
        if val is not None:
            return json.dumps(val, ensure_ascii=False)
    if isinstance(parsed, str):
        return parsed
    return args


def responses_to_chat(payload):
    """Translate a Responses API request body into a Chat Completions body."""
    messages = []
    instructions = payload.get("instructions")
    if isinstance(instructions, str) and instructions.strip():
        messages.append({"role": "system", "content": instructions})
    inp = payload.get("input")
    pending_reasoning = ""
    if isinstance(inp, str):
        messages.append({"role": "user", "content": inp})
    elif isinstance(inp, list):
        for item in inp:
            if isinstance(item, str):
                messages.append({"role": "user", "content": item})
                continue
            if not isinstance(item, dict):
                continue
            itype = item.get("type")
            if itype in (None, "message"):
                body = _flatten_content(item.get("content"))
                if body:
                    role = item.get("role") or "user"
                    if role == "developer":
                        role = "system"
                    # 与相邻 function_call 的 assistant 文本合并到同一条，
                    # 避免打断 tool 序列
                    if role == "assistant" and messages \
                            and messages[-1].get("role") == "assistant":
                        prev = messages[-1]
                        if prev.get("content"):
                            prev["content"] = str(prev["content"]) + "\n" + str(body)
                        else:
                            prev["content"] = body
                        if pending_reasoning and "reasoning_content" not in prev:
                            prev["reasoning_content"] = pending_reasoning
                            pending_reasoning = ""
                    else:
                        msg_dict = {"role": role, "content": body}
                        if role == "assistant" and pending_reasoning:
                            msg_dict["reasoning_content"] = pending_reasoning
                            pending_reasoning = ""
                        messages.append(msg_dict)
            elif itype == "reasoning":
                r_text = ""
                summ = item.get("summary")
                if isinstance(summ, list):
                    r_text = "\n".join(p.get("text", "") for p in summ
                                       if isinstance(p, dict) and p.get("text"))
                elif isinstance(summ, str):
                    r_text = summ
                if not r_text:
                    cnt = item.get("content")
                    if isinstance(cnt, str):
                        r_text = cnt
                    elif isinstance(cnt, list):
                        r_text = _flatten_content(cnt)
                if r_text:
                    if messages and messages[-1].get("role") == "assistant":
                        messages[-1]["reasoning_content"] = r_text
                    else:
                        pending_reasoning = r_text
            elif itype == "function_call_output":
                raw_out = item.get("output")
                if isinstance(raw_out, list):
                    content = _flatten_content(raw_out)
                elif isinstance(raw_out, dict):
                    if raw_out.get("type") in ("input_image", "image_url", "image") \
                            or "image_url" in raw_out:
                        content = _flatten_content([raw_out])
                    else:
                        content = json.dumps(raw_out, ensure_ascii=False)
                elif isinstance(raw_out, str):
                    content = raw_out
                else:
                    content = str(raw_out)
                messages.append({
                    "role": "tool",
                    "tool_call_id": item.get("call_id") or "",
                    "content": content,
                })
            elif itype == "function_call":
                tc_item = {
                    "id": item.get("call_id") or item.get("id") or "",
                    "type": "function",
                    "function": {
                        "name": item.get("name") or "",
                        "arguments": item.get("arguments") or "{}",
                    },
                }
                if messages and messages[-1].get("role") == "assistant":
                    prev = messages[-1]
                    if "tool_calls" in prev:
                        prev["tool_calls"].append(tc_item)
                    else:
                        prev["tool_calls"] = [tc_item]
                    if pending_reasoning and "reasoning_content" not in prev:
                        prev["reasoning_content"] = pending_reasoning
                        pending_reasoning = ""
                else:
                    msg_dict = {"role": "assistant", "content": "",
                                "tool_calls": [tc_item]}
                    if pending_reasoning:
                        msg_dict["reasoning_content"] = pending_reasoning
                        pending_reasoning = ""
                    messages.append(msg_dict)
            elif itype == "custom_tool_call":
                raw_input = item.get("input")
                if isinstance(raw_input, (dict, list)):
                    raw_input = json.dumps(raw_input, ensure_ascii=False)
                if not isinstance(raw_input, str):
                    raw_input = "" if raw_input is None else str(raw_input)
                tc_item = {
                    "id": item.get("call_id") or item.get("id") or "",
                    "type": "function",
                    "function": {
                        "name": item.get("name") or "",
                        "arguments": json.dumps({"input": raw_input},
                                                ensure_ascii=False),
                    },
                }
                if messages and messages[-1].get("role") == "assistant":
                    prev = messages[-1]
                    if "tool_calls" in prev:
                        prev["tool_calls"].append(tc_item)
                    else:
                        prev["tool_calls"] = [tc_item]
                    if pending_reasoning and "reasoning_content" not in prev:
                        prev["reasoning_content"] = pending_reasoning
                        pending_reasoning = ""
                else:
                    msg_dict = {"role": "assistant", "content": "",
                                "tool_calls": [tc_item]}
                    if pending_reasoning:
                        msg_dict["reasoning_content"] = pending_reasoning
                        pending_reasoning = ""
                    messages.append(msg_dict)
            elif itype == "custom_tool_call_output":
                raw_out = item.get("output")
                if isinstance(raw_out, list):
                    content = _flatten_content(raw_out)
                elif isinstance(raw_out, dict):
                    content = json.dumps(raw_out, ensure_ascii=False)
                elif isinstance(raw_out, str):
                    content = raw_out
                else:
                    content = str(raw_out)
                messages.append({
                    "role": "tool",
                    "tool_call_id": item.get("call_id") or "",
                    "content": content,
                })
            else:
                # 未知 item 不静默丢弃：丢工具调用会让上游记录不一致
                log("responses: WARNING unhandled input item type=%r keys=%s"
                    % (itype, sorted(item.keys())[:8]))
    chat = {"model": payload.get("model"), "messages": messages}
    for key in ("temperature", "top_p", "seed"):
        if payload.get(key) is not None:
            chat[key] = payload[key]
    if payload.get("max_output_tokens") is not None:
        chat["max_tokens"] = payload["max_output_tokens"]
    effort = None
    reasoning = payload.get("reasoning")
    if isinstance(reasoning, dict):
        effort = reasoning.get("effort")
    if not effort:
        effort = payload.get("reasoning_effort")
    if effort:
        chat["reasoning_effort"] = effort
    if payload.get("tools"):
        chat["tools"] = _tools_for_chat(payload["tools"])
    if payload.get("tool_choice"):
        chat["tool_choice"] = payload["tool_choice"]
    if payload.get("parallel_tool_calls") is not None:
        chat["parallel_tool_calls"] = payload["parallel_tool_calls"]
    return chat


def _responses_usage(u):
    if not u:
        return None
    det = u.get("completion_tokens_details") or {}
    pdet = u.get("prompt_tokens_details") or {}
    return {
        "input_tokens": u.get("prompt_tokens") or 0,
        "input_tokens_details": {
            "cached_tokens": u.get("prompt_cache_hit_tokens")
            or det.get("cached_tokens") or pdet.get("cached_tokens") or 0,
        },
        "output_tokens": u.get("completion_tokens") or 0,
        "output_tokens_details": {
            "reasoning_tokens": det.get("reasoning_tokens") or 0},
        "total_tokens": u.get("total_tokens") or 0,
    }


def chat_to_response(chat_obj, model, custom_names=None):
    """Fold a Chat Completions object into a Responses API response object.

    custom_names: the set of tool names the client declared as freeform.
    Calls to those tools are re-inflated into custom_tool_call items so
    clients such as Codex recognise them.
    """
    custom_names = custom_names or set()
    choice = (chat_obj.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    text = msg.get("content") or ""
    reasoning = msg.get("reasoning_content") or ""
    output = []
    if reasoning:
        output.append({
            "id": _new_id("rs_"),
            "type": "reasoning",
            "status": "completed",
            "summary": [{"type": "summary_text", "text": reasoning}],
        })
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function") or {}
        call_id = tc.get("id") or _new_id("call_")
        name = fn.get("name") or ""
        if name and name in custom_names:
            output.append({
                "id": _new_id("ctc_"),
                "type": "custom_tool_call",
                "status": "completed",
                "call_id": call_id,
                "name": name,
                "input": _unwrap_custom_input(fn.get("arguments") or ""),
            })
        else:
            output.append({
                "id": _new_id("fc_"),
                "type": "function_call",
                "status": "completed",
                "call_id": call_id,
                "name": name,
                "arguments": fn.get("arguments") or "{}",
            })
    # DeepSeek DSML 工具调用回退
    if not (msg.get("tool_calls")):
        dsml_calls, clean_t = parse_dsml_tool_calls(text)
        if dsml_calls:
            for dc in dsml_calls:
                output.append({
                    "id": _new_id("fc_"),
                    "type": "function_call",
                    "status": "completed",
                    "call_id": dc.get("id") or _new_id("call_"),
                    "name": dc.get("name") or "",
                    "arguments": dc.get("arguments") or "{}",
                })
            text = clean_t
    if text or not output:
        output.append({
            "id": _new_id("msg_"),
            "type": "message",
            "status": "completed",
            "role": "assistant",
            "content": [{"type": "output_text", "text": text,
                         "annotations": []}] if text else [],
        })
    finish = choice.get("finish_reason") or "stop"
    obj = {
        "id": _new_id("resp_"),
        "object": "response",
        "created_at": int(time.time()),
        "status": "completed" if finish != "length" else "incomplete",
        "model": model,
        "output": output,
        "output_text": text,
        "parallel_tool_calls": True,
        "tool_choice": "auto",
        "tools": [],
        "metadata": {},
    }
    u = _responses_usage(chat_obj.get("usage"))
    if u:
        obj["usage"] = u
    if finish == "length":
        obj["incomplete_details"] = {"reason": "max_output_tokens"}
    return obj


def stream_responses_events(inner_lines, model, holder):
    """Yield Responses-API SSE frames translated from chat-completions chunks.

    inner_lines: 已解包的 "data: {chunk}" 行迭代器（iter_inner_sse 输出）。
    """
    resp_id, msg_id, rs_id = _new_id("resp_"), _new_id("msg_"), _new_id("rs_")
    created = int(time.time())
    # 上游重开后本生成器会被重建：若 seq 从 0 重来，客户端看到的
    # sequence_number 会回退。从 holder 恢复历史最大号，保证严格单调。
    seq = int(holder.get("seq") or 0) if isinstance(holder, dict) else 0
    if isinstance(holder, dict):
        holder["resp_id"] = resp_id
    text_parts, reason_parts = [], []
    outputs = []
    reason_index = None
    msg_index = None
    finish = "stop"
    usage = None
    tool_calls_map = {}
    text_buffer = ""
    dsml_tool_calls = []
    custom_names = set(holder.get("custom_names") or ())

    def resp_obj(status):
        obj = {
            "id": resp_id,
            "object": "response",
            "created_at": created,
            "status": status,
            "model": model,
            "output": [o for o in outputs if o],
            "output_text": "".join(text_parts),
            "parallel_tool_calls": True,
            "tool_choice": "auto",
            "tools": [],
            "metadata": {},
        }
        u = _responses_usage(usage)
        if u:
            obj["usage"] = u
        return obj

    def ev(etype, payload_obj):
        nonlocal seq
        seq += 1
        if isinstance(holder, dict):
            holder["seq"] = seq        # 供外层补发终态事件时续号
        data = {"type": etype, "sequence_number": seq}
        data.update(payload_obj)
        body = json.dumps(data, ensure_ascii=False)
        return ("event: " + etype + "\ndata: " + body + "\n\n").encode("utf-8")

    def reason_item(status):
        return {
            "id": rs_id,
            "type": "reasoning",
            "status": status,
            "summary": [{"type": "summary_text", "text": "".join(reason_parts)}],
        }

    def msg_item(status):
        item = {"id": msg_id, "type": "message", "status": status,
                "role": "assistant", "content": []}
        if text_parts:
            item["content"] = [{"type": "output_text",
                                "text": "".join(text_parts),
                                "annotations": []}]
        return item

    yield ev("response.created", {"response": resp_obj("in_progress")})
    yield ev("response.in_progress", {"response": resp_obj("in_progress")})
    for raw in inner_lines:
        data = strip_data_prefix(raw.decode("utf-8", "replace")
                                 if isinstance(raw, bytes) else raw)
        if not data or data == "[DONE]":
            continue
        try:
            chunk = json.loads(data)
        except Exception:
            continue
        if usage is None and chunk.get("usage"):
            usage = chunk["usage"]
            holder["usage"] = usage
        for choice in chunk.get("choices") or []:
            delta = choice.get("delta") or {}
            piece = delta.get("reasoning_content")
            if piece:
                if reason_index is None:
                    reason_index = len(outputs)
                    outputs.append(None)
                    yield ev("response.output_item.added",
                             {"output_index": reason_index,
                              "item": reason_item("in_progress")})
                    yield ev("response.reasoning_summary_part.added", {
                        "item_id": rs_id, "output_index": reason_index,
                        "summary_index": 0,
                        "part": {"type": "summary_text", "text": ""},
                    })
                reason_parts.append(piece)
                yield ev("response.reasoning_summary_text.delta", {
                    "item_id": rs_id, "output_index": reason_index,
                    "summary_index": 0, "delta": piece,
                })
            for tc in delta.get("tool_calls") or []:
                idx = tc.get("index", 0)
                fn = tc.get("function") or {}
                fn_name = fn.get("name") or ""
                fn_args = fn.get("arguments") or ""
                call_id = tc.get("id") or ""
                if idx not in tool_calls_map:
                    out_idx = len(outputs)
                    outputs.append(None)
                    c_id = call_id or _new_id("call_")
                    is_custom = bool(fn_name) and fn_name in custom_names
                    entry = {
                        "output_index": out_idx,
                        "id": c_id,
                        "name": fn_name,
                        "arguments": fn_args,
                        "custom": is_custom,
                        "item_id": _new_id("ctc_" if is_custom else "fc_"),
                    }
                    tool_calls_map[idx] = entry
                    item = {"id": entry["item_id"], "status": "in_progress",
                            "call_id": c_id, "name": fn_name}
                    if is_custom:
                        item["type"] = "custom_tool_call"
                        item["input"] = ""
                    else:
                        item["type"] = "function_call"
                        item["arguments"] = ""
                    yield ev("response.output_item.added",
                             {"output_index": out_idx, "item": item})
                else:
                    entry = tool_calls_map[idx]
                    if fn_name and not entry["name"]:
                        entry["name"] = fn_name
                        if fn_name in custom_names:
                            entry["custom"] = True
                    if fn_args:
                        entry["arguments"] += fn_args
                        if entry.get("custom"):
                            yield ev("response.custom_tool_call_input.delta", {
                                "output_index": entry["output_index"],
                                "item_id": entry["item_id"],
                                "call_id": entry["id"],
                                "delta": fn_args,
                            })
                        else:
                            yield ev("response.function_call_arguments.delta", {
                                "output_index": entry["output_index"],
                                "call_id": entry["id"],
                                "delta": fn_args,
                            })
            piece = delta.get("content")
            if piece:
                if msg_index is None:
                    if reason_index is not None:
                        full_r = "".join(reason_parts)
                        yield ev("response.reasoning_summary_text.done", {
                            "item_id": rs_id, "output_index": reason_index,
                            "summary_index": 0, "text": full_r,
                        })
                        yield ev("response.reasoning_summary_part.done", {
                            "item_id": rs_id, "output_index": reason_index,
                            "summary_index": 0,
                            "part": {"type": "summary_text", "text": full_r},
                        })
                        outputs[reason_index] = reason_item("completed")
                        yield ev("response.output_item.done",
                                 {"output_index": reason_index,
                                  "item": outputs[reason_index]})
                    msg_index = len(outputs)
                    outputs.append(None)
                    yield ev("response.output_item.added", {
                        "output_index": msg_index,
                        "item": {"id": msg_id, "type": "message",
                                 "status": "in_progress", "role": "assistant",
                                 "content": []},
                    })
                    yield ev("response.content_part.added", {
                        "item_id": msg_id, "output_index": msg_index,
                        "content_index": 0,
                        "part": {"type": "output_text", "text": "",
                                 "annotations": []},
                    })
                # DSML 缓冲：不把原始 DSML 标签流给客户端
                text_buffer += piece
                # 泄漏回读（issue #8）：正文还一个字没吐、缓冲仍是
                # 「marker + JSON 数组」候选时先压住，流末统一判定。
                _leak_defer = not any(text_parts) and _leak_prefix_hold(
                    text_buffer)
                while text_buffer and not _leak_defer:
                    idx = text_buffer.find("<")
                    if idx == -1:
                        text_parts.append(text_buffer)
                        yield ev("response.output_text.delta", {
                            "item_id": msg_id, "output_index": msg_index,
                            "content_index": 0, "delta": text_buffer,
                        })
                        text_buffer = ""
                        break
                    m = DSML_CALLS_RE.search(text_buffer)
                    if m and m.start() == idx:
                        if idx > 0:
                            lead = text_buffer[:idx]
                            text_parts.append(lead)
                            yield ev("response.output_text.delta", {
                                "item_id": msg_id, "output_index": msg_index,
                                "content_index": 0, "delta": lead,
                            })
                        calls_found, _ = parse_dsml_tool_calls(m.group(0))
                        if calls_found:
                            dsml_tool_calls.extend(calls_found)
                        text_buffer = text_buffer[m.end():]
                        continue
                    cand = text_buffer[idx:idx + 30]
                    is_cand = ("DSML" in cand) or (
                        len(cand) < 10 and not any(c in cand
                                                   for c in (" ", "\t", "\n", ">")))
                    if is_cand:
                        lead = text_buffer[:idx]
                        text_parts.append(lead)
                        yield ev("response.output_text.delta", {
                            "item_id": msg_id, "output_index": msg_index,
                            "content_index": 0, "delta": lead,
                        })
                        text_buffer = text_buffer[idx:]
                        break
                    else:
                        next_lt = text_buffer[idx + 1:].find("<")
                        if next_lt != -1:
                            flush_len = idx + 1 + next_lt
                            lead = text_buffer[:flush_len]
                            text_parts.append(lead)
                            yield ev("response.output_text.delta", {
                                "item_id": msg_id, "output_index": msg_index,
                                "content_index": 0, "delta": lead,
                            })
                            text_buffer = text_buffer[flush_len:]
                        else:
                            text_parts.append(text_buffer)
                            yield ev("response.output_text.delta", {
                                "item_id": msg_id, "output_index": msg_index,
                                "content_index": 0, "delta": text_buffer,
                            })
                            text_buffer = ""
                            break
            if choice.get("finish_reason"):
                finish = choice["finish_reason"]
    if reason_index is not None and outputs[reason_index] is None:
        full_r = "".join(reason_parts)
        yield ev("response.reasoning_summary_text.done", {
            "item_id": rs_id, "output_index": reason_index,
            "summary_index": 0, "text": full_r,
        })
        yield ev("response.reasoning_summary_part.done", {
            "item_id": rs_id, "output_index": reason_index,
            "summary_index": 0,
            "part": {"type": "summary_text", "text": full_r},
        })
        outputs[reason_index] = reason_item("completed")
        yield ev("response.output_item.done",
                 {"output_index": reason_index, "item": outputs[reason_index]})
    # 1. 已完成的结构化工具调用
    for idx in sorted(tool_calls_map.keys()):
        entry = tool_calls_map[idx]
        if entry.get("custom"):
            yield ev("response.custom_tool_call_input.done", {
                "output_index": entry["output_index"],
                "item_id": entry["item_id"],
                "call_id": entry["id"],
                "input": _unwrap_custom_input(entry["arguments"]),
            })
            fc_item = {
                "id": entry["item_id"],
                "type": "custom_tool_call",
                "status": "completed",
                "call_id": entry["id"],
                "name": entry["name"],
                "input": _unwrap_custom_input(entry["arguments"]),
            }
        else:
            yield ev("response.function_call_arguments.done", {
                "output_index": entry["output_index"],
                "call_id": entry["id"],
                "arguments": entry["arguments"],
            })
            fc_item = {
                "id": entry["item_id"],
                "type": "function_call",
                "status": "completed",
                "call_id": entry["id"],
                "name": entry["name"],
                "arguments": entry["arguments"],
            }
        outputs[entry["output_index"]] = fc_item
        yield ev("response.output_item.done",
                 {"output_index": entry["output_index"], "item": fc_item})
    # 冲刷剩余缓冲文本
    if text_buffer:
        leak_calls, leak_clean = (None, text_buffer)
        dropped_leak = False
        if not any(text_parts):
            leak_calls, leak_clean = parse_leaked_tool_calls(
                text_buffer, holder.get("allowed_names"))
            if leak_calls is None and _leaked_partial_droppable(
                    text_buffer, holder.get("allowed_names")):
                dropped_leak = True
        if leak_calls:
            dsml_tool_calls.extend(
                {"id": c.get("id"),
                 "name": (c.get("function") or {}).get("name") or "",
                 "arguments": (c.get("function") or {}).get("arguments") or "{}"}
                for c in leak_calls)
            calls_rem, clean_rem = None, leak_clean
        elif dropped_leak:
            # issue #9：截断的 marker+JSON 回声 -> 不输出文本、不走 DSML 回退。
            log("dropped truncated leaked tool-call echo (%d chars) — issue #9"
                % len(text_buffer), level="WARN", tag="chat")
            calls_rem, clean_rem = None, None
        else:
            calls_rem, clean_rem = parse_dsml_tool_calls(text_buffer)
        if calls_rem:
            dsml_tool_calls.extend(calls_rem)
        if clean_rem:
            text_parts.append(clean_rem)
            if msg_index is not None:
                yield ev("response.output_text.delta", {
                    "item_id": msg_id, "output_index": msg_index,
                    "content_index": 0, "delta": clean_rem,
                })
        text_buffer = ""
    # 2. DSML 回退：无结构化工具调用时用解析出的 DSML 调用
    full_text = "".join(text_parts)
    dsml_calls = dsml_tool_calls
    if not dsml_calls:
        extra_calls, clean_text = parse_dsml_tool_calls(full_text)
        if extra_calls:
            dsml_calls = extra_calls
            full_text = clean_text
    if dsml_calls and not tool_calls_map:
        for dc in dsml_calls:
            out_idx = len(outputs)
            fc_item = {
                "id": _new_id("fc_"),
                "type": "function_call",
                "status": "completed",
                "call_id": dc.get("id") or _new_id("call_"),
                "name": dc.get("name") or "",
                "arguments": dc.get("arguments") or "{}",
            }
            outputs.append(fc_item)
            yield ev("response.output_item.added", {
                "output_index": out_idx,
                "item": dict(fc_item, status="in_progress", arguments=""),
            })
            yield ev("response.function_call_arguments.delta", {
                "output_index": out_idx,
                "call_id": fc_item["call_id"],
                "delta": fc_item["arguments"],
            })
            yield ev("response.function_call_arguments.done", {
                "output_index": out_idx,
                "call_id": fc_item["call_id"],
                "arguments": fc_item["arguments"],
            })
            yield ev("response.output_item.done",
                     {"output_index": out_idx, "item": fc_item})
    # 3. 有文本或没有任何输出项时，补 message 项
    has_other_items = any(o for o in outputs if o)
    if msg_index is not None or full_text or not has_other_items:
        if msg_index is None:
            msg_index = len(outputs)
            outputs.append(None)
            yield ev("response.output_item.added", {
                "output_index": msg_index,
                "item": {"id": msg_id, "type": "message",
                         "status": "in_progress", "role": "assistant",
                         "content": []},
            })
            yield ev("response.content_part.added", {
                "item_id": msg_id, "output_index": msg_index,
                "content_index": 0,
                "part": {"type": "output_text", "text": "",
                         "annotations": []},
            })
        yield ev("response.output_text.done", {
            "item_id": msg_id, "output_index": msg_index,
            "content_index": 0, "text": full_text,
        })
        yield ev("response.content_part.done", {
            "item_id": msg_id, "output_index": msg_index,
            "content_index": 0,
            "part": {"type": "output_text", "text": full_text,
                     "annotations": []},
        })
        outputs[msg_index] = msg_item("completed")
        yield ev("response.output_item.done",
                 {"output_index": msg_index, "item": outputs[msg_index]})
    status = "completed" if finish != "length" else "incomplete"
    final = resp_obj(status)
    if finish == "length":
        final["incomplete_details"] = {"reason": "max_output_tokens"}
    yield ev("response.completed", {"response": final})


def _responses_failed_frame(holder, code, message):
    """Responses 流的终态失败事件（response.failed）。

    stream_responses_events 的 ev() 闭包把最新 sequence_number 同步进
    holder["seq"]，这里在其上 +1 续号，保证严格大于已发出的事件——
    Responses 客户端只认终态事件，缺了它会一直等（原实现只 _sse_end）。
    """
    seq = int((holder or {}).get("seq") or 0) + 1
    data = {
        "type": "response.failed",
        "sequence_number": seq,
        "response": {
            "id": (holder or {}).get("resp_id") or _new_id("resp_"),
            "object": "response",
            "status": "failed",
            "error": {"code": str(code), "message": message},
        },
    }
    return ("event: response.failed\ndata: "
            + json.dumps(data, ensure_ascii=False) + "\n\n").encode("utf-8")


# ---------------------------------------------------------------------------
# HTTP layer
# ---------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    # Which configured API key the caller used, set by _key_ok(). Its bound
    # realm decides the upstream exit for this request alone.
    key_entry = None

    def handle(self):
        try:
            super(Handler, self).handle()
        except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError):
            pass

    def finish(self):
        try:
            super(Handler, self).finish()
        except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError):
            pass

    server_version = "qd-proxy/" + VERSION

    def log_message(self, fmt, *args):
        # 静默看板高频轮询的正常 200 GET，异常状态与业务操作照常记录。
        try:
            req_path = (getattr(self, "path", None)
                        or (args[0] if args else "")).split("?")[0]
            if req_path == "/favicon.ico":
                return           # 浏览器自动请求的 404 不刷屏
            status_code = int(args[1]) if len(args) > 1 \
                and str(args[1]).isdigit() else 200
            if status_code < 400 and getattr(self, "command", "GET") == "GET":
                quiet_prefixes = (
                    "/logs", "/usage", "/accounts", "/scheduler",
                    "/health", "/panel/status", "/realm",
                )
                if any(req_path == p or req_path.startswith(p + "/")
                       for p in quiet_prefixes):
                    return
        except Exception:
            pass
        log(fmt % args)

    def _sse_begin(self):
        """开始一个 HTTP/1.1 SSE 流：用 chunked 编码，保持连接可复用。

        之前用 `Connection: close` + 裸写字节：客户端（连接池型 harness）
        会把该连接视为可复用，下一次请求落在已半关闭的连接上，表现为
        “一直重连 / 连不上”。改为 chunked 后，流结束发 0 长度的终止块，
        连接保持 keep-alive，可安全复用。
        """
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Transfer-Encoding", "chunked")
        if cors_origin_allowed(self.path):
            self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self._chunked = True

    def _sse_write(self, data):
        """按 chunked 编码写一段数据（data: bytes）。"""
        if not data:
            return
        if not getattr(self, "_chunked", False):
            # 理论上不会发生；保守回退成裸写
            self.wfile.write(data)
            self.wfile.flush()
            return
        self.wfile.write(("%x\r\n" % len(data)).encode("ascii"))
        self.wfile.write(data)
        self.wfile.write(b"\r\n")
        self.wfile.flush()

    def _sse_end(self):
        """结束 chunked 流（写 0 长度终止块），连接保持可复用。"""
        if not getattr(self, "_chunked", False):
            return
        try:
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except Exception:
            pass
        self._chunked = False

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if cors_origin_allowed(self.path):
            self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _error(self, code, message, err_type="server_error"):
        self._json(code, {"error": {"message": message, "type": err_type,
                                    "code": code}})

    def _rate_limited(self, exc):
        """429 with Retry-After, so clients back off instead of hammering."""
        wait = max(1, int(getattr(exc, "wait", 60) or 60))
        body = json.dumps({
            "error": {
                "message": ("upstream rate limit reached for this model; "
                            "retry in %ds" % wait)
                + ((" - " + exc.detail[:200]) if getattr(exc, "detail", "") else ""),
                "type": "rate_limit_error",
                "code": 429,
                "retry_after": wait,
            }
        }, ensure_ascii=False).encode("utf-8")
        self.send_response(429)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Retry-After", str(wait))
        if cors_origin_allowed(self.path):
            self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _download(self, filename, obj):
        """Send a JSON document as a browser download.

        Content-Disposition is quoted because the filename is generated from
        user-controlled parts (the realm filter) and could otherwise break the
        header or allow a response-splitting attempt.
        """
        body = json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8")
        safe = re.sub(r'[^A-Za-z0-9._-]', "_", str(filename))[:120] \
            or "export.json"
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Disposition",
                         'attachment; filename="%s"' % safe)
        self.send_header("Cache-Control", "no-store")
        if cors_origin_allowed(self.path):
            self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _supplied_key(self):
        """The key the caller presented, from the header or the ?key= query."""
        supplied = (self.headers.get("Authorization") or "").removeprefix(
            "Bearer ").strip()
        if supplied:
            return supplied
        # 浏览器顶层导航无法设置头，所以 ?key= 也接受（看板跨设备打开用）。
        try:
            query = parse_qs(urlparse(self.path).query)
            return (query.get("key") or [""])[0].strip()
        except Exception:
            return ""

    def _key_ok(self):
        """True when the request carries a right key (or no key is needed)."""
        # 面板会话同时解锁管理 API，浏览器无需在 localStorage 存 API key。
        if self._panel_ok():
            return True
        self.key_entry = identify_key(self._supplied_key())
        if self.key_entry:
            return True
        if not auth_required():
            return True
        return False

    def _key_realm(self):
        """Realm bound to the key this request used, or "" when unbound."""
        return (self.key_entry or {}).get("realm") or ""

    def _cross_realm_error(self, model, realm):
        """解释模型/出口错配，替代上游晦涩的 403。"""
        if not realm or not model:
            return ""
        owner = exclusive_realm(model)
        if not owner or owner == realm:
            return ""
        name = (self.key_entry or {}).get("name") or "当前 Key"
        served = "国内版" if owner == "cn" else "国际版"
        used = "国内版" if realm == "cn" else "国际版"
        return ("模型 %s 只在%s提供，但「%s」绑定的是%s出口。"
                "请改用对应出口的 Key，或把该 Key 的出口改为「跟随面板切换」。"
                % (model, served, name, used))

    def _request_realm(self, explicit=None):
        """Pick the upstream exit for this request.

        Priority: an explicit ?realm= argument, then the realm bound to the
        API key, then the X-Realm header / ?realm= query, and finally the
        global switch. Returning None lets open_upstream() fall back to
        model-based detection.
        """
        if explicit:
            return explicit
        bound = self._key_realm()
        if bound:
            return bound
        header = self.headers.get("X-Realm")
        if header:
            return header
        try:
            return parse_qs(urlparse(self.path).query).get("realm", [None])[0]
        except Exception:
            return None

    def _authorized(self):
        if self._key_ok():
            return True
        self._error(401, "invalid api key", "invalid_request_error")
        return False

    # ---- web panel access ----
    def _panel_token(self):
        """Session token from the X-Panel-Token header.

        Deliberately header-only: a token in the query string leaks through
        browser history, the Referer header and any reverse-proxy access log.
        """
        return (self.headers.get("X-Panel-Token") or "").strip()

    def _panel_ok(self):
        return PANEL.valid(self._panel_token())

    @staticmethod
    def _is_panel_route(path):
        """Management endpoints shown in the web panel.

        Model listings stay reachable with the API key alone so that plain
        OpenAI clients can keep discovering models.
        """
        if path.startswith("/accounts"):
            return True
        if path.startswith("/usage") or path.startswith("/v1/usage"):
            return True
        if path.startswith("/tasks") or path.startswith("/scheduler"):
            return True
        if path.startswith("/settings"):
            return True
        if path.startswith("/logs"):
            return True
        if path.startswith("/diag"):
            return True
        if path.startswith("/update"):
            return True
        return False

    def do_OPTIONS(self):
        self.send_response(204)
        if cors_origin_allowed(self.path):
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "*")
            self.send_header("Access-Control-Allow-Methods",
                             "GET, POST, OPTIONS")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        if self._is_panel_route(path) and not self._panel_ok():
            return self._error(401, "panel password required",
                               "invalid_request_error")
        if path in ("/", "/dashboard", "/ui"):
            return self._dashboard()
        if path == "/panel/status":
            # 不带 token 也要回答：看板需要先知道是否显示登录页。
            info = {
                "panel_password_required": True,
                "panel_password_is_default":
                    qoder_settings.panel_password_is_default(ACCOUNTS_DIR),
                "authenticated": self._panel_ok(),
            }
            info["api_key_set"] = bool(API_KEY)
            return self._json(200, info)
        if path in ("/ping", "/healthz", "/livez", "/readyz"):
            # 极简探活端点：**不需要面板密码/API Key、不查账号池**。
            # 客户端/守护脚本常用 GET /ping 判活，之前返回 404 会被判成
            # “网关不可用” → 反复重连。这里返回纯文本 pong。
            body = b"pong\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            if cors_origin_allowed(self.path):
                self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            return self.wfile.write(body)
        if path == "/update/check":
            # 项目新版本检测（看板「运行信息 · 新版本检测」；结果缓存 6h，force=1 强制刷新）
            _u = parse_qs(urlparse(self.path).query)
            try:
                return self._json(200, check_for_update(
                    force=bool(_u.get("force", [None])[0])))
            except Exception as exc:
                return self._error(500, "update check failed: %s" % exc)
        if path == "/diag/vm":
            # 本机虚拟化检测（看板「签到与福利中心」展示；中文输出）。
            # 以官方风控桥 runtime-info.exe 的 vmInfo 为准，桥不可用时本机交叉校验。
            _q = parse_qs(urlparse(self.path).query)
            try:
                return self._json(200, local_vm_status(
                    _q.get("realm", [None])[0] or CURRENT_REALM,
                    force=bool(_q.get("force", [None])[0])))
            except Exception as exc:
                return self._error(500, "vm status failed: %s" % exc)
        if path == "/health":
            rep = current_account()
            info = {
                "ok": True,
                "service": "qoder-proxy",
                "version": VERSION,
                "realm": CURRENT_REALM,
                "accounts": len(POOL.accounts) if POOL else 0,
                "accounts_ready": POOL.count_ready() if POOL else 0,
                "api_key_required": bool(API_KEY),
            }
            if self._key_ok():
                info.update({
                    "uid": rep.uid if rep else None,
                    "domain": rep.domain if rep else None,
                    "issuer": "qoder" if rep else None,
                    "credential_file": os.path.basename(rep.path)
                    if rep and rep.path else None,
                    "expires_at": rep.expires_at if rep else None,
                })
            return self._json(200, info)
        if path == "/realm":
            return self._json(200, {"current": CURRENT_REALM,
                                    "options": ["intl", "cn"]})
        if path in ("/v1/models", "/models"):
            if not self._authorized():
                return
            req_realm = self._request_realm() or CURRENT_REALM
            try:
                entries = fetch_models(realm=req_realm)
            except Exception as exc:
                return self._error(502, str(exc))
            data = [model_entry(mid, meta) for mid, meta in entries]
            try:
                catalog_source = qoder_catalog.snapshot_source(req_realm)
            except Exception as exc:   # 只读探针：失败也不能拖垮模型列表
                catalog_source = "unknown"
                log("catalog source probe failed: %s" % exc, level="WARN")
            return self._json(200, {"object": "list", "data": data,
                                    "realm": req_realm or CURRENT_REALM,
                                    "catalog_source": catalog_source})
        if path in ("/usage", "/v1/usage"):
            if not self._authorized():
                return
            req_realm = query.get("realm", [None])[0] \
                or self.headers.get("X-Realm") or CURRENT_REALM
            return self._json(200, usage_snapshot(realm=req_realm))
        if path == "/usage/recent":
            if not self._authorized():
                return
            try:
                limit = max(1, min(1000, int((query.get("limit") or ["100"])[0])))
            except ValueError:
                limit = 100
            try:
                page = max(1, int((query.get("page") or ["1"])[0]))
            except ValueError:
                page = 1
            req_realm = query.get("realm", [None])[0] \
                or self.headers.get("X-Realm") or CURRENT_REALM
            return self._json(200, recent_usage(limit, realm=req_realm, page=page))
        if path == "/accounts/credits":
            if not self._authorized():
                return
            for a in (POOL.accounts if POOL else []):
                a.fetch_credits()
            return self._json(200, {"accounts": account_views()})
        if path == "/accounts":
            if not self._authorized():
                return
            return self._json(200, {
                "accounts": account_views(realm=query.get("realm", [None])[0]
                                          or CURRENT_REALM),
                "storage": ACCOUNTS_DIR,
                "usable": POOL.count_ready() if POOL else 0,
            })
        if path == "/accounts/export":
            if not self._authorized():
                return
            realm = (query.get("realm") or [None])[0] or None
            if realm not in ("intl", "cn"):
                realm = None
            include_secrets = (query.get("secrets") or ["1"])[0] \
                not in ("0", "false", "no")
            uids = []
            for raw in query.get("uid") or []:
                uids.extend(part.strip() for part in str(raw).split(",")
                            if part.strip())
            if uids:
                known = {a.uid for a in (POOL.accounts if POOL else [])}
                missing = [u for u in uids if u not in known]
                if missing:
                    return self._error(404, "no such account: %s"
                                       % ", ".join(missing[:5]),
                                       "invalid_request_error")
            doc = qoder_accounts.build_export_document(
                POOL.accounts if POOL else [],
                realm=realm, include_secrets=include_secrets,
                uids=uids or None)
            if (query.get("download") or ["0"])[0] in ("1", "true", "yes"):
                stamp = time.strftime("%Y%m%d-%H%M%S")
                if len(uids) == 1:
                    label = uids[0][:8]
                else:
                    label = realm + "-" if realm else ""
                name = "qoder-accounts-%s%s.json" % (label, stamp)
                return self._download(name, doc)
            return self._json(200, doc)
        if path == "/accounts/login/poll":
            if not self._authorized():
                return
            state = (query.get("state") or [""])[0]
            return self._json(200, POOL.poll_login(state))
        if path == "/usage/analytics":
            if not self._authorized():
                return
            return self._json(200, compute_usage_analytics())
        if path == "/usage/by-account":
            if not self._authorized():
                return
            return self._json(200, {"accounts": usage_by_account()})
        if path == "/usage/perf":
            if not self._authorized():
                return
            try:
                sample = max(10, min(20000,
                                     int((query.get("sample") or ["5000"])[0])))
            except ValueError:
                sample = 5000
            req_realm = query.get("realm", [None])[0] \
                or self.headers.get("X-Realm") or CURRENT_REALM
            return self._json(200, perf_stats(sample, realm=req_realm))
        if path == "/tasks":
            if not self._authorized():
                return
            import qoder_tasks
            uid = (query.get("uid") or [None])[0]
            # 账号池与积分按视图分区：?realm=cn|intl 时只列该区域账号
            realm_q = (query.get("realm") or [None])[0]
            if realm_q not in ("cn", "intl"):
                realm_q = None
            view = qoder_tasks.fetch_tasks_view(POOL, realm=realm_q, uid=uid)
            if view.get("msg") and not view.get("tasks"):
                return self._json(200, view)
            return self._json(200, view)
        if path == "/scheduler":
            if not self._authorized():
                return
            return self._json(200, SCHEDULER.status() if SCHEDULER
                              else {"enabled": False, "msg": "未运行"})
        if path == "/settings":
            if not self._authorized():
                return
            return self._json(200, runtime_settings_view())
        if path == "/logs":
            if not self._authorized():
                return
            try:
                limit = int(query.get("limit", ["200"])[0])
            except (ValueError, TypeError):
                limit = 200
            level = query.get("level", [""])[0]
            tag = query.get("tag", [""])[0]
            search = query.get("search", [""])[0]
            try:
                since_id = int(query.get("since_id", ["0"])[0])
            except (ValueError, TypeError):
                since_id = 0
            return self._json(200, get_logs(limit=limit, level=level, tag=tag,
                                            search=search, since_id=since_id))
        if path == "/logs/export":
            if not self._authorized():
                return
            log_data = get_logs(limit=5000)
            lines = ["[%s] [%s] [%s] %s" % (item["ts"], item["level"],
                                             item["tag"], item["msg"])
                     for item in log_data["logs"]]
            text_content = "\n".join(lines).encode("utf-8")
            filename = "qd-proxy-%s.log" % time.strftime("%Y%m%d-%H%M%S")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Disposition",
                             'attachment; filename="%s"' % filename)
            self.send_header("Content-Length", str(len(text_content)))
            if cors_origin_allowed(self.path):
                self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(text_content)
            return
        if path == "/settings/reveal":
            # 看板只画掩码 Key，复制明文需要显式请求；必须面板会话。
            if not self._panel_ok():
                return self._error(401, "panel password required",
                                   "invalid_request_error")
            if qoder_settings.panel_password_is_default(ACCOUNTS_DIR):
                # 默认密码 = 局域网内任何人都能登录；明文 Key 不能这样交出去。
                return self._error(
                    403,
                    "面板仍在使用默认密码，局域网内任何人都能登录后读到明文 API "
                    "Key。请先在「设置 → 面板密码」修改密码，再查看明文 Key。",
                    "permission_error")
            wanted = (query.get("id") or [""])[0]
            for entry in configured_keys():
                if entry.get("id") == wanted:
                    return self._json(200, {"id": wanted,
                                            "key": entry.get("key") or ""})
            return self._error(404, "no such key", "invalid_request_error")
        return self._error(404, "not found", "invalid_request_error")

    def _dashboard(self):
        try:
            with open(DASHBOARD_HTML, "rb") as fh:
                body = fh.read()
        except Exception as exc:
            return self._error(500, "dashboard.html unavailable: %s" % exc)
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_payload(self, max_bytes=MAX_PAYLOAD_BYTES, allow_list=False):
        """Parse the request body into a dict (or a list when allow_list)."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except Exception:
            length = 0
        if length > max_bytes:
            raise BodyTooLarge(length)
        if length < 0:
            raise BadJSON()
        try:
            raw = self.rfile.read(length).decode("utf-8") if length else "{}"
            data = json.loads(raw or "{}")
        except Exception:
            raise BadJSON()
        if isinstance(data, dict):
            return data
        if allow_list and isinstance(data, list):
            return data
        return {}

    def _payload_or_error(self, allow_list=False):
        """Read the body, replying with the right error and returning None."""
        try:
            return self._read_payload(allow_list=allow_list)
        except BodyTooLarge as exc:
            self._error(413, "payload too large (%d bytes > %d limit)"
                        % (exc.length, MAX_PAYLOAD_BYTES),
                        "invalid_request_error")
            return None
        except BadJSON:
            self._error(400, "invalid JSON body", "invalid_request_error")
            return None

    def _handle_settings_save(self):
        """Persist panel-managed settings from the web settings tab."""
        payload = self._payload_or_error()
        if payload is None:
            return
        reply = {}
        if "api_keys" in payload:
            raw = payload.get("api_keys")
            if not isinstance(raw, list):
                return self._error(400, "api_keys must be a list",
                                   "invalid_request_error")
            # 面板只画掩码 Key：空值表示“保留原值”，不是“清空”。
            existing = {entry.get("id"): entry for entry in configured_keys()}
            cleaned = []
            for index, item in enumerate(raw):
                if not isinstance(item, dict):
                    return self._error(400, "each api key must be an object",
                                       "invalid_request_error")
                entry_id = str(item.get("id") or "").strip()
                value = str(item.get("key") or "").strip()
                if not value and entry_id and entry_id in existing:
                    value = existing[entry_id].get("key") or ""
                if not entry_id:
                    entry_id = "k%d" % index
                if value and len(value) < 4:
                    return self._error(400,
                                       "api key must be at least 4 characters",
                                       "invalid_request_error")
                if not value:
                    return self._error(400,
                                       "a key entry is empty - fill it in or "
                                       "remove the row",
                                       "invalid_request_error")
                realm = str(item.get("realm") or "").strip().lower()
                if realm not in ("", "intl", "cn"):
                    return self._error(400, "realm must be intl, cn or empty",
                                       "invalid_request_error")
                created_at = item.get("created_at") \
                    or (existing.get(entry_id, {}).get("created_at")
                        if entry_id in existing else None) \
                    or time.strftime("%Y/%m/%d %H:%M")
                cleaned.append({
                    "id": entry_id,
                    "name": str(item.get("name") or "").strip(),
                    "key": value,
                    "realm": realm,
                    "enabled": item.get("enabled", True) is not False,
                    "created_at": created_at,
                })
            qoder_settings.set_api_keys(ACCOUNTS_DIR, cleaned)
            reply["api_keys_saved"] = len(cleaned)
        if "auth_disabled" in payload:
            qoder_settings.set_auth_disabled(ACCOUNTS_DIR,
                                             payload.get("auth_disabled"))
            reply["auth_disabled"] = bool(payload.get("auth_disabled"))
        new_key = payload.get("api_key")
        if new_key is not None:
            new_key = str(new_key).strip()
            if new_key and len(new_key) < 4:
                return self._error(400, "api key must be at least 4 characters",
                                   "invalid_request_error")
            global API_KEY, API_KEY_FILE_SET
            qoder_settings.set_api_key(ACCOUNTS_DIR, new_key)
            API_KEY = new_key
            API_KEY_FILE_SET = True
            reply["api_key_set"] = bool(new_key)
        if payload.get("restart_scheduler"):
            if SCHEDULER:
                SCHEDULER.stop()
                SCHEDULER.start()
            reply["scheduler"] = "restarted"
        reply.update(runtime_settings_view())
        return self._json(200, reply)

    def _handle_panel(self, path):
        """Panel login, logout and the settings screen (password)."""
        payload = self._payload_or_error()
        if payload is None:
            return
        if path == "/panel/login":
            client_ip = self.client_address[0] \
                if hasattr(self, "client_address") and self.client_address \
                else "127.0.0.1"
            now = time.time()
            with _login_lock:
                _prune_login_attempts(now)
                attempts = [t for t in _login_attempts.get(client_ip, [])
                            if now - t < 60]
                _login_attempts[client_ip] = attempts
                if len(attempts) >= 5:
                    wait_sec = int(60 - (now - attempts[0]))
                    return self._error(429,
                                       "too many login attempts, please wait %ds"
                                       % max(1, wait_sec),
                                       "rate_limit_error")
            password = str(payload.get("password") or "")
            if not qoder_settings.verify_panel_password(ACCOUNTS_DIR, password):
                with _login_lock:
                    _login_attempts.setdefault(client_ip, []).append(now)
                time.sleep(0.5)   # 撞库缓解
                return self._error(401, "invalid panel password",
                                   "invalid_request_error")
            with _login_lock:
                _login_attempts.pop(client_ip, None)
            token = PANEL.create()
            return self._json(200, {
                "ok": True,
                "token": token,
                "using_default_password":
                    qoder_settings.panel_password_is_default(ACCOUNTS_DIR),
            })
        if path == "/panel/logout":
            PANEL.revoke(self._panel_token())
            return self._json(200, {"ok": True})
        # 之后所有路由都要面板会话
        if not self._panel_ok():
            return self._error(401, "panel password required",
                               "invalid_request_error")
        if path == "/panel/password":
            current = str(payload.get("current") or "")
            new = str(payload.get("new") or "")
            if not qoder_settings.verify_panel_password(ACCOUNTS_DIR, current):
                return self._error(401, "current password is wrong",
                                   "invalid_request_error")
            if len(new) < 4:
                return self._error(400, "new password must be at least 4 characters",
                                   "invalid_request_error")
            qoder_settings.set_panel_password(ACCOUNTS_DIR, new)
            if new != qoder_settings.DEFAULT_PANEL_PASSWORD:
                # 换密码使其它浏览器会话全部失效
                PANEL.revoke_all()
            token = PANEL.create()
            return self._json(200, {"ok": True, "token": token})
        return self._error(404, "not found", "invalid_request_error")

    def _handle_accounts(self, path, payload):
        """Account-management endpoints (dashboard uses these)."""
        if POOL is None:
            return self._error(503, "account pool unavailable")
        if path == "/accounts/import" and isinstance(payload, list):
            payload = {"data": payload}
        if not isinstance(payload, dict):
            return self._error(400, "expected a JSON object",
                               "invalid_request_error")
        if path in ("/accounts/credits", "/accounts/credits/fetch"):
            uid = payload.get("uid")
            realm = payload.get("realm")
            if uid:
                targets = [POOL.get(uid)]
            elif realm and realm != "all":
                targets = [a for a in POOL.accounts if a.realm == realm]
            else:
                targets = list(POOL.accounts)
            results = []
            for account in targets:
                if account is None:
                    continue
                res = account.fetch_credits()
                account.fetch_plan()
                results.append({"uid": account.uid, "ok": res.get("ok", False),
                                "credits": account.credits,
                                "plan": account.plan,
                                "error": res.get("error", "")})
            return self._json(200, {"results": results,
                                    "accounts": account_views()})
        if path == "/tasks/run":
            import qoder_tasks
            if not POOL:
                return self._json(200, {"ok": False, "msg": "账号池不可用"})
            uid = payload.get("uid")
            if uid and uid != "all":
                target = POOL.get(uid)
                if not target:
                    return self._json(200, {"ok": False,
                                            "msg": "未找到指定的账号"})
                targets = [target]
            else:
                # 不再按区域过滤：签到能力运行时探测，接口不存在的账号会在
                # 日志里给出明确原因（历史问题：国际版点签到完全没反应）。
                targets = [a for a in POOL.accounts
                           if a.enabled and a.access_token]
            if not targets:
                return self._json(200, {"ok": False, "msg": "未找到可用账号（账号已禁用或缺少凭证）"})
            res = qoder_tasks.run_batch_checkin(targets, gap=1.0)
            qoder_tasks.invalidate_panel_cache()   # 写操作后失效面板短缓存
            return self._json(200, {
                "ok": res["ok"],
                "credit_added": res["credit_added"],
                "logs": res["logs"],
                "accounts_count": res["accounts_count"],
            })
        if path == "/tasks/travel":
            # 看板福利按钮的对应动作：批量领取 Pro 福利包（官方仅国内版 sash 活动）
            import qoder_tasks
            if not POOL:
                return self._json(200, {"ok": False, "msg": "账号池不可用"})
            uid = payload.get("uid")
            if uid and uid != "all":
                target = POOL.get(uid)
                if not target:
                    return self._json(200, {"ok": False,
                                            "msg": "未找到指定的账号"})
                targets = [target]
            else:
                targets = [a for a in POOL.accounts
                           if a.enabled and a.access_token]
            if not targets:
                return self._json(200, {"ok": False, "msg": "未找到可用账号（账号已禁用或缺少凭证）"})
            res = qoder_tasks.run_batch_pro_claim(targets)
            qoder_tasks.invalidate_panel_cache()   # 写操作后失效面板短缓存
            return self._json(200, {
                "ok": True,
                "results": res["results"],
                "msg": res["msg"],
                "logs": res["logs"],
                "accounts_count": res["accounts_count"],
            })
        if path == "/scheduler/trigger":
            if SCHEDULER:
                return self._json(200, SCHEDULER.trigger_now())
            return self._json(200, {"ok": False, "msg": "调度器未初始化"})
        if path == "/scheduler/toggle":
            if SCHEDULER:
                SCHEDULER.enabled = not SCHEDULER.enabled
                SCHEDULER.log("用户切换调度器状态为: %s"
                              % ("启用" if SCHEDULER.enabled else "暂停"))
                return self._json(200, SCHEDULER.status())
            return self._json(200, {"ok": False, "msg": "调度器未初始化"})
        if path == "/logs/clear":
            clear_logs()
            return self._json(200, {"ok": True})
        if path == "/realm":
            new_realm = payload.get("realm")
            if new_realm in ("intl", "cn"):
                save_persisted_realm(new_realm)
            return self._json(200, {"ok": True, "current": CURRENT_REALM,
                                    "persisted": True})
        if path == "/accounts/checkin":
            # 账号面板的「每日签到」按钮：**只做每日签到领积分**——Credits 类活动
            # （如"每天领 100 Credits"，旧 sash 接口兜底），不领券/兑换码类活动，
            # 也不领 Pro 福利包（那些走签到与福利中心的按钮）。
            import qoder_tasks
            uid = payload.get("uid")
            targets = [POOL.get(uid)] if uid else list(POOL.accounts)
            results = []
            last_at = None
            for account in targets:
                if account is None:
                    continue
                if not account.enabled or not account.access_token:
                    continue            # 跳过的账号不占用防风控等待
                if last_at is not None:
                    # 账号之间保持 >= CHECKIN_MIN_GAP（与 qoder_tasks.py 声明的
                    # >=1.0s 一致）；try/finally 让失败路径同样计时间隔。
                    idle = CHECKIN_MIN_GAP - (time.time() - last_at)
                    if idle > 0:
                        time.sleep(idle)
                try:
                    res = qoder_tasks.run_checkin(account, gap=CHECKIN_MIN_GAP,
                                                  only_daily=True)
                finally:
                    last_at = time.time()
                results.append({
                    "uid": account.uid,
                    "nickname": account.nickname,
                    "ok": bool(res.get("ok")),
                    "earned_credit": res.get("earned_credit") or 0,
                    "msg": (res.get("logs") or [""])[-1],
                    "logs": res.get("logs") or [],
                    "credits": res.get("credits"),
                })
            qoder_tasks.invalidate_panel_cache()   # 写操作后失效面板短缓存
            return self._json(200, {"results": results,
                                    "accounts": account_views()})
        if path == "/accounts/login/start":
            platform = payload.get("platform") or "CLI"
            target_realm = payload.get("realm") or CURRENT_REALM
            if target_realm not in ("intl", "cn"):
                target_realm = "cn"
            try:
                started = POOL.start_login(realm=target_realm,
                                           platform=platform)
            except Exception as exc:
                return self._error(502, "could not start login: %s" % exc)
            log("oauth device login started (realm=%s, state=%s)"
                % (target_realm, started["state"][:8]))
            return self._json(200, started)
        if path == "/accounts/login/cancel":
            state = payload.get("state") or ""
            return self._json(200, {"cancelled": POOL.cancel_login(state)})
        if path == "/accounts/import/pat":
            pat = payload.get("pat") or ""
            realm = payload.get("realm") or CURRENT_REALM
            if realm not in ("intl", "cn"):
                realm = "cn"
            try:
                account = POOL.import_pat(pat, realm=realm)
            except Exception as exc:
                return self._error(400, "PAT import failed: %s" % exc)
            log("imported PAT account %s (realm=%s)"
                % (account.uid[:8], realm))
            return self._json(200, {"imported": [account.public()],
                                    "accounts": account_views()})
        if path == "/accounts/import/desktop":
            # 两步确认：{} 只读扫描（双区：桌面 App auth.v1.dat + CLI user）；
            # {"path":...} 按确认导入该凭证；{"all":true} 导入全部有效项。
            target_path = payload.get("path")
            if target_path:
                realm = payload.get("realm")
                try:
                    account = qoder_accounts.import_desktop_credential(
                        path=target_path, realm=realm)
                except Exception as exc:
                    return self._error(400, "import failed: %s" % exc)
                log("imported %s (%s) from local client credential"
                    % (account.uid[:8], account.realm), tag="accounts")
                return self._json(200, {
                    "imported": [account.public()],
                    "accounts": account_views(),
                    "pool_uids": [a.uid for a in POOL.accounts],
                })
            if payload.get("all"):
                try:
                    imported = qoder_accounts.import_desktop_credential()
                except Exception as exc:
                    return self._error(400, "import failed: %s" % exc)
                for account in imported:
                    log("imported %s (%s) from local client credential"
                        % (account.uid[:8], account.realm), tag="accounts")
                return self._json(200, {
                    "imported": [a.public() for a in imported],
                    "accounts": account_views(),
                    "pool_uids": [a.uid for a in POOL.accounts],
                })
            try:
                detected = qoder_accounts.scan_desktop_credentials()
            except Exception as exc:
                detected = []
                log("desktop credential scan failed: %s" % exc)
            return self._json(200, {
                "detected": detected,
                "accounts": account_views(),
                "pool_uids": [a.uid for a in POOL.accounts],
            })
        if path == "/accounts/refresh":
            uid = payload.get("uid")
            targets = [POOL.get(uid)] if uid else list(POOL.accounts)
            results = []
            for account in targets:
                if account is None:
                    continue
                ok = account.refresh()
                account.save(ACCOUNTS_DIR)
                results.append({"uid": account.uid, "ok": ok,
                                "error": account.last_error})
            return self._json(200, {"results": results})
        if path == "/accounts/test":
            uid = payload.get("uid")
            if not uid:
                return self._error(400, "uid required")
            account = POOL.get(uid)
            if not account:
                return self._error(404, "no such account")
            test_model = payload.get("model") or "auto"
            test_payload = {
                "model": test_model,
                "messages": [{"role": "user", "content": "hi"}],
                "stream": False,
            }
            t0 = time.time()
            try:
                resp, acc, _ = open_upstream(test_payload, target_realm=acc_realm(account))
                with resp:
                    chat_obj = aggregate_stream(resp, test_model, None)
                wall_ms = int((time.time() - t0) * 1000)
                choices = chat_obj.get("choices") or []
                msg = (choices[0].get("message") or {}) if choices else {}
                reply_text = (msg.get("content") or msg.get("reasoning_content")
                              or "OK").strip()
                if len(reply_text) > 80:
                    reply_text = reply_text[:77] + "..."
                account.clear_error()
                log("account test: uid=%s model=%s wall=%dms ok=True"
                    % (account.uid[:8], test_model, wall_ms), tag="accounts")
                return self._json(200, {"ok": True, "uid": account.uid,
                                        "model": test_model,
                                        "elapsed_ms": wall_ms,
                                        "reply": reply_text})
            except RateLimited as exc:
                wall_ms = int((time.time() - t0) * 1000)
                return self._json(200, {"ok": False, "uid": account.uid,
                                        "status": 429,
                                        "error": "rate limited: %s" % exc.detail[:150],
                                        "elapsed_ms": wall_ms})
            except urllib.error.HTTPError as exc:
                wall_ms = int((time.time() - t0) * 1000)
                try:
                    detail = exc.read(400).decode("utf-8", "replace")
                except Exception:
                    detail = ""
                account.note_error("HTTP %d: %s" % (exc.code, detail[:80]),
                                   cooldown=60)
                log("account test: uid=%s model=%s wall=%dms error=%d"
                    % (account.uid[:8], test_model, wall_ms, exc.code),
                    level="WARN", tag="accounts")
                return self._json(200, {"ok": False, "uid": account.uid,
                                        "status": exc.code,
                                        "error": "HTTP %d: %s"
                                        % (exc.code, detail[:150]),
                                        "elapsed_ms": wall_ms})
            except Exception as exc:
                wall_ms = int((time.time() - t0) * 1000)
                account.note_error(str(exc)[:80], cooldown=60)
                log("account test: uid=%s model=%s wall=%dms exc=%s"
                    % (account.uid[:8], test_model, wall_ms, exc),
                    level="WARN", tag="accounts")
                return self._json(200, {"ok": False, "uid": account.uid,
                                        "status": 500, "error": str(exc),
                                        "elapsed_ms": wall_ms})
        if path == "/accounts/set":
            uid = payload.get("uid")
            if not uid:
                return self._error(400, "uid required")
            updated = POOL.set_enabled(uid, bool(payload.get("enabled")))
            if updated is None:
                return self._error(404, "no such account")
            log("account %s %s" % (uid[:8],
                                   "enabled" if payload.get("enabled")
                                   else "disabled"))
            return self._json(200, {"account": updated})
        if path == "/accounts/set-all":
            POOL.set_all_enabled(bool(payload.get("enabled")))
            return self._json(200, {"accounts": account_views()})
        if path == "/accounts/delete":
            uid = payload.get("uid")
            if not uid:
                return self._error(400, "uid required")
            removed = POOL.remove(uid)
            log("account %s deleted" % uid[:8])
            return self._json(200, {"deleted": removed,
                                    "accounts": account_views()})
        if path == "/accounts/import":
            # 支持的文档形态见 qoder_accounts._coerce_account_rows。
            # 选项：dryRun / overwrite / realm ("intl"|"cn")
            blob = payload.get("data") if "data" in payload else payload
            if not isinstance(blob, (dict, list)):
                return self._error(400, "the document must be a JSON object "
                                   "or array", "invalid_request_error")
            rows, problem = qoder_accounts._coerce_account_rows(blob)
            if problem:
                return self._error(400, "cannot read the document: %s" % problem,
                                   "invalid_request_error")
            dry_run = bool(payload.get("dryRun"))
            overwrite = bool(payload.get("overwrite"))
            forced_realm = (payload.get("realm") or "").strip().lower() or None
            if forced_realm and forced_realm not in ("intl", "cn"):
                return self._error(400, "realm must be intl or cn",
                                   "invalid_request_error")
            if dry_run:
                return self._json(200, {
                    "dryRun": True,
                    "count": len(rows),
                    "result": POOL.preview_import_rows(rows, realm=forced_realm,
                                                       overwrite=overwrite),
                    "accounts": account_views(),
                })
            report = POOL.import_rows(rows, realm=forced_realm,
                                      overwrite=overwrite)
            log("account import: %d added, %d updated, %d skipped, %d invalid"
                % (len(report["added"]), len(report["updated"]),
                   len(report["skipped"]), len(report["invalid"])))
            return self._json(200, {
                "count": len(rows),
                "result": report,
                "accounts": account_views(),
            })
        return self._error(404, "unknown account endpoint",
                           "invalid_request_error")

    def _handle_responses(self, payload):
        """Serve /v1/responses by translating to chat completions upstream."""
        session_key = extract_session_key(self.headers, payload)
        custom_names = custom_tool_names(payload.get("tools"))
        chat_req = responses_to_chat(payload)
        model = payload.get("model") or "auto"
        want_stream = bool(payload.get("stream"))
        t_start = time.time()
        fp = prompt_fingerprint(chat_req.get("messages"))
        log("responses: model=%s stream=%s msgs=%d effort=%r custom_tools=%s"
            % (model, want_stream, len(chat_req.get("messages") or []),
               chat_req.get("reasoning_effort"),
               sorted(custom_names) or "-"))
        holder = {"usage": None, "custom_names": custom_names,
                  "allowed_names": _tool_names_from_payload(chat_req)}
        try:
            req_realm = self._request_realm() or CURRENT_REALM
            blocked = self._cross_realm_error(chat_req.get("model"), req_realm)
            if blocked:
                return self._error(400, blocked, "invalid_request_error")
            upstream, account, _ = open_upstream(
                chat_req, session_key=session_key, target_realm=req_realm)
        except RateLimited as exc:
            t = time.time() - t_start
            record_error(model, 429, exc.detail[:200],
                         elapsed_ms=int(t * 1000))
            return self._rate_limited(exc)
        except urllib.error.HTTPError as exc:
            # 错误体在 open_upstream 中已读过（挂在 qoder_detail），二次 read
            # 会拿到残缺内容——优先取挂载值。
            detail = getattr(exc, "qoder_detail", "")
            if not detail:
                try:
                    detail = exc.read(600).decode("utf-8", "replace")
                except Exception:
                    detail = ""
            record_error(model, exc.code, detail,
                         elapsed_ms=int((time.time() - t_start) * 1000))
            msg, etype = friendly_upstream_error(exc.code, detail)
            return self._error(exc.code, msg, etype)
        except Exception as exc:
            message = str(exc)
            record_error(model, 502, message,
                         elapsed_ms=int((time.time() - t_start) * 1000))
            if message.startswith("no usable account"):
                return self._error(503, message
                                   + " - add or enable one at the dashboard (/)")
            return self._error(502, "upstream unreachable: %s" % exc)
        with upstream:
            if want_stream:
                self._sse_begin()
                first_ms = None
                # 流内信封重试：上游可能 HTTP200 建流后在信封里投 418
                # （access log 记 200 + 业务错 418 即此形态）。只要还没向
                # 客户端写出任何上游字节，就重开上游再试。
                attempts = 0
                cur = upstream
                pump_exc = None
                try:
                    while True:
                        try:
                            # 统计已消费的上游数据行：错误信封本身不产出
                            # 行，故 n==0 表示还没消费到任何上游数据 → 可重开
                            consumed = {"n": 0}

                            def _count_src(src, _c=consumed):
                                for item in src:
                                    _c["n"] += 1
                                    yield item

                            inner = _count_src(
                                sse_with_heartbeat(
                                    iter_inner_sse(cur, holder=holder),
                                    self._sse_write))
                            for frame in stream_responses_events(inner, model, holder):
                                if first_ms is None:
                                    first_ms = int((time.time() - t_start) * 1000)
                                self._sse_write(clean_responses_frame(frame))
                            break
                        except (BrokenPipeError, ConnectionResetError,
                                ConnectionAbortedError):
                            wall = int((time.time() - t_start) * 1000)
                            record_usage(model, holder.get("usage"), stream=True,
                                         elapsed_ms=wall, ttft_ms=first_ms,
                                         gen_ms=(wall - first_ms)
                                         if first_ms is not None else None,
                                         fp=fp, account=account.uid)
                            return
                        except UpstreamStatus as exc:
                            _handle_envelope_account_cooldown(account, exc, model=model, session_key=session_key)
                            # 控制帧（response.created 等）先于数据，不能算
                            # “已输出”；以上游数据行计数判断能否重开。
                            if should_retry_envelope(exc, False, attempts) \
                                    and consumed.get("n", 0) == 0:
                                attempts += 1
                                log("responses in-stream envelope status %s "
                                    "(try %d/%d), reopening upstream"
                                    % (exc.status, attempts + 1,
                                       TRANSIENT_MAX_RETRIES + 1),
                                    level="WARN", tag="chat")
                                time.sleep(attempts)
                                try:
                                    # 必须用转换后的 chat 请求体重开：原始
                                    # Responses 体的会话在 input 字段里，而
                                    # build_qoder_body 只读 messages -> 空会话。
                                    cur2, account, _ = open_upstream(
                                        chat_req, session_key=session_key,
                                        target_realm=req_realm)
                                except Exception as rex:
                                    log("responses reopen failed: %s"
                                        % str(rex)[:160], level="WARN")
                                    pump_exc = exc
                                    break
                                if cur is not upstream:
                                    try:
                                        cur.close()
                                    except Exception:
                                        pass
                                cur = cur2
                                continue
                            pump_exc = exc
                            break
                finally:
                    if cur is not upstream:
                        try:
                            cur.close()
                        except Exception:
                            pass
                if pump_exc is not None:
                    record_error(model, pump_exc.status, pump_exc.detail,
                                 elapsed_ms=int((time.time() - t_start) * 1000))
                    msg, _ = friendly_upstream_error(
                        _to_int_status(pump_exc.status), pump_exc.detail)
                    log("responses upstream status %s: %s"
                        % (pump_exc.status, msg[:200]), level="ERROR",
                        tag="chat")
                    # Responses 协议靠终态事件收尾：只关流会让客户端一直等。
                    try:
                        self._sse_write(_responses_failed_frame(
                            holder, _to_int_status(pump_exc.status), msg))
                    except Exception:
                        pass
                    self._sse_end()
                    return
                self._sse_end()
                wall = int((time.time() - t_start) * 1000)
                record_usage(model, holder.get("usage"), stream=True,
                             elapsed_ms=wall, ttft_ms=first_ms,
                             gen_ms=(wall - first_ms)
                             if first_ms is not None else None,
                             fp=fp, account=account.uid)
                return
            try:
                chat_obj, account = aggregate_with_envelope_retry(
                    upstream, chat_req, session_key, req_realm, model,
                    holder, account)
            except UpstreamStatus as exc:
                record_error(model, exc.status, exc.detail,
                             elapsed_ms=int((time.time() - t_start) * 1000))
                msg, etype = friendly_upstream_error(_to_int_status(exc.status),
                                                     exc.detail)
                return self._error(exc.status if str(exc.status).isdigit() else 502,
                                   msg, etype)
            except Exception as exc:
                record_error(model, 502, str(exc),
                             elapsed_ms=int((time.time() - t_start) * 1000))
                return self._error(502, "upstream stream error: %s" % exc)
            wall = int((time.time() - t_start) * 1000)
            result = chat_to_response(chat_obj, model, custom_names)
            record_usage(model, chat_obj.get("usage"), stream=False,
                         elapsed_ms=wall, fp=fp, account=account.uid)
            return self._json(200, result)

    def do_POST(self):
        path = self.path.split("?")[0]
        if path == "/settings/save":
            if not self._panel_ok():
                return self._error(401, "panel password required",
                                   "invalid_request_error")
            return self._handle_settings_save()
        if path in ("/panel/login", "/panel/logout", "/panel/password"):
            return self._handle_panel(path)
        if self._is_panel_route(path) and not self._panel_ok():
            return self._error(401, "panel password required",
                               "invalid_request_error")
        is_account_route = (
            path.startswith("/accounts/")
            or path == "/realm"
            or path.startswith("/tasks")
            or path.startswith("/scheduler")
            or path.startswith("/logs")
        )
        if not is_account_route and path not in (
                "/v1/chat/completions", "/chat/completions",
                "/v1/responses", "/responses"):
            if path in ("/v1/completions", "/completions"):
                # legacy Completions（prompt 而非 messages）本网关不实现：
                # 明确 404 优于"接受请求却按空会话转发上游"。
                return self._error(
                    404,
                    "the legacy /v1/completions API is not supported here: "
                    "this gateway speaks Chat Completions and Responses. "
                    "Use /v1/chat/completions with messages=[...] instead.",
                    "invalid_request_error")
            return self._error(404, "not found", "invalid_request_error")
        if not self._authorized():
            return
        payload = self._payload_or_error(allow_list=(path == "/accounts/import"))
        if payload is None:
            return
        if is_account_route:
            return self._handle_accounts(path, payload)
        if path in ("/v1/responses", "/responses"):
            return self._handle_responses(payload)

        # ---- Chat Completions 主链路 ----
        normalize_tool_choice(payload)
        normalize_tools(payload)
        translate_max_completion_tokens(payload)
        model = payload.get("model") or "auto"
        payload.setdefault("stream", False)
        # 保持原始终端 stream 意图
        want_stream = bool(payload.get("stream"))
        # 转换统一在 build_qoder_body 内做（压平/清洗/工具）
        session_key = extract_session_key(self.headers, payload)
        fp = prompt_fingerprint(payload.get("messages"))
        t_start = time.time()
        effort = payload.get("reasoning_effort") or \
            (payload.get("reasoning") or {}).get("effort") \
            if isinstance(payload.get("reasoning"), dict) \
            else payload.get("reasoning_effort")
        log("chat: model=%s client_effort=%r stream=%s msgs=%d"
            % (model, effort, want_stream,
               len(payload.get("messages") or [])))
        try:
            req_realm = self._request_realm() or CURRENT_REALM
            blocked = self._cross_realm_error(model, req_realm)
            if blocked:
                return self._error(400, blocked, "invalid_request_error")
            upstream, account, _ = open_upstream(payload, session_key=session_key,
                                                 target_realm=req_realm)
        except RateLimited as exc:
            record_error(model, 429, exc.detail[:200],
                         elapsed_ms=int((time.time() - t_start) * 1000))
            return self._rate_limited(exc)
        except urllib.error.HTTPError as exc:
            # 错误体在 open_upstream 中已读过（挂在 qoder_detail），二次 read
            # 会拿到残缺内容——优先取挂载值。
            detail = getattr(exc, "qoder_detail", "")
            if not detail:
                try:
                    detail = exc.read(600).decode("utf-8", "replace")
                except Exception:
                    detail = ""
            record_error(model, exc.code, detail,
                         elapsed_ms=int((time.time() - t_start) * 1000))
            msg, etype = friendly_upstream_error(exc.code, detail)
            return self._error(exc.code, msg, etype)
        except Exception as exc:
            message = str(exc)
            record_error(model, 502, message,
                         elapsed_ms=int((time.time() - t_start) * 1000))
            if message.startswith("no usable account"):
                return self._error(503, message
                                   + " - add or enable one at the dashboard (/)")
            return self._error(502, "upstream unreachable: %s" % exc)

        with upstream:
            holder = {"usage": None}
            if want_stream:
                self._sse_begin()
                emitted = False
                first_ms = None
                # 流内信封重试（同上：200 建流后信封投 418 的形态），仅在
                # 尚未向客户端写出任何上游字节时重开。
                attempts = 0
                cur = upstream
                pump_exc = None
                try:
                    while True:
                        try:
                            for line in sse_with_heartbeat(
                                    recover_leaked_tool_calls(
                                        iter_inner_sse(cur, holder=holder),
                                        allowed_names=_tool_names_from_payload(payload)),
                                    self._sse_write):
                                if first_ms is None:
                                    first_ms = int((time.time() - t_start) * 1000)
                                emitted = True
                                self._sse_write(line)
                            break
                        except (BrokenPipeError, ConnectionResetError,
                                ConnectionAbortedError):
                            # 客户端断开；上游已产出的部分照常记账。
                            wall = int((time.time() - t_start) * 1000)
                            record_usage(model, holder.get("usage"), stream=True,
                                         elapsed_ms=wall, ttft_ms=first_ms,
                                         gen_ms=(wall - first_ms)
                                         if first_ms is not None else None,
                                         fp=fp, account=account.uid)
                            return
                        except UpstreamStatus as exc:
                            _handle_envelope_account_cooldown(account, exc, model=model, session_key=session_key)
                            if should_retry_envelope(exc, emitted, attempts):
                                attempts += 1
                                log("chat in-stream envelope status %s on "
                                    "model '%s' (try %d/%d), reopening upstream"
                                    % (exc.status, model, attempts + 1,
                                       TRANSIENT_MAX_RETRIES + 1),
                                    level="WARN", tag="chat")
                                time.sleep(attempts)
                                try:
                                    cur2, account, _ = open_upstream(
                                        payload, session_key=session_key,
                                        target_realm=req_realm)
                                except Exception as rex:
                                    log("chat reopen failed: %s"
                                        % str(rex)[:160], level="WARN")
                                    pump_exc = exc
                                    break
                                if cur is not upstream:
                                    try:
                                        cur.close()
                                    except Exception:
                                        pass
                                cur = cur2
                                continue
                            pump_exc = exc
                            break
                finally:
                    if cur is not upstream:
                        try:
                            cur.close()
                        except Exception:
                            pass
                if pump_exc is not None:
                    exc = pump_exc
                    wall = int((time.time() - t_start) * 1000)
                    record_error(model, exc.status, exc.detail, elapsed_ms=wall)
                    msg, etype = friendly_upstream_error(
                        _to_int_status(exc.status), exc.detail)
                    err = json.dumps({"error": {
                        "message": msg, "type": etype,
                        "code": exc.status}}, ensure_ascii=False)
                    try:
                        self._sse_write(("data: %s\n\n" % err).encode("utf-8"))
                        self._sse_write(b"data: [DONE]\n\n")
                    except Exception:
                        pass
                    self._sse_end()
                    return
                if not emitted:
                    err = json.dumps({"error": {
                        "message": "empty upstream stream",
                        "type": "server_error"}})
                    self._sse_write(("data: %s\n\n" % err).encode("utf-8"))
                self._sse_write(b"data: [DONE]\n\n")
                self._sse_end()
                wall = int((time.time() - t_start) * 1000)
                record_usage(model, holder.get("usage"), stream=True,
                             elapsed_ms=wall, ttft_ms=first_ms,
                             gen_ms=(wall - first_ms)
                             if first_ms is not None else None,
                             fp=fp, account=account.uid)
                return
            try:
                result, account = aggregate_with_envelope_retry(
                    upstream, payload, session_key, req_realm, model,
                    holder, account)
            except UpstreamStatus as exc:
                record_error(model, exc.status, exc.detail,
                             elapsed_ms=int((time.time() - t_start) * 1000))
                code = exc.status if str(exc.status).isdigit() else 502
                try:
                    code = int(code)
                except Exception:
                    code = 502
                if code < 400 or code > 599:
                    code = 502
                msg, etype = friendly_upstream_error(
                    _to_int_status(exc.status), exc.detail)
                return self._error(code, msg, etype)
            except Exception as exc:
                record_error(model, 502, str(exc),
                             elapsed_ms=int((time.time() - t_start) * 1000))
                return self._error(502, "upstream stream error: %s" % exc)
            wall = int((time.time() - t_start) * 1000)
            first_at = result.get("first_chunk_at")
            first_ms = int((first_at - t_start) * 1000) if first_at else None
            record_usage(model, result.get("usage"), stream=False,
                         elapsed_ms=wall, ttft_ms=first_ms,
                         gen_ms=(wall - first_ms) if first_ms is not None else None,
                         fp=fp, account=account.uid)
            return self._json(200, result)


def acc_realm(account):
    return account.realm if account else CURRENT_REALM


def main():
    global POOL, ACCOUNTS_DIR, API_KEY, SYSTEM_PROMPT, USAGE_DIR, USAGE_LOG, \
        USAGE_SUMMARY, SCHEDULER
    API_KEY_GENERATED = False
    ap = argparse.ArgumentParser(
        description="Qoder (qoder.com.cn / qoder.com) -> OpenAI-compatible proxy")
    ap.add_argument("--host", default=os.environ.get("HOST") or "127.0.0.1")
    # 8788 被 mimo-api-proxy.mjs 占用，8789 是 wb-proxy 默认端口；
    # Qoder 网关默认 8790。Docker 显式传 --port 8790。
    ap.add_argument("--port", type=int,
                    default=int(os.environ.get("PORT") or "8790"))
    ap.add_argument("--lan", action="store_true",
                    help="listen on every interface so other devices on the "
                         "LAN can reach it (implies --host 0.0.0.0 and forces "
                         "an api key)")
    ap.add_argument("--api-key",
                    default=os.environ.get("API_KEY")
                    or os.environ.get("QD_PROXY_KEY") or None,
                    help="require this bearer token on /v1/* (optional)")
    ap.add_argument("--system-prompt", default=DEFAULT_SYSTEM_PROMPT,
                    help="system message used when the request has none "
                         "(default: keep the official Qoder template prompt)")
    ap.add_argument("--usage-dir", default=None,
                    help="where to store usage.jsonl / usage-summary.json "
                         "(default: ./usage)")
    ap.add_argument("--accounts-dir",
                    default=os.environ.get("ACCOUNTS_DIR") or None,
                    help="where the per-account credential files live "
                         "(default: ./accounts)")
    ap.add_argument("--panel-password", default=None,
                    help="set the web panel password on startup (default: "
                         "admin)")
    args = ap.parse_args()

    if args.lan and args.host == "127.0.0.1":
        args.host = "0.0.0.0"
    if args.usage_dir:
        USAGE_DIR = os.path.abspath(args.usage_dir)
        USAGE_LOG = os.path.join(USAGE_DIR, "usage.jsonl")
        USAGE_SUMMARY = os.path.join(USAGE_DIR, "usage-summary.json")

    # 拒绝启动第二份：Windows 上 SO_REUSEADDR 会让两个 socket 绑同一端口，
    # 连接被静默分流，极难诊断。
    try:
        probe = urllib.request.urlopen(
            "http://%s:%d/health"
            % ("127.0.0.1" if args.host == "0.0.0.0" else args.host, args.port),
            timeout=2)
        existing = json.loads(probe.read().decode("utf-8"))
    except Exception:
        existing = None   # 没人应答 /health —— 让下面的 bind 决定
    if isinstance(existing, dict):
        # 只有我们的 /health 带账号池字段（"accounts"）。其它服务可能占用
        # 同端口也应答 /health JSON（例如 wb-proxy / mimo-api-proxy）。
        foreign = existing.get("service") not in (None, "qoder-proxy") \
            or "accounts" not in existing
        if foreign:
            who = existing.get("service") or "an unknown HTTP service"
            print()
            print("  [ERROR] port %d is already taken by another program: %s"
                  % (args.port, who))
            print("          qd-proxy itself is NOT running - nothing was "
                  "started.")
            print()
            print("  Fix: start qoder-proxy on a different port, e.g.")
            print("          start-qoder-proxy.bat %d" % (args.port + 1))
            print("          python qoder_proxy.py --port %d" % (args.port + 1))
            print()
            print("  Check who owns the port:  netstat -ano | findstr :%d"
                  % args.port)
            print()
            raise SystemExit(1)
        print()
        print("  [已有一个反代在 %d 端口运行，无需重复启动]" % args.port)
        print("  账号: %s @ %s" % (existing.get("uid", "?"),
                                   existing.get("domain", "?")))
        print("  看板: http://127.0.0.1:%d/" % args.port)
        print()
        print("  如果要重启: 先把原来那个窗口关掉（或结束 python 进程），"
              "再运行本程序。")
        print()
        return

    API_KEY = args.api_key
    SYSTEM_PROMPT = args.system_prompt
    if args.accounts_dir:
        ACCOUNTS_DIR = os.path.abspath(args.accounts_dir)
    # LAN 模式绝不能带默认密钥：网关花的是账号自己的上游额度，
    # 可猜的默认值等于让全网段的人白嫖。首次生成一次并持久化。
    if args.lan and not API_KEY:
        API_KEY, API_KEY_GENERATED = qoder_settings.ensure_launcher_key(
            ACCOUNTS_DIR)
    # 面板保存的 Key 优先于自动生成的 LAN Key（.bat 重启后浏览器改动仍生效）；
    # 命令行 --api-key 依然最高优先级。
    global API_KEY_FILE_SET
    saved_key, key_from_panel = qoder_settings.api_key_override(ACCOUNTS_DIR)
    if key_from_panel and not args.api_key:
        API_KEY = saved_key
        API_KEY_FILE_SET = True
    if args.panel_password:
        qoder_settings.set_panel_password(ACCOUNTS_DIR, args.panel_password)
        log("panel      : password set from --panel-password")
    elif qoder_settings.panel_password_is_default(ACCOUNTS_DIR):
        log("panel      : password is still the default 'admin' - change it "
            "in the panel")
        if args.host == "0.0.0.0":
            log("SECURITY   : LAN mode + default panel password 'admin' - "
                "anyone on this network can log in and read your API key. "
                "Change it in the panel, or restart with --panel-password.",
                level="WARN")

    POOL = qoder_accounts.AccountPool(ACCOUNTS_DIR, log=log)
    POOL.load()
    load_persisted_realm()
    from qoder_scheduler import Scheduler
    SCHEDULER = Scheduler(POOL)
    SCHEDULER.start()

    if not POOL.accounts:
        # 永不静默采用本机客户端登录：先报告扫描结果，由用户在看板确认导入。
        try:
            detected = qoder_accounts.scan_desktop_credentials()
        except Exception:
            detected = []
        usable = [d for d in detected if d.get("valid")]
        if usable:
            log("no accounts yet - detected %d local credential(s), NOT importing"
                % len(usable))
            for d in usable:
                log("  available: %s  %s  %s" % (
                    (d.get("uid") or "?")[:8], d.get("nickname") or "(no name)",
                    d.get("realmName") or d.get("realm")))
            log("open the dashboard and click [Scan local credentials] to import")
        else:
            log("no accounts yet - no local Qoder credentials found on this machine")
        # 不在这里退出：看板必须可达，才能通过浏览器完成登录。
    rep = current_account()
    log("accounts   : %d total, %d usable"
        % (len(POOL.accounts), POOL.count_ready()))
    for account in POOL.accounts:
        log("  - %s  %s  %s  %s" % (
            account.uid[:8], account.nickname or "(no name)",
            account.realm, account.domain))
    log("store      : %s" % ACCOUNTS_DIR)
    log("credential : %s" % (rep.path if rep else "-"))
    log("account    : %s @ %s" % (rep.uid if rep else "-",
                                  rep.domain if rep else "-"))
    log("realm      : %s (%s)" % (
        CURRENT_REALM,
        "qoder.com" if CURRENT_REALM == "intl" else "qoder.com.cn"))
    log("catalog    : %s" % (BASEPROMPT_PATH if BASEPROMPT
                             else "baseprompt.json MISSING"))

    if args.host == "0.0.0.0":
        ips = local_ip_addresses() or ["<this-pc-ip>"]
        print()
        print("  " + "=" * 62)
        print("  LAN MODE - reachable from other devices")
        print()
        for ip in ips:
            print("    API       : http://%s:%s/v1" % (ip, args.port))
            print("    Dashboard : http://%s:%s/" % (ip, args.port))
        print()
        print("    API Key   : %s" % API_KEY)
        if API_KEY_GENERATED:
            print("                (newly generated & saved to "
                  "accounts/settings.json)")
        else:
            print("                (reused from accounts/settings.json)")
        print()
        print("    Open the dashboard (key already included):")
        print("      http://%s:%s/?key=%s" % (ips[0], args.port, API_KEY))
        print()
        print("    Clients: Base URL = the API address above, then paste "
              "the key.")
        print()
        print("    If nothing can connect, allow python through the")
        print("    firewall: run allow-firewall.bat once as administrator.")
        print("  " + "=" * 62)
        print()
        sys.stdout.flush()

    if not POOL.accounts:
        print()
        print("  " + "=" * 62)
        print("  NO ACCOUNTS YET")
        print()
        print("  Open the dashboard and click [+ 添加账号 (OAuth)]:")
        print("      http://127.0.0.1:%d/" % args.port)
        print()
        print("  The browser flow adds the account automatically.")
        print("  This window must stay open.")
        print("  " + "=" * 62)
        print()
        sys.stdout.flush()

    try:
        server = ThreadingHTTPServer((args.host, args.port), Handler)
    except OSError as exc:
        # bind 前探测与 bind 之间端口被抢，或被不答 /health 的程序占着
        print()
        print("  [ERROR] failed to listen on %s:%d - %s"
              % (args.host, args.port, exc))
        print("          the port is reserved or held by another program;")
        print("          qd-proxy did NOT start.")
        print()
        print("  Fix: stop the program holding the port, or pick another "
              "port:")
        print("          netstat -ano | findstr :%d" % args.port)
        print("          start-qoder-proxy.bat %d" % (args.port + 1))
        print()
        raise SystemExit(1)
    log("listening  : http://%s:%d/v1  (api key: %s)"
        % (args.host, args.port, "on" if API_KEY else "off"))
    log("dashboard  : http://%s:%d/" % (args.host, args.port))
    # 进程生命周期内保持 handler 引用：SetConsoleCtrlHandler 存的是裸指针，
    # 回调被 GC 会在关窗时崩溃。
    _ctrl_handler = install_console_close_handler()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("bye")
    finally:
        try:
            server.server_close()
        except Exception:
            pass


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    main()
