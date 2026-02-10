# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build & Development

```bash
# Install for development
pip install -e .

# Install from PyPI
pip install nanobot-ai

# Run tests
pytest tests/

# Lint and format
ruff check nanobot/
ruff format nanobot/

# Run a single test
pytest tests/test_tool_validation.py -k "test_name"

# Docker build
docker build -t nanobot .
```

Ruff config: line-length 100, target Python 3.11, rules: E, F, I, N, W.

## CLI Commands

```bash
nanobot onboard          # Initialize config and workspace
nanobot agent -m "..."   # Send single message
nanobot agent            # Interactive REPL mode
nanobot gateway          # Start gateway (channels + agent loop)
nanobot status           # Show config and provider status
nanobot channels login   # Link WhatsApp
nanobot channels status  # Show channel status
nanobot cron add/list/remove  # Manage scheduled tasks
```

## Architecture

**nanobot** is an ultra-lightweight personal AI assistant framework (~3,500 lines of core code). Python 3.11+, fully async.

### Message Flow

```
Channel → InboundMessage → MessageBus → AgentLoop → LLM + ToolExecution → OutboundMessage → MessageBus → Channel.send()
```

### Key Components

- **`nanobot/agent/loop.py`** — Core agent loop. Receives messages from the bus, builds context, calls LLM, executes tools iteratively (max 20 iterations), returns response.
- **`nanobot/agent/context.py`** — Assembles LLM prompts from history, memory, skills, and workspace bootstrap files (AGENTS.md, SOUL.md, USER.md, TOOLS.md, IDENTITY.md).
- **`nanobot/bus/queue.py`** — Async message bus decoupling channels from the agent. Publish/subscribe pattern with `InboundMessage` and `OutboundMessage` events.
- **`nanobot/agent/tools/`** — Tool system with abstract `Tool` base class. Tools use JSON Schema validation and OpenAI function calling format. Built-in tools: filesystem (read/write/edit/list), shell exec (with dangerous command blocking), web search/fetch, message routing, spawn (subagents), cron management.
- **`nanobot/agent/subagent.py`** — Background task spawning. Subagents get a restricted tool set (no message, no spawn) and announce results back via system message.
- **`nanobot/providers/registry.py`** — Single source of truth for LLM providers. Adding a provider: (1) add `ProviderSpec` to the `PROVIDERS` tuple, (2) add field to `ProvidersConfig` in `config/schema.py`. 13+ providers supported via LiteLLM.
- **`nanobot/channels/`** — Chat platform integrations (Telegram, Discord, WhatsApp, Feishu, DingTalk, Signal). Each extends `BaseChannel` ABC with `start()`, `stop()`, `send()`, `is_allowed()`.
- **`nanobot/config/schema.py`** — Pydantic-based config. Root: `Config` with `agents`, `channels`, `providers`, `gateway`, `tools` sections. Stored at `~/.nanobot/config.json`.
- **`nanobot/session/manager.py`** — Conversation persistence in JSONL format at `~/.nanobot/sessions/`.
- **`nanobot/agent/memory.py`** — Daily notes (`YYYY-MM-DD.md`) and long-term memory (`MEMORY.md`) at `~/.nanobot/workspace/memory/`.
- **`nanobot/cron/`** — Persistent scheduled tasks with croniter. Jobs stored in `~/.nanobot/cron/jobs.json`.
- **`nanobot/heartbeat/`** — 30-minute periodic check that reads `HEARTBEAT.md`, parses checklist items, spawns subagents for incomplete tasks.
- **`nanobot/skills/`** — Extensible skill system. Skills are Markdown files (`SKILL.md`) loaded from workspace or built-in paths. Built-in: github, weather, tmux, cron, skill-creator, summarize.
- **`bridge/`** — WhatsApp Baileys bridge (Node.js/TypeScript), separate from the Python codebase.

### Key Patterns

- **Async-first**: Pure async I/O throughout, asyncio task management.
- **Registry-driven providers**: Declarative `ProviderSpec` tuples with automatic env var handling, model prefixing, and per-model parameter overrides.
- **Tool ABC**: All tools implement `name()`, `description()`, `parameters()` (JSON Schema), `execute()`. Registered via tool registry.
- **Workspace bootstrap**: Agent context assembled from Markdown files in `~/.nanobot/workspace/` — personality, instructions, user info, and tool docs.
