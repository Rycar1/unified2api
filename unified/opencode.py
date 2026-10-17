"""Opt-in OpenCode headers. Store opaque IDs only, never conversation content."""
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from collections import OrderedDict

from fastapi import HTTPException

SESSION_HEADERS = ('x-opencode-session', 'session_id', 'x-session-id',
    'x-deepseek-harness-session-id', 'x-claude-code-session-id', 'x-codex-session-id', 'x-conversation-id')
USER_AGENT = 'Unified2API/1.0 (OpenCode support)'


def clean_header(value):
    return isinstance(value, str) and 0 < len(value) <= 512 and all(32 <= ord(c) < 127 for c in value)


class OpenCodeSupport:
    def __init__(self, root, clock=time.time):
        self.key_path = root / '.opencode-session.key'
        self.clock = clock
        self.lock = threading.RLock()
        self.responses = OrderedDict()
        self.key = None

    def digest(self, value):
        with self.lock:
            if self.key is None:
                try:
                    with os.fdopen(os.open(self.key_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), 'wb') as handle:
                        handle.write(secrets.token_bytes(32))
                except FileExistsError:
                    pass
                self.key = self.key_path.read_bytes()
                if len(self.key) != 32:
                    raise RuntimeError('OpenCode session key has an invalid length')
            data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
            return hmac.new(self.key, data, hashlib.sha256).hexdigest()

    def prepare(self, cid, scope, body):
        incoming = {k.decode('latin-1').lower(): v.decode('latin-1') for k, v in scope.get('headers', [])}
        token = incoming.get('authorization', '').removeprefix('Bearer ').strip() or incoming.get('x-api-key', '')
        namespace = self.digest([cid, token])
        headers = {}
        for name in SESSION_HEADERS:
            value = incoming.get(name)
            if value is not None:
                if not clean_header(value) or not value.strip():
                    raise HTTPException(400, '客户端会话头格式无效，请使用不超过 512 字符的 ASCII 会话 ID')
                headers[name] = value
        session = next(iter(headers.values()), None)
        try:
            payload = json.loads(body)
        except (ValueError, TypeError):
            payload = {}
        if not isinstance(payload, dict): payload = {}
        if session is None:
            previous = payload.get('previous_response_id')
            with self.lock:
                self.expire()
                known = self.responses.get((namespace, previous)) if isinstance(previous, str) else None
            if known:
                session = known[1]
            else:
                metadata = payload.get('metadata')
                metadata = metadata if isinstance(metadata, dict) else {}
                identity = next((value for value in (payload.get('conversation_id'), metadata.get('session_id'), metadata.get('conversation_id'))
                                 if isinstance(value, str) and value), None)
                messages = payload.get('messages', payload.get('input', []))
                if isinstance(messages, str): messages = [{'role':'user', 'content':messages}]
                messages = messages if isinstance(messages, list) else []
                system = [m.get('content') for m in messages if isinstance(m, dict) and m.get('role') in {'system', 'developer'}]
                first = next((m.get('content') for m in messages if isinstance(m, dict) and m.get('role') == 'user'), None)
                # Without a client ID, the initial context is an approximation:
                # identical opening contexts share affinity; compaction can change it.
                seed = ['id', identity] if identity else ['context', system, first] if first is not None else ['orphan', previous or secrets.token_hex(16)]
                session = 'u2a-' + self.digest([namespace, seed])
        headers['x-opencode-session'] = session
        agent = incoming.get('user-agent', '')
        generic = agent.lower().startswith(('python-', 'httpx/', 'curl/', 'go-http-client/', 'openai/', 'node'))
        headers['User-Agent'] = agent if clean_header(agent) and not generic else USER_AGENT
        return headers, (namespace, session)

    def expire(self):
        now = self.clock()
        while self.responses and next(iter(self.responses.values()))[0] < now-3600:
            self.responses.popitem(last=False)

    def observe(self, context, raw):
        try:
            value = json.loads(raw)
        except (ValueError, TypeError):
            return
        if not isinstance(value, dict): return
        response = value.get('response', value)
        if not isinstance(response, dict): return
        rid = response.get('id')
        if not isinstance(rid, str) or not rid or len(rid) > 512: return
        namespace, session = context
        with self.lock:
            self.expire()
            pair = (namespace, rid)
            self.responses[pair] = (self.clock(), session)
            self.responses.move_to_end(pair)
            while len(self.responses) > 4096: self.responses.popitem(last=False)
