"""Bounded streaming bridge to the embedded Go provider."""
import asyncio
import base64
import ctypes
import json
import os


class Trae:
    def __init__(self):
        self.lib = ctypes.CDLL(os.environ.get("TRAE_LIBRARY", "/app/libtrae.so"))
        for name, args, result in [
            ("TraeInit", [], ctypes.c_void_p),
            ("TraeStart", [ctypes.c_char_p] * 4, ctypes.c_ulonglong),
            ("TraeRead", [ctypes.c_ulonglong], ctypes.c_void_p),
            ("TraeCancel", [ctypes.c_ulonglong], None),
            ("TraeStop", [], None),
            ("TraeFree", [ctypes.c_void_p], None),
        ]:
            fn = getattr(self.lib, name)
            fn.argtypes, fn.restype = args, result
        error = self.string(self.lib.TraeInit())
        if error:
            raise RuntimeError(error)

    def string(self, pointer):
        try:
            return ctypes.string_at(pointer).decode()
        finally:
            self.lib.TraeFree(pointer)

    async def events(self, method, path, body=b""):
        handle = self.lib.TraeStart(method.encode(), path.encode(), body,
                                    os.environ["UNIFIED_API_KEY"].encode())
        try:
            while True:
                event = json.loads(await asyncio.to_thread(lambda: self.string(self.lib.TraeRead(handle))))
                if event.get("done"):
                    break
                if event:
                    yield event
        finally:
            self.lib.TraeCancel(handle)

    async def request(self, method, path, body=b""):
        status, chunks = 500, []
        async for event in self.events(method, path, body):
            status = event.get("status", status)
            if event.get("data"):
                chunks.append(base64.b64decode(event["data"]))
        content = b"".join(chunks)
        try:
            data = json.loads(content)
        except ValueError:
            data = {"detail": "TRAE 返回格式异常"}
        return status, data

    def close(self):
        self.lib.TraeStop()
