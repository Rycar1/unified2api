# -*- coding: utf-8 -*-
"""wb_identity.py - 出站身分 (WorkBuddy 桌面端 / 官方 VSCode 插件 / 官方 CodeBuddy CLI)

支援三套出站身分:
  1. WorkBuddy 独立桌面客户端 (workbuddy / desktop / wb):
     - X-IDE-Type: WorkBuddy
     - X-IDE-Name: WorkBuddy
     - X-Product: SaaS (国际版聊天) / WorkBuddy (国内版)
     - 国际版端点: www.workbuddy.ai (UA: WorkBuddy/5.8.4 workbuddy-ai/5.8.4 CLI/2.161.0)
     - 国内版端点: copilot.tencent.com (UA: WorkBuddy/5.5.6 WorkBuddy/5.5.6 CLI/2.137.1)

  2. 官方 VSCode 插件 (vscode / vsc):
     - X-IDE-Type: VSCode
     - X-IDE-Name: VSCode
     - X-Product: SaaS
     - UA: VSCode/<ver> WorkBuddy/<ver>
     - 国际版端点: www.workbuddy.ai，国内版端点: www.workbuddy.cn

  3. 官方 CodeBuddy CLI (cli):
     - X-IDE-Type: CLI
     - X-IDE-Name: CLI
     - X-Product: SaaS
     - UA: CLI/<ver> CodeBuddy/<ver>
     - 国际版端点: www.workbuddy.ai，国内版端点: copilot.tencent.com
"""

import secrets
import time
import uuid

# International chat compatibility from upstream PR #381 (24a2cda).
DESKTOP_VERSION_INTL = "5.8.4"
DESKTOP_UA_INTL = "WorkBuddy/5.8.4 workbuddy-ai/5.8.4 CLI/2.161.0"

DEFAULT_IDE_VERSION = "2.117.2"
DEFAULT_WORKBUDDY_IDE_VERSION = "1.119.0"
DEFAULT_WORKBUDDY_PRODUCT_VERSION = "4.9.29177644"

PRODUCT_DESKTOP = "workbuddy"
PRODUCT_VSCODE = "vscode"
PRODUCT_CLI = "cli"
VALID_PRODUCTS = (PRODUCT_DESKTOP, PRODUCT_VSCODE, PRODUCT_CLI)


def normalize_product(value):
    """把各种写法收敛成 'workbuddy'、'vscode' 或 'cli'。"""
    v = str(value or "").strip().lower()
    if v in ("workbuddy", "wb", "desktop"):
        return PRODUCT_DESKTOP
    if v in ("vscode", "vsc", "ide"):
        return PRODUCT_VSCODE
    if v in ("cli", "codebuddy"):
        return PRODUCT_CLI
    return PRODUCT_DESKTOP


_ENDPOINTS = {
    ("intl", PRODUCT_DESKTOP): ("https://www.workbuddy.ai", "www.workbuddy.ai"),
    ("intl", PRODUCT_VSCODE): ("https://www.workbuddy.ai", "www.workbuddy.ai"),
    # www.codebuddy.ai does not resolve on the networks this was measured on
    # (getaddrinfo fails; the system resolver hands back the 0.0.0.1 sinkhole),
    # so the international CLI identity rides the workbuddy.ai edge, which
    # accepts CLI headers and answers normally.
    ("intl", PRODUCT_CLI): ("https://www.workbuddy.ai", "www.workbuddy.ai"),
    ("cn", PRODUCT_DESKTOP): ("https://copilot.tencent.com", "copilot.tencent.com"),
    ("cn", PRODUCT_VSCODE): ("https://www.workbuddy.cn", "www.workbuddy.cn"),
    ("cn", PRODUCT_CLI): ("https://copilot.tencent.com", "copilot.tencent.com"),
}


def endpoint_for(realm, product):
    """回传 (chat base URL, X-Domain)。身分换了，端点也要跟着换。"""
    key = ("cn" if realm == "cn" else "intl", normalize_product(product))
    return _ENDPOINTS[key]


def domain_for_realm(realm):
    """X-Domain 的值（预设桌面端）。"""
    return endpoint_for(realm, PRODUCT_DESKTOP)[1]


def uuid7():
    """RFC 9562 timestamp and random bits, also supported on Python 3.12."""
    millis = int(time.time() * 1000) & ((1 << 48) - 1)
    return uuid.UUID(int=(millis << 80) | (7 << 76) | (secrets.randbits(12) << 64)
                     | (2 << 62) | secrets.randbits(62))


def new_conversation_id():
    return str(uuid7())


