"""Encrypted, live outbound proxy settings; never proxy internal control traffic."""
import base64
import ipaddress
import json
import os
import secrets
import threading
from urllib.parse import urlsplit

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import HTTPException
from admin.server import write_json

AAD = b'unified2api-outbound-v1'
FIELDS = ('global_proxy', 'antigravity_proxy')


def validate_proxy(value):
    if not isinstance(value, str):
        raise HTTPException(400, '代理地址必须是 HTTP/HTTPS URL，留空表示清除')
    value = value.strip()
    if not value:
        return ''
    try:
        url = urlsplit(value)
        if (url.scheme not in {'http', 'https'} or not url.hostname or
                url.path not in {'', '/'} or url.query or url.fragment or
                any(c.isspace() or ord(c) < 32 for c in value) or len(value) > 2048):
            raise ValueError()
        if url.port is not None and not 1 <= url.port <= 65535:
            raise ValueError()
    except ValueError:
        raise HTTPException(400, '请输入有效的 HTTP/HTTPS 代理地址（可包含用户名和密码）') from None
    return value


def internal_target(url):
    host = (urlsplit(str(url)).hostname or '').lower()
    if host in {'localhost', 'internal', 'workbuddy-hub', 'docker', 'probe'}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class ProxySettings:
    def __init__(self, store, apply_native=None):
        self.path = store.root / 'outbound-proxies.enc.json'
        key = store.root / '.outbound-proxy.key'
        if not key.exists():
            if self.path.exists():
                raise RuntimeError('Outbound proxy encryption key is missing')
            try:
                with os.fdopen(os.open(key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as handle:
                    handle.write(secrets.token_bytes(32))
            except FileExistsError:
                pass
        self.cipher = AESGCM(key.read_bytes())
        self.lock = threading.RLock()
        self.apply_native = apply_native
        self.values = {}
        if self.path.exists():
            doc = json.loads(self.path.read_text(encoding='utf-8'))
            self.values = json.loads(self.cipher.decrypt(base64.b64decode(doc['nonce']), base64.b64decode(doc['data']), AAD))
        for value in self.values.values():
            validate_proxy(value)

    def configured(self, field):
        with self.lock:
            return self.values.get(field, os.getenv('ANTIGRAVITY_PROXY' if field == 'antigravity_proxy' else 'UNIFIED_OUTBOUND_PROXY', '').strip())

    def resolve(self, provider='', url=None):
        if url is not None and internal_target(url):
            return None
        return ((self.configured('antigravity_proxy') if provider == 'antigravity' else '') or
                self.configured('global_proxy') or None)

    def options(self, provider='', transport=None):
        # Explicit transports are test doubles or in-process clients.
        return {'proxy': None if transport is not None else self.resolve(provider), 'trust_env': False}

    def public(self):
        return {'settings': {field: {'configured': bool(self.configured(field))} for field in FIELDS},
                'antigravity_source': 'independent' if self.configured('antigravity_proxy') else 'global' if self.configured('global_proxy') else 'direct'}

    def update(self, body):
        if not isinstance(body, dict) or not body or set(body) - set(FIELDS):
            raise HTTPException(400, '代理设置包含不支持的字段')
        patch = {field: validate_proxy(value) for field, value in body.items()}
        with self.lock:
            values = {**self.values, **patch}
            nonce = secrets.token_bytes(12)
            sealed = self.cipher.encrypt(nonce, json.dumps(values).encode(), AAD)
            previous = self.configured('global_proxy')
            desired = values.get('global_proxy', previous)
            if self.apply_native:
                self.apply_native(desired)
            try:
                write_json(self.path, {'version': 1, 'nonce': base64.b64encode(nonce).decode(), 'data': base64.b64encode(sealed).decode()})
            except Exception:
                if self.apply_native:
                    self.apply_native(previous)
                raise
            self.values = values
        return self.public()


def install_workbuddy(module, settings):
    """Keep account-specific exits ahead of the optional global fallback."""
    original = vars(module.urlopen).get('_unified_original', module.urlopen)
    def urlopen(req, timeout=30, proxy=''):
        url = req.full_url if hasattr(req, 'full_url') else str(req)
        return original(req, timeout=timeout, proxy=proxy or settings.resolve(url=url) or '')
    urlopen._unified_original = original
    module.urlopen = urlopen


def register(app, store, settings, body_json):
    from fastapi import Request
    @app.api_route('/admin/api/unified/proxies', methods=['GET', 'PATCH'])
    async def proxies(req: Request):
        store.require_admin(req)
        return settings.update(await body_json(req)) if req.method == 'PATCH' else settings.public()
