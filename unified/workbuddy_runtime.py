"""Lifecycle-managed WorkBuddy Python runtime, private to the main container."""
import asyncio
import importlib
from pathlib import Path
import sys
import threading
from http.server import ThreadingHTTPServer
from types import SimpleNamespace


class EmbeddedWorkBuddy:
    def __init__(self, api_key, admin_key, accounts_dir, usage_dir, port=8788):
        self.api_key, self.admin_key = api_key, admin_key
        self.accounts_dir, self.usage_dir, self.port = accounts_dir, usage_dir, port
        self.server = self.thread = self.proxy = None

    def start(self):
        source = Path(__file__).resolve().parents[1] / "vendor" / "workbuddy2api-hub"
        if str(source) not in sys.path:
            sys.path.insert(0, str(source))
        self.proxy = importlib.import_module("wb_proxy")
        self.proxy.USAGE_DIR = str(self.usage_dir)
        self.proxy.USAGE_LOG = str(Path(self.usage_dir) / "usage.jsonl")
        for name,filename in {'LIMIT_EVENTS_FILE':'limit-events.jsonl','ESTIMATE_DAILY_FILE':'estimate-daily.json',
                              'RECENT_INDEX_FILE':'usage-recent-index.json'}.items():
            if hasattr(self.proxy,name): setattr(self.proxy,name,str(Path(self.usage_dir)/filename))
        from .workbuddy_hooks import install
        install(self.proxy)
        if hasattr(self.proxy,'wb_activity'): self.proxy.wb_activity.set_data_dir(str(self.usage_dir))
        args = SimpleNamespace(accounts_dir=str(self.accounts_dir), api_key=self.api_key,
            panel_password=self.admin_key, system_prompt="You are a helpful assistant.", lan=False)
        # Bind before starting the scheduler; a port conflict must fail startup.
        self.server = ThreadingHTTPServer(("127.0.0.1", self.port), self.proxy.Handler)
        self.server.daemon_threads = True
        self.server.block_on_close = False
        try:
            self.proxy._bootstrap_runtime(args)
            from .workbuddy_activity import ActivityConfig,WebActivity
            config=getattr(self,'config',None) or ActivityConfig(Path(self.accounts_dir)/'.unified-web-activity')
            self.activity=WebActivity(config)
            self.activity.install(self.proxy)
            self.thread = threading.Thread(target=self.server.serve_forever, name="workbuddy-internal", daemon=True)
            self.thread.start()
        except Exception:
            self.server.server_close()
            if self.proxy.SCHEDULER:
                self.proxy.SCHEDULER.stop()
            raise

    def stop(self):
        if self.proxy:
            for name in ('PRICING','CREDITS_REFRESHER','UPDATES'):
                worker=getattr(self.proxy,name,None)
                if worker and hasattr(worker,'stop'):
                    worker.stop()
                    if isinstance(worker,threading.Thread) and worker.is_alive(): worker.join(timeout=3)
        if self.proxy and self.proxy.SCHEDULER:
            self.proxy.SCHEDULER.stop()
            scheduler_thread = self.proxy.SCHEDULER._thread
            if scheduler_thread:
                scheduler_thread.join(timeout=3)
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        if self.thread:
            self.thread.join(timeout=3)

    async def __aenter__(self):
        await asyncio.to_thread(self.start)
        return self

    async def __aexit__(self, *exc):
        await asyncio.to_thread(self.stop)