def new_message_id():
    return uuid7().hex


def trace_headers():
    """Keep W3C and B3 trace fields consistent for one outbound request."""
    trace = new_message_id()
    span = secrets.token_hex(8)
    return {
        "X-Trace-ID": trace,
        "traceparent": "00-%s-%s-01" % (trace, span),
        "b3": "%s-%s-1" % (trace, span),
        "X-B3-TraceId": trace,
        "X-B3-SpanId": span,
        "X-B3-Sampled": "1",
    }


def build_identity_headers(product, realm, uid, token,
                           conversation_id=None, enterprise_id="",
                           tenant_id="", department=""):
    """回传这一轮要用的身分标头。product = 'workbuddy' (桌面端), 'vscode' 或 'cli'。"""
    product = normalize_product(product)
    is_cn = (realm == "cn")

    if product == PRODUCT_DESKTOP and not is_cn:
        conv = str(conversation_id or "").strip() or new_conversation_id()
        msg_id = new_message_id()
    else:
        conv = str(conversation_id or "").strip() or str(uuid.uuid4()).upper()
        msg_id = uuid.uuid4().hex

    if product == PRODUCT_DESKTOP:
        client_ver = "5.5.6" if is_cn else DESKTOP_VERSION_INTL
        chat_ua = ("WorkBuddy/5.5.6 WorkBuddy/5.5.6 CLI/2.137.1" if is_cn
                   else DESKTOP_UA_INTL)
        headers = {
            "X-Agent-Purpose": "conversation",
            "X-IDE-Name": "WorkBuddy",
            "X-IDE-Type": "WorkBuddy",
            "X-IDE-Version": client_ver,
            "X-Product": "WorkBuddy" if is_cn else "SaaS",
            "X-Domain": endpoint_for(realm, product)[1],
            "User-Agent": chat_ua,
            "X-User-Id": str(uid or "anonymous"),
            "Authorization": "Bearer " + str(token or ""),
            "X-Request-ID": msg_id,
        }
        if not is_cn:
            conversation_request_id = new_message_id()
            headers.update({
                "X-Agent-Intent": "craft",
                "X-Agent-Type": "main",
                "X-Private-Data": "true",
                "X-Conversation-ID": conv,
                "X-Conversation-Request-ID": conversation_request_id,
                "X-Root-Request-ID": conversation_request_id,
                "X-Conversation-Message-ID": msg_id,
            })
            headers.update(trace_headers())
    elif product == PRODUCT_VSCODE:
        headers = {
            "X-Agent-Intent": "craft",
            "X-IDE-Type": "VSCode",
            "X-IDE-Name": "VSCode",
            "X-IDE-Version": DEFAULT_WORKBUDDY_IDE_VERSION,
            "X-Product-Version": DEFAULT_WORKBUDDY_PRODUCT_VERSION,
            "X-Env-ID": "production",
            "X-Product": "SaaS",
            "X-Domain": endpoint_for(realm, product)[1],
            "User-Agent": "VSCode/%s WorkBuddy/%s" % (
                DEFAULT_WORKBUDDY_IDE_VERSION, DEFAULT_WORKBUDDY_PRODUCT_VERSION),
            "X-User-Id": str(uid or "anonymous"),
            "Authorization": "Bearer " + str(token or ""),
            "X-Conversation-ID": conv,
            "X-Conversation-Request-ID": uuid.uuid4().hex,
            "X-Conversation-Message-ID": msg_id,
            "X-Request-ID": msg_id,
        }
    else:  # PRODUCT_CLI
        ide_version = DEFAULT_IDE_VERSION
        headers = {
            "X-Agent-Intent": "craft",
            "X-IDE-Type": "CLI",
            "X-IDE-Name": "CLI",
            "X-IDE-Version": ide_version,
            "X-Product": "SaaS",
            "X-Domain": endpoint_for(realm, product)[1],
            "User-Agent": "CLI/%s CodeBuddy/%s" % (ide_version, ide_version),
            "X-User-Id": str(uid or "anonymous"),
            "Authorization": "Bearer " + str(token or ""),
            "X-Conversation-ID": conv,
            "X-Conversation-Request-ID": uuid.uuid4().hex,
            "X-Conversation-Message-ID": msg_id,
            "X-Request-ID": msg_id,
        }

    if enterprise_id:
        headers["X-Enterprise-Id"] = str(enterprise_id)
        headers["X-Tenant-Id"] = str(tenant_id or enterprise_id)
    if department:
        headers["X-Department-Info"] = str(department)

    return headers
