<div align="center">
  <h1>pocketfox</h1>
  <img src="pocketfox-logo.svg" alt="pocketfox" width="240">
  <p><em>An ultra-lightweight personal AI assistant with composable contexts.</em></p>
</div>

**pocketfox** is a small (<10k SLoC) async Python agent framework. It connects an LLM
to chat platforms (Telegram, Discord, WhatsApp, Signal, Feishu, DingTalk), tools, and
scheduled tasks — and lets you wire each one into independent **contexts** that decide
*who* the agent is, *what* it can do, and *which channels* it listens and replies on.

## Why pocketfox

### Composable contexts
A context is a self-contained agent personality assembled from Markdown files in your
workspace. Each context picks its own files, model, prologue, and tool whitelist:

```toml
[contexts.work]
model        = "anthropic/claude-sonnet-4-6"
prologue     = "You are a focused work assistant. Be terse."
context_files = ["AGENTS.md", "TOOLS.md", "WORK.md", "MEMORY.md"]
allowed_tools = ["fs_*", "web_*", "message_*"]

[contexts.home]
prologue     = "You are warm, casual, and remember family stuff."
context_files = ["AGENTS.md", "SOUL.md", "USER.md", "MEMORY.md"]
```

Files like `AGENTS.md`, `SOUL.md`, `USER.md`, `TOOLS.md`, and `MEMORY.md` are loaded
verbatim into the system prompt via [LOOM](https://github.com/outfox/loom), with cache
breakpoints placed for cheap Anthropic prefix caching. Swap files, swap personality.

### Context routing across channels
A context can be fed from one or many channels at once, and reply to a different set:

```toml
[contexts.work]
inputs              = ["telegram:123", "discord:*", "signal:+15551234567"]
outputs_responsive  = ["telegram:*", "discord:*"]   # reply where the message came from
outputs_always      = ["telegram:999"]              # also mirror to my phone
cron                = "0 9 * * 1-5"                 # weekday morning standup
cron_files          = ["STANDUP.md"]                # extra files loaded only for cron runs
```

Routing rules:
- `inputs` — `channel:chat_id` (exact) or `channel:*` (any chat on that channel)
- `outputs_responsive` — only fire when triggered by an inbound message
- `outputs_always` — fire for every turn, including cron and heartbeat
- One inbound message can match several contexts; each runs independently

### Other things it does
- **Multi-modal** — images, audio, video, and documents (PDF, docx) attached on chat
  platforms flow into the LLM as native content blocks
- **Subagents** — agents can spawn background subagents with restricted toolsets
- **Skills** — drop a `SKILL.md` into the workspace and it becomes available; built-ins
  cover GitHub, weather, tmux, cron, and summarization
- **Cron + heartbeat** — persistent scheduled jobs and a 30-minute proactive checklist
- **300+ models** via OpenRouter, plus direct providers (Anthropic, OpenAI, DeepSeek,
  Groq, Gemini, Moonshot, Zhipu, DashScope) and any OpenAI-compatible local server (vLLM)

## Install

```bash
pip install pocketfox-ai
# or, from source:
git clone https://github.com/outfox/pocketfox.git && cd pocketfox && pip install -e .
```

## Quick start

```bash
pocketfox onboard                        # initialize ~/.pocketfox/
$EDITOR ~/.pocketfox/config.toml         # add an API key
pocketfox agent -m "what's 2 + 2?"       # one-shot
pocketfox agent                          # REPL
pocketfox gateway                        # run channels + agent loop
```

Minimal config:

```toml
[providers.openrouter]
api_key = "sk-or-v1-..."

[agents.defaults]
model = "anthropic/claude-sonnet-4-6"

[channels.telegram]
enabled    = true
token      = "BOT_TOKEN_FROM_BOTFATHER"
allow_from = ["YOUR_USER_ID"]
```

If no `[contexts.*]` section is defined, pocketfox synthesizes a default context that
listens on every enabled channel — so the simple case stays simple.

## CLI

| Command | What it does |
|---------|--------------|
| `pocketfox onboard`         | Initialize config and workspace |
| `pocketfox agent [-m ...]`  | Single message or interactive REPL |
| `pocketfox gateway`         | Start channels + agent loop |
| `pocketfox status`          | Show config and provider status |
| `pocketfox channels login`  | Link WhatsApp (scan QR) |
| `pocketfox cron add/list/remove` | Manage scheduled tasks |

## Layout

```
pocketfox/
├── agent/      # loop, context builder (LOOM), router, tools, subagents
├── channels/   # Telegram, Discord, WhatsApp, Signal, Feishu, DingTalk
├── providers/  # OpenRouter + direct LLM providers
├── bus/        # async message bus (decouples channels from the agent)
├── cron/       # persistent scheduled tasks (croniter)
├── heartbeat/  # proactive 30-minute checklist runner
├── skills/     # bundled skills (github, weather, tmux, cron, summarize)
├── session/    # JSONL conversation persistence
└── cli/        # commands
```

Workspace lives at `~/.pocketfox/`: config, sessions, logs, and the Markdown files
that get composed into your contexts.

## Security

For anything beyond local play, set `tools.restrict_to_workspace = true` to sandbox
file/shell tools, and populate `allow_from` on every enabled channel. See `SECURITY.md`.

## License

MIT.
