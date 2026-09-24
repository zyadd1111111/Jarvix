# AI provider adapters

Jarvix uses a small `AIProvider` protocol in `jarvix.domain`. The orchestrator passes an explicit list of `Message` objects and registered `ToolSpec` schemas to `complete(messages, tools, model)`. The result is a `Completion` containing an assistant message, structured `ToolCall` objects and usage information. Providers never execute tools or retrieve local context themselves.

## Included adapters

| Provider ID | REST API | Initial model setting |
| --- | --- | --- |
| `openai` | OpenAI Chat Completions | `gpt-4.1-mini` |
| `gemini` | Gemini `generateContent` | `gemini-2.5-flash` |

The defaults are configurable starting points, not a claim about which model is best or newest. The official model pages document [GPT-4.1 mini](https://developers.openai.com/api/docs/models/gpt-4.1-mini) and [Gemini 2.5 Flash](https://ai.google.dev/gemini-api/docs/models/gemini-2.5-flash) with function calling support. Model access depends on the user's provider account. Documentation was checked September 16, 2026. This initial foundation does not make paid API calls during installation or tests.

`create_provider(provider_id, api_key)` selects an adapter. API keys come from the application's credential service; adapters do not read settings, environment files or the database. Keys are sent only in authorization headers to fixed HTTPS endpoints. The model field cannot change the destination URL.

## Tool protocol

Tool schemas are standard JSON Schema objects. Registry names such as `memory.create` are converted into stable, provider-safe names with hash suffixes. Each response is mapped back to the original registry name. Unknown functions, duplicate call IDs, malformed argument objects, duplicate JSON keys, nonfinite numbers and oversized argument payloads fail before a `Completion` is returned. The registry remains responsible for validating arguments against each tool's schema and enforcing permissions.

The OpenAI adapter uses the [Chat Completions API](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create), normalizes assistant tool calls and includes subsequent tool results by call ID. Provider storage is disabled with `store: false`. Parallel calls are disabled in the request, but the response parser can handle multiple calls safely; the orchestrator controls execution order.

The Gemini adapter follows the [generateContent REST reference](https://ai.google.dev/api/generate-content): system instructions are separate from conversation contents, function declarations use `parametersJsonSchema`, and tool results use `functionResponse`. Parallel tool results are grouped in one user content entry. Provider-generated IDs are echoed when present. Otherwise, Jarvix generates local IDs for correlation without inventing provider IDs.

Gemini response parts and their encrypted thought signatures are retained in `Message.metadata["gemini_parts"]`. Call ID mapping is retained in `gemini_call_ids`. These parts are returned unchanged to Gemini during the tool loop, as required by its [thought signature guidance](https://ai.google.dev/gemini-api/docs/generate-content/thought-signatures). Thought text is excluded from the visible assistant message. The orchestrator must retain the original completion message, including metadata. Do not reconstruct it from visible text alone during a tool cycle. OpenAI ignores Gemini metadata. Starting a fresh conversation when changing providers or models avoids carrying incompatible reasoning state between them.

## Boundaries and failure behavior

- Only explicitly supplied messages and tool definitions are serialized. Adapters do not add notes, memory, files, environment data or generic message metadata.
- Request size is capped at 512 KiB; decoded responses at 2 MiB; individual arguments at 32 KiB; tool calls per response at 16; generated output at 4,096 tokens. Exceeding a cap produces an explicit error rather than silently truncating tool arguments or history.
- A response stopped at its output-token limit is rejected even when it includes parseable tool calls. Partial plans must not execute.
- HTTP has connect/read/write/pool timeouts of 10/60/15/10 seconds. An owned client is closed after each request. Default clients ignore ambient proxy configuration. Redirects are never followed.
- Requests are not automatically retried. A retry could repeat a generation after the server accepted it, and downstream tools may write data. The user can decide whether to try again after an error.
- Errors use fixed, actionable messages for configuration, authentication, quota, transport and protocol problems. Provider error bodies, request contents, URLs with secrets and raw network exceptions are not exposed or logged.
- Provider-side retention policies still apply. Local-first storage does not make a cloud prompt local or override a provider's terms.

The initial adapters support text and structured function calls. Streaming, vision, audio, provider-hosted tools, automatic web grounding and cancellation of an in-flight HTTP request are not implemented here. Gemini's newer Interactions API is separate; it can be added as a new adapter without changing the orchestrator contract. Other vendors may need different role or tool serialization despite implementing the same protocol.

## Adding an adapter

1. Implement `id` and `complete` with no UI dependency.
2. Normalize text, calls, errors and usage to domain objects. Preserve opaque protocol state only in namespaced metadata.
3. Register its constructor and default model in `jarvix.providers`.
4. Add settings/credential support and mocked request-response tests.
5. Verify tool cycles, argument validation, authentication errors, bounded payloads and privacy before enabling it.

Tests inject an `httpx.Client` with `MockTransport`. Injection is for controlled tests and trusted hosts; callers supplying a custom client own its lifecycle and any hooks or custom retry transport. Run `python -m pytest tests/test_providers.py` from the project root. Live credential testing must be a separate, explicitly configured operation.
