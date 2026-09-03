# 2. Technology Stack

## 2.1 Chosen stack

| Layer | Choice | Why this, not the alternative |
|---|---|---|
| Core language | **Python 3.11+**, fully typed, async | The agent/ML ecosystem lives here; `asyncio` fits an I/O-bound orchestrator |
| API | **FastAPI** + Pydantic v2 | Schema-first, native async, WS support, free OpenAPI |
| Realtime | **WebSocket** (`/realtime`, `/device`) | Bidirectional, survives NAT; device nodes dial *out* |
| Durable state | **PostgreSQL 16** via SQLAlchemy 2 (async) + Alembic | Relational integrity for tasks/permissions/audit; JSONB where shape varies |
| Vector search | **pgvector** | One datastore, transactional with the row it describes. A separate vector DB would let memory and its metadata drift |
| Cache / queue / pubsub | **Redis 7** (Streams for the bus, lists for the queue) | Already needed for presence + rate limits |
| Dev/test datastore | **SQLite + aiosqlite**, brute-force vector scan | The whole system must boot with zero infrastructure, or nobody runs the tests |
| Agent layer | **In-house orchestrator** over a thin `AgentProvider` port | LangGraph/OpenAI Agents SDK are adapters, not the spine. The supervisor/permission/verify loop is the product; it must not be a framework's opinion |
| LLM | `LLMProvider` port → Anthropic / OpenAI / **Ollama** / rule-based | Model Router (§33) needs ≥2 live tiers plus an offline one |
| STT | `STTProvider` port → faster-whisper (local) / cloud | Local-first for `HIGHLY_PRIVATE` audio |
| TTS | `TTSProvider` port → Piper (local) / cloud neural | Offline mode must still speak |
| Wake word | openWakeWord / Porcupine behind `WakeWordProvider` | Runs on-device; no audio leaves until wake |
| Vision | OpenCV + MediaPipe (hands), ONNX detector, Tesseract/PaddleOCR | Local, no cloud round-trip per frame |
| Desktop | **Tauri 2 + React + TypeScript** | ~10 MB binary, real OS access for the node, no Electron RAM tax |
| Mobile | **Flutter** | One codebase for the remote/approval surface; good background audio |
| Device node | Python 3.11 + per-OS adapter (`pywin32`/UIA, AppleScript, `xdotool`/DBus) | Same protocol everywhere; OS specifics isolated in one file |
| Secrets | OS keychain (DPAPI/Keychain/libsecret) → `SecretVault` port | Never in DB, prompt, note, or vector store |
| Observability | `structlog` JSON + OpenTelemetry spans keyed by `trace_id/task_id/agent_id` | Multi-agent debugging is impossible without correlation |
| Tests | pytest + pytest-asyncio, fakes for every port | Ports have fakes so CI needs no GPU, no cloud, no Postgres |

## 2.2 Version pins and runtime floor

Python ≥3.11 (for `asyncio.TaskGroup`, `StrEnum`, `Self`). Node ≥20 for the desktop app.
Postgres ≥16 for pgvector 0.7 HNSW. All Python deps pinned by range in `pyproject.toml`;
the lock is generated with `uv`.

## 2.3 What is deliberately *not* in the stack

- **No vendor agent framework in core.** Adapters only.
- **No LangChain-style global chains.** Explicit DI container, explicit ports.
- **No always-on cloud dependency.** Offline mode (§58) is a first-class configuration:
  `RuleBasedLLM` + local STT/TTS + SQLite + local device node is a complete, working
  Thursday, just a less clever one.
- **No coordinate-clicking as a primary automation path** (§19). GUI automation is the
  last of five tiers and is marked `risk=HIGH` in the tool registry.
