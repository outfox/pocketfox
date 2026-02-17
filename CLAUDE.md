# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build & Development

```bash
# Install for development (creates editable install)
pip install -e .

# Install with dev dependencies
pip install -e ".[dev]"

# Install from PyPI
pip install pocketfox-ai

# Run all tests
pytest tests/

# Run specific test file
pytest tests/test_tool_validation.py

# Run specific test by name pattern
pytest tests/test_tool_validation.py -k "test_name"

# Lint and format
ruff check pocketfox/
ruff format pocketfox/

# Count core agent lines (should be ~3,500)
bash core_agent_lines.sh

# Docker build and test
docker build -t pocketfox .
bash tests/test_docker.sh
```

**Ruff config**: line-length 100, target Python 3.11, rules: E, F, I, N, W (E501 ignored).
**Test framework**: pytest with asyncio_mode=auto for async tests.

## CLI Commands

```bash
pocketfox onboard          # Initialize config and workspace
pocketfox agent -m "..."   # Send single message
pocketfox agent            # Interactive REPL mode
pocketfox gateway          # Start gateway (channels + agent loop)
pocketfox status           # Show config and provider status
pocketfox channels login   # Link WhatsApp
pocketfox channels status  # Show channel status
pocketfox cron add/list/remove  # Manage scheduled tasks
```

## Architecture

**pocketfox** is an ultra-lightweight personal AI assistant framework (~3,500 lines of core code). Python 3.11+, fully async.

### Message Flow

```
Channel → InboundMessage → MessageBus → AgentLoop → LLM + ToolExecution → OutboundMessage → MessageBus → Channel.send()
```

### Key Components

- **`pocketfox/agent/loop.py`** — Core agent loop. Receives messages from the bus, builds context, calls LLM, executes tools iteratively (max 20 iterations), returns response.
- **`pocketfox/agent/context.py`** — Assembles LLM prompts from history, memory, skills, and workspace bootstrap files (AGENTS.md, SOUL.md, USER.md, TOOLS.md, IDENTITY.md).
- **`pocketfox/bus/queue.py`** — Async message bus decoupling channels from the agent. Publish/subscribe pattern with `InboundMessage` and `OutboundMessage` events.
- **`pocketfox/agent/tools/`** — Tool system with abstract `Tool` base class. Tools use JSON Schema validation and OpenAI function calling format. Built-in tools: filesystem (read/write/edit/list), shell exec (with dangerous command blocking), web search/fetch, message routing, spawn (subagents), cron management.
- **`pocketfox/agent/subagent.py`** — Background task spawning. Subagents get a restricted tool set (no message, no spawn) and announce results back via system message.
- **`pocketfox/providers/registry.py`** — Single source of truth for LLM providers. Adding a provider: (1) add `ProviderSpec` to the `PROVIDERS` tuple, (2) add field to `ProvidersConfig` in `config/schema.py`. 13+ providers supported via LiteLLM.
- **`pocketfox/channels/`** — Chat platform integrations (Telegram, Discord, WhatsApp, Feishu, DingTalk, Signal). Each extends `BaseChannel` ABC with `start()`, `stop()`, `send()`, `is_allowed()`.
- **`pocketfox/config/schema.py`** — Pydantic-based config. Root: `Config` with `agents`, `channels`, `providers`, `gateway`, `tools` sections. Stored at `~/.pocketfox/config.toml`.
- **`pocketfox/session/manager.py`** — Conversation persistence in JSONL format at `~/.pocketfox/sessions/`.
- **`pocketfox/agent/memory.py`** — Daily notes (`YYYY-MM-DD.md`) and long-term memory (`MEMORY.md`) at `~/.pocketfox/workspace/memory/`.
- **`pocketfox/cron/`** — Persistent scheduled tasks with croniter. Jobs stored in `~/.pocketfox/cron/jobs.json`.
- **`pocketfox/heartbeat/`** — 30-minute periodic check that reads `HEARTBEAT.md`, parses checklist items, spawns subagents for incomplete tasks.
- **`pocketfox/skills/`** — Extensible skill system. Skills are Markdown files (`SKILL.md`) with YAML frontmatter (name, description, metadata) followed by agent instructions. Loaded from `~/.pocketfox/workspace/skills/` or built-in `pocketfox/skills/`. Built-in: github, weather, tmux, cron, skill-creator, summarize.
- **`bridge/`** — WhatsApp Baileys bridge (Node.js/TypeScript), separate from the Python codebase. HTTP API on `localhost:3001` with endpoints for session management and message sending.

### Key Patterns

