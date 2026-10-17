"""Lazy, loopback-only OpenCode2API child process, independent of gateway secrets."""
import asyncio
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import subprocess

import httpx
from fastapi import HTTPException


class OpenCodeRuntime:
    base_url = 'http://127.0.0.1:8789/v1'

    def __init__(self, root, source=None):
        self.root = Path(root).resolve()
        self.source = source or Path(__file__).resolve().parent.parent / 'vendor/opencode2api'
        self.token = secrets.token_urlsafe(36)
        self.password = secrets.token_urlsafe(36)
        self.process = None
        self.lock = asyncio.Lock()
        self.outbound = None

    def status(self):
        available = bool(shutil.which('node') and shutil.which('opencode') and
                         (self.source / 'node_modules/express/package.json').is_file())
        return {'available':available, 'running':bool(self.process and self.process.poll() is None),
                'version':'2.5.0', 'opencode_version':'1.19.0',
                'message':'' if available else '当前环境未安装内置 OpenCode2API 运行时，请使用新版 Docker 镜像'}

    def environment(self):
        # Do not inherit ADMIN_KEY, provider tokens, host credentials or client configs.
        env = {key:os.environ[key] for key in ('PATH','SystemRoot','SYSTEMROOT','WINDIR','PATHEXT') if key in os.environ}
        env.update(HOME=str(self.root/'home'), USERPROFILE=str(self.root/'home'),
            XDG_CONFIG_HOME=str(self.root/'home/config'), XDG_DATA_HOME=str(self.root/'home/data'),
            XDG_CACHE_HOME=str(self.root/'home/cache'), XDG_STATE_HOME=str(self.root/'home/state'),
            TMPDIR=str(self.root/'tmp'), TEMP=str(self.root/'tmp'), TMP=str(self.root/'tmp'),
            API_KEY=self.token, OPENCODE_SERVER_PASSWORD=self.password,
            OPENCODE_PROXY_PORT='8789', OPENCODE_SERVER_PORT='8790',
            OPENCODE_SERVER_URL='http://127.0.0.1:8790', BIND_HOST='127.0.0.1',
            OPENCODE_PATH=shutil.which('opencode') or 'opencode',
            OPENCODE_PROXY_MANAGE_BACKEND='true', OPENCODE_USE_ISOLATED_HOME='false',
            OPENCODE_DISABLE_TOOLS='true', OPENCODE_INTERNAL_ALLOWED_TOOLS='',
            OPENCODE_PROXY_OMIT_SYSTEM_PROMPT='false', OPENCODE_PROXY_DEBUG='false',
            OPENCODE_WEB_PANEL_ENABLED='false', OPENCODE_PUBLIC_DIAGNOSTICS='false',
            OPENCODE_PROXY_AUTO_CLEANUP_CONVERSATIONS='true',
            OPENCODE_PROXY_CLEANUP_INTERVAL_MS='300000', OPENCODE_PROXY_CLEANUP_MAX_AGE_MS='3600000',
            OPENCODE_PROXY_REQUEST_TIMEOUT_MS='900000',
            OPENCODE2API_EVENT_FIRST_DELTA_TIMEOUT_MS='180000', OPENCODE2API_EVENT_IDLE_TIMEOUT_MS='180000',
            OPENCODE_CONFIG_CONTENT=json.dumps({'enabled_providers':['opencode'], 'autoupdate':False}))
        if self.outbound:
            proxy = self.outbound.resolve()
            if proxy: env.update(HTTP_PROXY=proxy, HTTPS_PROXY=proxy, NO_PROXY='127.0.0.1,localhost')
        return env

    def launch(self):
        options = {}
        owner = None
        if os.name == 'posix' and os.geteuid() == 0:
            import pwd
            try: owner = pwd.getpwnam('opencode-runtime')
            except KeyError: raise HTTPException(503, '请使用包含独立运行用户的新版 Docker 镜像') from None
            options.update(user=owner.pw_uid, group=owner.pw_gid, extra_groups=[])
        for path in (self.root, self.root/'home', self.root/'tmp', self.root/'workspace'):
            if path.is_symlink(): raise HTTPException(503, 'OpenCode 运行目录不能是符号链接')
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
            path.chmod(0o700)
            if owner: os.chown(path, owner.pw_uid, owner.pw_gid)
        if os.name == 'nt':
            options['creationflags'] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        else: options['start_new_session'] = True
        self.process = subprocess.Popen([shutil.which('node'), str(self.source/'index.js')],
            cwd=self.root/'workspace', env=self.environment(), stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **options)

    async def ensure(self):
        async with self.lock:
            if not self.status()['available']:
                raise HTTPException(503, self.status()['message'])
            if self.process and self.process.poll() is not None:
                await asyncio.to_thread(self.stop)
            if not self.process:
                try: self.launch()
                except OSError: raise HTTPException(503, 'OpenCode2API 运行时启动失败，请检查镜像和运行目录权限') from None
            try:
                async with asyncio.timeout(90), httpx.AsyncClient(trust_env=False, timeout=1) as client:
                    while True:
                        if self.process.poll() is not None:
                            raise HTTPException(503, f'OpenCode2API 进程已退出（{self.process.returncode}），请检查端口和依赖')
                        try:
                            response = await client.get(self.base_url.removesuffix('/v1')+'/health')
                            backend = await client.get('http://127.0.0.1:8790/global/health',
                                auth=httpx.BasicAuth('opencode',self.password))
                            if (response.status_code == backend.status_code == 200 and
                                response.json().get('status') == 'ok' and backend.json().get('healthy') is True): return
                        except (httpx.HTTPError, ValueError): pass
                        await asyncio.sleep(.25)
            except TimeoutError: pass
            raise HTTPException(503, 'OpenCode2API 启动超时，请稍后重试')

    def stop(self):
        process = self.process
        if not process: return
        if os.name == 'nt':
            if process.poll() is None:
                subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            try: os.killpg(process.pid,signal.SIGTERM)
            except ProcessLookupError: pass
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try: os.killpg(process.pid,signal.SIGKILL)
                except ProcessLookupError: pass
        try: process.wait(timeout=5)
        except subprocess.TimeoutExpired: pass
        self.process = None

    async def close(self):
        await asyncio.to_thread(self.stop)
