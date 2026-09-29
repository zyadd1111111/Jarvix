"""OpenAI Chat Completions adapter. No UI, storage or tool execution dependencies."""
from __future__ import annotations

import httpx

from jarvix.domain import Completion, Message, ProviderError, ToolCall, ToolSpec
from jarvix.providers._names import domain_name, name_map, wire_name
from jarvix.providers._transport import (
    MAX_ARGUMENT_BYTES, MAX_OUTPUT_TOKENS, MAX_TOOL_CALLS,
    Transport, arguments, dump_json, load_json, validate_model,
)


class OpenAIProvider:
    id = "openai"
    supports_vision = True

    def __init__(self, api_key: str, client: httpx.Client | None = None) -> None:
        self._transport = Transport(api_key, client)

    def complete(self, messages: list[Message], tools: list[ToolSpec], model: str) -> Completion:
        model = validate_model(model)
        data = self._transport.request(
            "https://api.openai.com/v1/chat/completions", self._payload(messages, tools, model),
            {"Authorization": f"Bearer {self._transport._api_key}"},
        )
        return self._parse(data, tools, model)

    def _payload(self, messages, tools, model):
        payload: dict = {
            "model": model, "messages": [self._message(m) for m in messages],
            "max_completion_tokens": MAX_OUTPUT_TOKENS, "store": False,
        }
        if tools:
            payload["tools"] = [{"type": "function", "function": {
                "name": wire_name(t.name), "description": t.description, "parameters": t.parameters,
            }} for t in tools]
            payload["tool_choice"] = "auto"
            payload["parallel_tool_calls"] = False
        return payload

    def stream(self, messages, tools, model, on_delta):
        model = validate_model(model)
        payload = self._payload(messages, tools, model)
        payload.update(stream=True, stream_options={"include_usage": True})
        text, refusal, calls, usage, finish = "", "", {}, {}, None
        for event in self._transport.events("https://api.openai.com/v1/chat/completions", payload,
                                           {"Authorization": f"Bearer {self._transport._api_key}"}):
            try:
                choices = event.get("choices", [])
                if not isinstance(choices, list) or len(choices) > 1 or "error" in event:
                    raise ValueError
                if event.get("usage"):
                    usage = event["usage"]
                if not choices:
                    continue
                choice = choices[0]
                if "message" in choice:
                    result = self._parse(event, tools, model)
                    on_delta(result.message.content)
                    return result
                if choice.get("index", 0) != 0 or finish is not None:
                    raise ValueError
                delta = choice["delta"]
                if delta.get("role", "assistant") != "assistant":
                    raise ValueError
                for key in ("content", "refusal"):
                    part = delta.get(key) or ""
                    if not isinstance(part, str):
                        raise ValueError
                    if key == "content":
                        text += part
                    else:
                        refusal += part
                    if part:
                        on_delta(part)
                for call in delta.get("tool_calls") or []:
                    index = call["index"]
                    if not isinstance(index, int) or not 0 <= index < MAX_TOOL_CALLS:
                        raise ValueError
                    target = calls.setdefault(index, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                    if call.get("type", "function") != "function":
                        raise ValueError
                    if call.get("id"):
                        if not isinstance(call["id"], str):
                            raise ValueError
                        target["id"] += call["id"]
                    for key in ("name", "arguments"):
                        value = call.get("function", {}).get(key) or ""
                        if not isinstance(value, str):
                            raise ValueError
                        target["function"][key] += value
                    if len(target["function"]["arguments"].encode()) > MAX_ARGUMENT_BYTES:
                        raise ValueError
                finish = choice.get("finish_reason")
            except (KeyError, IndexError, TypeError, ValueError, AttributeError):
                raise ProviderError("OpenAI returned an unsupported stream. No tool was executed.") from None
        data = {"choices": [{"finish_reason": finish, "message": {
            "role": "assistant", "content": text, "refusal": refusal,
            "tool_calls": [calls[index] for index in sorted(calls)],
        }}], "usage": usage}
        return self._parse(data, tools, model)

    def _parse(self, data, tools, model):
        names = name_map(tools)
        try:
            choice = data["choices"][0]
            finish = choice.get("finish_reason")
            if finish == "length":
                raise ProviderError("The AI response reached its output limit. No tools from that response were executed; shorten your request.")
            if finish == "content_filter":
                raise ProviderError("The AI provider declined to return this response under its content policy.")
            if finish not in ("stop", "tool_calls"):
                raise ValueError
            raw = choice["message"]
            if raw.get("role") != "assistant":
                raise ValueError
            text = raw.get("content") or raw.get("refusal") or ""
            if not isinstance(text, str):
                raise ValueError
            raw_calls = raw.get("tool_calls") or []
            if not isinstance(raw_calls, list) or len(raw_calls) > MAX_TOOL_CALLS:
                raise ValueError
            calls: list[ToolCall] = []
            seen: set[str] = set()
            for call in raw_calls:
                call_id = call["id"]
                if (call.get("type") != "function" or not isinstance(call_id, str)
                        or not call_id or len(call_id) > 256 or call_id in seen):
                    raise ValueError
                function = call["function"]
                encoded = function["arguments"]
                if not isinstance(encoded, str) or len(encoded.encode("utf-8")) > MAX_ARGUMENT_BYTES:
                    raise ValueError
                calls.append(ToolCall(call_id, domain_name(function["name"], names), arguments(load_json(encoded))))
                seen.add(call_id)
            if not text and not calls:
                raise ValueError
            if (finish == "tool_calls") != bool(calls):
                raise ValueError
            usage = data.get("usage") or {}
            if not isinstance(usage, dict):
                raise ValueError
        except (KeyError, IndexError, TypeError, ValueError, AttributeError):
            raise ProviderError("OpenAI returned an unsupported or malformed response. No tool was executed.") from None
        return Completion(Message("assistant", text, calls, metadata={"provider": self.id, "model": model}), usage)

    @staticmethod
    def _message(message: Message) -> dict:
        value: dict = {"role": message.role, "content": message.content}
        if message.images:
            if message.role != "user":
                raise ProviderError("Images may only be attached to a user message.")
            value["content"] = [{"type": "text", "text": message.content}, *[{
                "type": "image_url", "image_url": {
                    "url": f"data:{image.mime_type};base64,{image.data_base64}", "detail": "auto",
                },
            } for image in message.images]]
        if message.role == "assistant" and message.tool_calls:
            value["content"] = message.content or None
            value["tool_calls"] = [{
                "id": call.id, "type": "function", "function": {
                    "name": wire_name(call.name), "arguments": dump_json(call.arguments),
                },
            } for call in message.tool_calls]
        elif message.role == "tool":
            if not message.tool_call_id:
                raise ProviderError("The conversation contains an unmatched tool result. Start a new conversation.")
            value["tool_call_id"] = message.tool_call_id
        return value