- **Async-first**: Pure async I/O throughout, asyncio task management.
- **Registry-driven providers**: Declarative `ProviderSpec` tuples with automatic env var handling, model prefixing, and per-model parameter overrides.
- **Tool ABC**: All tools implement `name()`, `description()`, `parameters()` (JSON Schema), `execute()`. Registered via tool registry.
- **Workspace bootstrap**: Agent context assembled from Markdown files in `~/.pocketfox/workspace/` — personality, instructions, user info, and tool docs (AGENTS.md, SOUL.md, USER.md, TOOLS.md, IDENTITY.md).
- **Security sandboxing**: `tools.restrict_to_workspace` config option restricts all file/shell tools to workspace directory (path traversal protection).
- **Message bus decoupling**: Channels publish/subscribe to `InboundMessage`/`OutboundMessage` events instead of directly calling agent code.

## Security & Best Practices

**Critical security settings:**
- `tools.restrict_to_workspace: true` — Sandbox all file/shell operations to workspace directory (IMPORTANT for production)
- `channels.*.allow_from` — Whitelist user IDs (empty = allow all; production should restrict)
- Shell tool blocks dangerous patterns: `rm -rf /`, fork bombs, `mkfs.*`, raw disk writes
- Path traversal protection in filesystem tools
- Config file at `~/.pocketfox/config.toml` should be `chmod 600`

**For production deployments:**
- Run as dedicated non-root user
- Set `restrictToWorkspace: true`
- Configure `allowFrom` lists for all channels
- Use separate API keys with spending limits
- Monitor logs at `~/.pocketfox/logs/`
- Keep dependencies updated (especially `litellm`)

See `SECURITY.md` for full security checklist and incident response procedures.

## Adding New Features

### Adding a New LLM Provider

The provider system uses a registry pattern for minimal code changes:

1. Add `ProviderSpec` to `PROVIDERS` tuple in `pocketfox/providers/registry.py`:
   ```python
   ProviderSpec(
       name="myprovider",
       keywords=("myprovider", "mymodel"),
       env_key="MYPROVIDER_API_KEY",
       display_name="My Provider",
       litellm_prefix="myprovider",
       skip_prefixes=("myprovider/",),
   )
   ```

2. Add field to `ProvidersConfig` in `pocketfox/config/schema.py`:
   ```python
   class ProvidersConfig(BaseModel):
       myprovider: ProviderConfig = ProviderConfig()
   ```

That's it! Environment variables, model prefixing, config matching, and `pocketfox status` display all work automatically.

### Adding a New Tool

1. Create class extending `Tool` in `pocketfox/agent/tools/`
2. Implement: `name`, `description`, `parameters` (JSON Schema), `execute()`
3. Register in `pocketfox/agent/tools/__init__.py`
4. Tool automatically appears in LLM function calling

### Adding a New Channel

1. Create class extending `BaseChannel` in `pocketfox/channels/`
2. Implement: `start()`, `stop()`, `send()`, `is_allowed()`
3. Add config schema to `ChannelsConfig` in `pocketfox/config/schema.py`
4. Register in `pocketfox/channels/__init__.py`

### Adding a New Skill

Skills are Markdown files with YAML frontmatter:
```markdown
---
name: my-skill
description: Does something useful
---

Instructions for the agent when this skill is loaded...
```

Place in `~/.pocketfox/workspace/skills/my-skill/SKILL.md` or create via `skill-creator` skill.

## Debugging & Development Tips

**Logs**: Check `~/.pocketfox/logs/` for debug output
**Config**: Located at `~/.pocketfox/config.toml` (Pydantic validation errors show which fields are invalid)
**Sessions**: JSONL files in `~/.pocketfox/sessions/` — each message is one JSON line
**Agent loop**: Max 20 iterations before timeout (see `loop.py`)

**Common patterns:**
- Provider auto-detection: Based on API key prefix (`sk-or-` → OpenRouter) or `apiBase` URL keywords
- Model prefixing: Automatic (e.g., `qwen-max` → `dashscope/qwen-max`) unless `skip_prefixes` matches
- WhatsApp bridge: Requires Node.js ≥18, runs separate HTTP server on `localhost:3001`
- Groq for transcription: If Groq provider configured, Telegram voice messages auto-transcribe via Whisper

**Code style:**
- Use `loguru` for logging (not `print()`)
- Async-first: Use `async`/`await`, no blocking calls
- Type hints: Use Python 3.11+ syntax (`dict[str, Any]`, not `Dict[str, Any]`)
- Error handling: Raise exceptions with clear messages, catch at appropriate boundaries
