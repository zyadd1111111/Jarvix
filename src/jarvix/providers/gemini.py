"""Gemini generateContent adapter with lossless thought-signature round trips."""
from __future__ import annotations

from copy import deepcopy
from uuid import uuid4

import httpx

from jarvix.domain import Completion, Message, ProviderError, ToolCall, ToolSpec
from jarvix.providers._names import domain_name, name_map, wire_name
from jarvix.providers._transport import MAX_OUTPUT_TOKENS, MAX_TOOL_CALLS, Transport, arguments, load_json, validate_model


class GeminiProvider:
    id = "gemini"

    def __init__(self, api_key: str, client: httpx.Client | None = None) -> None:
        self._transport = Transport(api_key, client)

    def complete(self, messages: list[Message], tools: list[ToolSpec], model: str) -> Completion:
        model = validate_model(model)
        names = name_map(tools)
        payload = self._payload(messages)
        payload["generationConfig"] = {"maxOutputTokens": MAX_OUTPUT_TOKENS, "candidateCount": 1}
        if tools:
            payload["tools"] = [{"functionDeclarations": [{
                "name": wire_name(t.name), "description": t.description, "parametersJsonSchema": t.parameters,
            } for t in tools]}]
        data = self._transport.request(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            payload, {"x-goog-api-key": self._transport._api_key},
        )
        try:
            if data.get("promptFeedback", {}).get("blockReason"):
                raise ProviderError("Gemini declined to return this response under its content policy.")
            candidate = data["candidates"][0]
            finish = candidate.get("finishReason")
            if finish == "MAX_TOKENS":
                raise ProviderError("The AI response reached its output limit. No tools from that response were executed; shorten your request.")
            if finish in {"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "IMAGE_SAFETY"}:
                raise ProviderError("Gemini declined to return this response under its content policy.")
            if finish != "STOP":
                raise ValueError
            content = candidate["content"]
            if content.get("role", "model") != "model":
                raise ValueError
            parts = content["parts"]
            if not isinstance(parts, list):
                raise ValueError
            texts: list[str] = []
            calls: list[ToolCall] = []
            call_ids: dict[str, str | None] = {}
            for part in parts:
                if not isinstance(part, dict):
                    raise ValueError
                if "text" in part and "functionCall" in part:
                    raise ValueError
                if "thought" in part and not isinstance(part["thought"], bool):
                    raise ValueError
                if "text" in part:
                    if not isinstance(part["text"], str):
                        raise ValueError
                    if not part.get("thought"):
                        texts.append(part["text"])
                elif "functionCall" in part:
                    if part.get("thought"):
                        raise ValueError
                    function = part["functionCall"]
                    provider_id = function.get("id")
                    if provider_id is not None and (not isinstance(provider_id, str) or not provider_id or len(provider_id) > 256):
                        raise ValueError
                    call_id = provider_id or f"gemini_{uuid4().hex}"
                    if call_id in call_ids:
                        raise ValueError
                    calls.append(ToolCall(call_id, domain_name(function["name"], names), arguments(function.get("args", {}))))
                    call_ids[call_id] = provider_id
                elif set(part) != {"thoughtSignature"}:
                    raise ValueError
                if "thoughtSignature" in part and not isinstance(part["thoughtSignature"], str):
                    raise ValueError
            if len(calls) > MAX_TOOL_CALLS or (not any(texts) and not calls):
                raise ValueError
            usage = data.get("usageMetadata") or {}
            if not isinstance(usage, dict):
                raise ValueError
        except (KeyError, IndexError, TypeError, ValueError, AttributeError):
            raise ProviderError("Gemini returned an unsupported or malformed response. No tool was executed.") from None
        message = Message("assistant", "".join(texts), calls, metadata={
            "provider": self.id, "model": model,
            "gemini_parts": deepcopy(parts), "gemini_call_ids": call_ids,
        })
        return Completion(message, usage)

    @staticmethod
    def _payload(messages: list[Message]) -> dict:
        contents: list[dict] = []
        system: list[dict] = []
        pending: dict[str, tuple[str, str | None]] = {}
        previous_tool = False
        for message in messages:
            if message.role == "system":
                system.append({"text": message.content})
                continue
            if message.role == "tool":
                if message.tool_call_id not in pending:
                    raise ProviderError("The conversation contains an unmatched tool result. Start a new conversation.")
                name, provider_id = pending.pop(message.tool_call_id)
                result = load_json(message.content)
                if not isinstance(result, dict):
                    result = {"result": result}
                response: dict = {"name": name, "response": result}
                if provider_id is not None:
                    response["id"] = provider_id
                part = {"functionResponse": response}
                # Parallel results belong to one user content, preserving order.
                if previous_tool:
                    contents[-1]["parts"].append(part)
                else:
                    contents.append({"role": "user", "parts": [part]})
                previous_tool = True
                continue
            previous_tool = False
            if message.role == "assistant":
                if message.metadata.get("provider") == "gemini" and "gemini_parts" in message.metadata:
                    parts = deepcopy(message.metadata["gemini_parts"])
                    for call in message.tool_calls:
                        pending[call.id] = (wire_name(call.name), message.metadata.get("gemini_call_ids", {}).get(call.id))
                else:
                    parts = [{"text": message.content}] if message.content else []
                    for call in message.tool_calls:
                        parts.append({"functionCall": {"name": wire_name(call.name), "args": call.arguments}})
                        pending[call.id] = (wire_name(call.name), None)
                if parts:
                    contents.append({"role": "model", "parts": parts})
            elif message.role == "user":
                contents.append({"role": "user", "parts": [{"text": message.content}]})
            else:
                raise ProviderError("The conversation contains an unsupported message role.")
        if pending:
            raise ProviderError("The conversation has unfinished tool calls. Start a new conversation.")
        payload: dict = {"contents": contents}
        if system:
            payload["systemInstruction"] = {"parts": system}
        return payload
