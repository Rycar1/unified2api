"""Shared Qoder Chat/Responses/Messages conversion and bounded continuations."""
import copy
import json
import secrets
import time
from collections import OrderedDict

from fastapi import HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from core.anthropic_adapter import AnthropicStreamConverter, anthropic_request_to_chat
from core.responses_adapter import ResponsesStreamConverter, responses_request_to_chat
from .qoder_hub import compact


def content_parts(content):
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    parts = []
    for block in content or []:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind in {"text", "input_text", "output_text"}:
            parts.append({"type": "text", "text": block.get("text", "")})
        elif kind in {"image_url", "input_image", "image"}:
            source = block.get("source") or {}
            image = block.get("image_url")
            url = image.get("url") if isinstance(image, dict) else image
            if kind == "image":
                url = source.get("url") if source.get("type") == "url" else (
                    "data:" + source.get("media_type", "image/png") + ";base64," + source["data"] if source.get("data") else None)
            if url:
                parts.append({"type": "image_url", "image_url": {"url": url}})
    return parts


def request_to_chat(payload, path):
    if path == "/v1/responses":
        chat = responses_request_to_chat(payload)
        source = payload.get("input")
        if isinstance(source, list):
            users = [row for row in source if isinstance(row, dict) and row.get("role") == "user"]
            targets = [row for row in chat.get("messages", []) if row.get("role") == "user"]
            for target, original in zip(targets, users):
                parts = content_parts(original.get("content", ""))
                if any(part["type"] == "image_url" for part in parts):
                    target["content"] = parts
        return chat
    if path != "/v1/messages":
        return payload
    prepared = copy.deepcopy(payload)
    replacements = {}
    for row in prepared.get("messages", []):
        if row.get("role") != "user" or not isinstance(row.get("content"), list):
            continue
        blocks = row["content"]
        parts = content_parts(blocks)
        for tool in blocks:
            if isinstance(tool, dict) and tool.get("type") == "tool_result":
                parts.extend(part for part in content_parts(tool.get("content")) if part["type"] == "image_url")
        if any(part["type"] == "image_url" for part in parts):
            marker = "qoder-image-" + secrets.token_hex(16)
            replacements[marker] = parts
            row["content"] = [block for block in blocks if isinstance(block, dict) and block.get("type") == "tool_result"] + [{"type": "text", "text": marker}]
    chat = anthropic_request_to_chat(prepared)
    messages = chat.get("messages", [])
    rebuilt = []
    index = 0
    while index < len(messages):
        row = messages[index]
        marker = row.get("content")
        if isinstance(marker, str) and marker in replacements:
            # Tool replies must precede the user image attachment.
            index += 1
            while index < len(messages) and messages[index].get("role") == "tool":
                rebuilt.append(messages[index])
                index += 1
            rebuilt.append({**row, "content": replacements[marker]})
        else:
            rebuilt.append(row)
            index += 1
    chat["messages"] = rebuilt
    return chat


class QoderRelay:
    def __init__(self):
        self.history = OrderedDict()
        self.history_bytes = 0

    def remember(self, chat, result):
        history = chat.get("messages", []) + responses_request_to_chat({"input": result.get("output", [])}).get("messages", [])
        size = len(compact(history).encode())
        if size > 16 * 1024 * 1024:
            return
        rid = result["id"]
        if rid in self.history:
            self.history_bytes -= self.history.pop(rid)[2]
        self.history[rid] = (time.monotonic() + 3600, history, size)
        self.history_bytes += size
        while len(self.history) > 256 or self.history_bytes > 16 * 1024 * 1024:
            self.history_bytes -= self.history.popitem(last=False)[1][2]

    async def relay(self, path, body, scope, receive, send):
        payload = json.loads(body)
        adapter = None
        if path == "/v1/responses":
            adapter = ResponsesStreamConverter(model=payload["model"])
            chat = request_to_chat(payload, path)
            previous = payload.get("previous_response_id")
            if previous:
                record = self.history.get(previous)
                if not record or record[0] <= time.monotonic():
                    raise HTTPException(400, "previous_response_id 不存在或已过期，请发送完整上下文")
                chat["messages"] = record[1] + chat.get("messages", [])
        elif path == "/v1/messages":
            adapter = AnthropicStreamConverter(model=payload["model"])
            chat = request_to_chat(payload, path)
        else:
            chat = payload
        if not isinstance(chat.get("messages"), list) or not chat["messages"]:
            raise HTTPException(400, "请提供有效消息")
        source = self.chat_chunks(chat)
        try:
            first = await anext(source)
        except StopAsyncIteration:
            raise HTTPException(502, "Qoder 没有返回模型输出") from None
        rid, created = "chatcmpl-" + secrets.token_hex(12), int(time.time())

        async def normalized():
            async def frames():
                yield first
                async for frame in source:
                    yield frame
            try:
                async for frame in frames():
                    yield {**frame, "id": rid, "object": "chat.completion.chunk", "created": created, "model": payload["model"]}
            finally:
                await source.aclose()

        if payload.get("stream", path == "/v1/responses"):
            async def stream():
                try:
                    async for frame in normalized():
                        line = "data: " + compact(frame)
                        event = adapter.feed_line(line) if adapter else line + "\n\n"
                        if event:
                            yield event.encode()
                    if adapter:
                        yield adapter.finish().encode()
                        if path == "/v1/responses":
                            self.remember(chat, adapter.get_nonstream_response())
                    else:
                        yield b"data: [DONE]\n\n"
                except HTTPException as exc:
                    error = {"message": str(exc.detail), "code": exc.status_code, "type": "upstream_error"}
                    if path == "/v1/responses":
                        data = {"type": "response.failed", "response": {"status": "failed", "error": error}}
                        yield ("event: response.failed\ndata: " + compact(data) + "\n\n").encode()
                    elif path == "/v1/messages":
                        yield ("event: error\ndata: " + compact({"type": "error", "error": error}) + "\n\n").encode()
                    else:
                        yield ("data: " + compact({"error": error}) + "\n\ndata: [DONE]\n\n").encode()
            response = StreamingResponse(stream(), media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
        else:
            content, reasoning, tools, usage, finish = [], [], {}, None, "stop"
            async for frame in normalized():
                if adapter:
                    adapter.feed_line("data: " + compact(frame))
                if frame.get("usage"):
                    usage = frame["usage"]
                for choice in frame.get("choices", []):
                    delta = choice.get("delta") or {}
                    content.append(delta.get("content") or "")
                    reasoning.append(delta.get("reasoning_content") or "")
                    finish = choice.get("finish_reason") or finish
                    for call in delta.get("tool_calls") or []:
                        tool = tools.setdefault(call.get("index", 0), {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                        if call.get("id"):
                            tool["id"] = call["id"]
                        for field in ("name", "arguments"):
                            tool["function"][field] += (call.get("function") or {}).get(field, "")
            if adapter:
                result = adapter.get_nonstream_response()
                if path == "/v1/responses":
                    self.remember(chat, result)
            else:
                message = {"role": "assistant", "content": "".join(content)}
                if any(reasoning):
                    message["reasoning_content"] = "".join(reasoning)
                if tools:
                    message["tool_calls"] = list(tools.values())
                result = {"id": rid, "object": "chat.completion", "created": created, "model": payload["model"],
                    "choices": [{"index": 0, "message": message, "finish_reason": finish}], "usage": usage}
            response = JSONResponse(result)
        try:
            return await response(scope, receive, send)
        finally:
            await source.aclose()
