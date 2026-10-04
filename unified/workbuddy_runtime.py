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
        args = SimpleNamespace(accounts_dir=str(self.accounts_dir), api_key=self.api_key,
            panel_password=self.admin_key, system_prompt="You are a helpful assistant.", lan=False)
        # Bind before starting the scheduler; a port conflict must fail startup.
        self.server = ThreadingHTTPServer(("127.0.0.1", self.port), self.proxy.Handler)
        self.server.daemon_threads = True
        self.server.block_on_close = False
        try:
            self.proxy._bootstrap_runtime(args)
            self.thread = threading.Thread(target=self.server.serve_forever, name="workbuddy-internal", daemon=True)
            self.thread.start()
        except Exception:
            self.server.server_close()
            if self.proxy.SCHEDULER:
                self.proxy.SCHEDULER.stop()
            raise

    def stop(self):
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
