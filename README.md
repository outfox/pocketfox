<div align="center">
  <h1>pocketfox: Ultra-Lightweight Personal AI Assistant</h1>
  <img src="pocketfox-logo.svg" alt="pocketfox" width="300">
  <p>
    <a href="https://pypi.org/project/pocketfox-ai/"><img src="https://img.shields.io/pypi/v/pocketfox-ai" alt="PyPI"></a>
    <a href="https://pepy.tech/project/pocketfox-ai"><img src="https://static.pepy.tech/badge/pocketfox-ai" alt="Downloads"></a>
    <img src="https://img.shields.io/badge/python-≥3.11-blue" alt="Python">
    <img src="https://img.shields.io/badge/license-MIT-green" alt="License">
   <a href="https://discord.gg/MnCvHqpUGB"><img src="https://img.shields.io/badge/Discord-Community-5865F2?style=flat&logo=discord&logoColor=white" alt="Discord"></a>
  </p>
</div>

👖🦊 **pocketfox** is an **ultra-lightweight** personal AI assistant inspired by [Clawdbot](https://github.com/openclaw/openclaw) 

⚡️ Delivers core agent functionality in just **~4,000** lines of code — **99% smaller** than Clawdbot's 430k+ lines.

📏 Real-time line count: **3,448 lines** (run `bash core_agent_lines.sh` to verify anytime)

## 📢 News

- **2026-02-17** 🔧 Official Fork named pocketfox.

## Key Features of pocketfox:

🪶 **Ultra-Lightweight**: Just ~4,000 lines of core agent code — 99% smaller than Clawdbot.

🔬 **Research-Ready**: Clean, readable code that's easy to understand, modify, and extend for research.

⚡️ **Lightning Fast**: Minimal footprint means faster startup, lower resource usage, and quicker iterations.

💎 **Easy-to-Use**: One-click to deploy and you're ready to go.

## 🏗️ Architecture

<p align="center">
  <img src="pocketfox_arch.png" alt="pocketfox architecture" width="800">
</p>

## 📦 Install

**Install from source** (latest features, recommended for development)

```bash
git clone https://github.com/outfox/pocketfox.git
cd pocketfox
pip install -e .
```

**Install with [uv](https://github.com/astral-sh/uv)** (stable, fast)

```bash
uv tool install pocketfox-ai
```

**Install from PyPI** (stable)

```bash
pip install pocketfox-ai
```

## 🚀 Quick Start

> [!TIP]
> Set your API key in `~/.pocketfox/config.toml`.
> Get API keys: [OpenRouter](https://openrouter.ai/keys) (Global) · [DashScope](https://dashscope.console.aliyun.com) (Qwen) · [Brave Search](https://brave.com/search/api/) (optional, for web search)

**1. Initialize**

```bash
pocketfox onboard
```

**2. Configure** (`~/.pocketfox/config.toml`)

For OpenRouter - recommended for global users:
```toml
[providers.openrouter]
api_key = "sk-or-v1-xxx"

[agents.defaults]
model = "anthropic/claude-opus-4-5"
```

**3. Chat**

```bash
pocketfox agent -m "What is 2+2?"
```

That's it! You have a working AI assistant in 2 minutes.

## 🖥️ Local Models (vLLM)

Run pocketfox with your own local models using vLLM or any OpenAI-compatible server.

**1. Start your vLLM server**

```bash
vllm serve meta-llama/Llama-3.1-8B-Instruct --port 8000
```

**2. Configure** (`~/.pocketfox/config.toml`)

```toml
[providers.vllm]
api_key = "dummy"
api_base = "http://localhost:8000/v1"

[agents.defaults]
model = "meta-llama/Llama-3.1-8B-Instruct"
```

**3. Chat**

```bash
pocketfox agent -m "Hello from my local LLM!"
```

> [!TIP]
> The `api_key` can be any non-empty string for local servers that don't require authentication.

## 💬 Chat Apps

Talk to your pocketfox through Telegram, Discord, WhatsApp, or Feishu — anytime, anywhere.

| Channel | Setup |
|---------|-------|
| **Telegram** | Easy (just a token) |
| **Discord** | Easy (bot token + intents) |
| **WhatsApp** | Medium (scan QR) |
| **Feishu** | Medium (app credentials) |
| **Signal** | Medium (requires signal-cli-rest-api) |

<details>
<summary><b>Telegram</b> (Recommended)</summary>

**1. Create a bot**
- Open Telegram, search `@BotFather`
- Send `/newbot`, follow prompts
- Copy the token

**2. Configure**

```toml
[channels.telegram]
enabled = true
token = "YOUR_BOT_TOKEN"
allow_from = ["YOUR_USER_ID"]
```

> Get your user ID from `@userinfobot` on Telegram.

**3. Run**

```bash
pocketfox gateway
```

</details>

<details>
<summary><b>Discord</b></summary>

**1. Create a bot**
- Go to https://discord.com/developers/applications
- Create an application → Bot → Add Bot
- Copy the bot token

**2. Enable intents**
- In the Bot settings, enable **MESSAGE CONTENT INTENT**
- (Optional) Enable **SERVER MEMBERS INTENT** if you plan to use allow lists based on member data

**3. Get your User ID**
- Discord Settings → Advanced → enable **Developer Mode**
- Right-click your avatar → **Copy User ID**

**4. Configure**

```toml
[channels.discord]
enabled = true
token = "YOUR_BOT_TOKEN"
allow_from = ["YOUR_USER_ID"]
```

**5. Invite the bot**
- OAuth2 → URL Generator
- Scopes: `bot`
- Bot Permissions: `Send Messages`, `Read Message History`
- Open the generated invite URL and add the bot to your server

**6. Run**

```bash
pocketfox gateway
```

</details>

<details>
<summary><b>WhatsApp</b></summary>

Requires **Node.js ≥18**.

**1. Link device**

```bash
pocketfox channels login
# Scan QR with WhatsApp → Settings → Linked Devices
```

**2. Configure**

```toml
[channels.whatsapp]
enabled = true
allow_from = ["+1234567890"]
```

**3. Run** (two terminals)

```bash
# Terminal 1
pocketfox channels login

# Terminal 2
pocketfox gateway
```

</details>

<details>
<summary><b>Feishu (飞书)</b></summary>

Uses **WebSocket** long connection — no public IP required.

**1. Create a Feishu bot**
- Visit [Feishu Open Platform](https://open.feishu.cn/app)
- Create a new app → Enable **Bot** capability
- **Permissions**: Add `im:message` (send messages)
- **Events**: Add `im.message.receive_v1` (receive messages)
  - Select **Long Connection** mode (requires running pocketfox first to establish connection)
- Get **App ID** and **App Secret** from "Credentials & Basic Info"
- Publish the app

**2. Configure**

```toml
[channels.feishu]
enabled = true
app_id = "cli_xxx"
app_secret = "xxx"
encrypt_key = ""
verification_token = ""
allow_from = []
```

> `encrypt_key` and `verification_token` are optional for Long Connection mode.
> `allow_from`: Leave empty to allow all users, or add `["ou_xxx"]` to restrict access.

**3. Run**

```bash
pocketfox gateway
```

> [!TIP]
> Feishu uses WebSocket to receive messages — no webhook or public IP needed!

</details>

<details>
<summary><b>DingTalk (钉钉)</b></summary>

Uses **Stream Mode** — no public IP required.

**1. Create a DingTalk bot**
- Visit [DingTalk Open Platform](https://open-dev.dingtalk.com/)
- Create a new app -> Add **Robot** capability
- **Configuration**:
  - Toggle **Stream Mode** ON
- **Permissions**: Add necessary permissions for sending messages
- Get **AppKey** (Client ID) and **AppSecret** (Client Secret) from "Credentials"
- Publish the app

**2. Configure**

```toml
[channels.dingtalk]
enabled = true
client_id = "YOUR_APP_KEY"
client_secret = "YOUR_APP_SECRET"
allow_from = []
```

> `allow_from`: Leave empty to allow all users, or add `["staffId"]` to restrict access.

**3. Run**

```bash
pocketfox gateway
```

</details>

<details>
<summary><b>Signal</b></summary>

Uses **signal-cli-rest-api** — requires a separate container or service.

**1. Set up signal-cli-rest-api**

Add to your `docker-compose.yml`:
```yaml
services:
  signal:
    image: bbernhard/signal-cli-rest-api:0.97
    environment:
      - MODE=native
    volumes:
      - ./signal-data:/home/.local/share/signal-cli
    ports:
      - "8080:8080"
```

**2. Register your phone number**

```bash
# Request verification code
curl -X POST "http://localhost:8080/v1/register/+491234567890"

# Verify with the code you receive via SMS
curl -X POST "http://localhost:8080/v1/register/+491234567890/verify/123456"
```

**3. Configure**

```toml
[channels.signal]
enabled = true
api_url = "http://signal:8080"
phone_number = "+491234567890"
allow_from = ["+491111111111", "+492222222222"]
```

> `allow_from`: List of phone numbers that can message the bot. Leave empty to allow everyone.

**4. Run**

```bash
pocketfox gateway
```

> [!TIP]
> Signal gives you a real phone number presence — not a bot account. Great for privacy-focused users!

</details>

## ⚙️ Configuration

Config file: `~/.pocketfox/config.toml`

### Providers

> [!NOTE]
> Groq provides free voice transcription via Whisper. If configured, Telegram voice messages will be automatically transcribed.

| Provider | Purpose | Get API Key |
|----------|---------|-------------|
| `openrouter` | LLM (recommended, access to all models) | [openrouter.ai](https://openrouter.ai) |
| `anthropic` | LLM (Claude direct) | [console.anthropic.com](https://console.anthropic.com) |
| `openai` | LLM (GPT direct) | [platform.openai.com](https://platform.openai.com) |
| `deepseek` | LLM (DeepSeek direct) | [platform.deepseek.com](https://platform.deepseek.com) |
| `groq` | LLM + **Voice transcription** (Whisper) | [console.groq.com](https://console.groq.com) |
| `gemini` | LLM (Gemini direct) | [aistudio.google.com](https://aistudio.google.com) |
| `aihubmix` | LLM (API gateway, access to all models) | [aihubmix.com](https://aihubmix.com) |
| `dashscope` | LLM (Qwen) | [dashscope.console.aliyun.com](https://dashscope.console.aliyun.com) |
| `moonshot` | LLM (Moonshot/Kimi) | [platform.moonshot.cn](https://platform.moonshot.cn) |
| `zhipu` | LLM (Zhipu GLM) | [open.bigmodel.cn](https://open.bigmodel.cn) |
| `vllm` | LLM (local, any OpenAI-compatible server) | — |

<details>
<summary><b>Provider Architecture (Developer Guide)</b></summary>

All models are accessed through **OpenRouter** (`pocketfox/providers/openrouter_provider.py`) — a single API key routes to 300+ models across Anthropic, OpenAI, DeepSeek, Gemini, and more.

```toml
[providers.openrouter]
api_key = "sk-or-v1-..."
```

Models use the `provider/model-name` format (e.g. `anthropic/claude-sonnet-4-6`, `deepseek/deepseek-chat`).

</details>


### Security

> [!TIP]
> For production deployments, set `restrict_to_workspace = true` in your config to sandbox the agent.

| Option | Default | Description |
|--------|---------|-------------|
| `tools.restrict_to_workspace` | `false` | When `true`, restricts **all** agent tools (shell, file read/write/edit, list) to the workspace directory. Prevents path traversal and out-of-scope access. |
| `channels.*.allow_from` | `[]` (allow all) | Whitelist of user IDs. Empty = allow everyone; non-empty = only listed users can interact. |


## CLI Reference

| Command | Description |
|---------|-------------|
| `pocketfox onboard` | Initialize config & workspace |
| `pocketfox agent -m "..."` | Chat with the agent |
| `pocketfox agent` | Interactive chat mode |
| `pocketfox gateway` | Start the gateway |
| `pocketfox status` | Show status |
| `pocketfox channels login` | Link WhatsApp (scan QR) |
| `pocketfox channels status` | Show channel status |

<details>
<summary><b>Scheduled Tasks (Cron)</b></summary>

```bash
# Add a job
pocketfox cron add --name "daily" --message "Good morning!" --cron "0 9 * * *"
pocketfox cron add --name "hourly" --message "Check status" --every 3600

# List jobs
pocketfox cron list

# Remove a job
pocketfox cron remove <job_id>
```

</details>

## 🐳 Docker

> [!TIP]
> The `-v ~/.pocketfox:/root/.pocketfox` flag mounts your local config directory into the container, so your config and workspace persist across container restarts.

Build and run pocketfox in a container:

```bash
# Build the image
docker build -t pocketfox .

# Initialize config (first time only)
docker run -v ~/.pocketfox:/root/.pocketfox --rm pocketfox onboard

# Edit config on host to add API keys
vim ~/.pocketfox/config.toml

# Run gateway (connects to Telegram/WhatsApp)
docker run -v ~/.pocketfox:/root/.pocketfox -p 18790:18790 pocketfox gateway

# Or run a single command
docker run -v ~/.pocketfox:/root/.pocketfox --rm pocketfox agent -m "Hello!"
docker run -v ~/.pocketfox:/root/.pocketfox --rm pocketfox status
```

## 📁 Project Structure

```
pocketfox/
├── agent/          # 🧠 Core agent logic
│   ├── loop.py     #    Agent loop (LLM ↔ tool execution)
│   ├── context.py  #    Prompt builder
│   ├── memory.py   #    Persistent memory
│   ├── skills.py   #    Skills loader
│   ├── subagent.py #    Background task execution
│   └── tools/      #    Built-in tools (incl. spawn)
├── skills/         # 🎯 Bundled skills (github, weather, tmux...)
├── channels/       # 📱 WhatsApp integration
├── bus/            # 🚌 Message routing
├── cron/           # ⏰ Scheduled tasks
├── heartbeat/      # 💓 Proactive wake-up
├── providers/      # 🤖 LLM providers (OpenRouter, etc.)
├── session/        # 💬 Conversation sessions
├── config/         # ⚙️ Configuration
└── cli/            # 🖥️ Commands
```

## 🤝 Contribute & Roadmap

PRs welcome! The codebase is intentionally small and readable. 🤗

**Roadmap** — Pick an item and [open a PR](https://github.com/outfox/pocketfox/pulls)!

- [x] **Voice Transcription** — Support for Groq Whisper (Issue #13)
- [ ] **Multi-modal** — See and hear (images, voice, video)
- [ ] **Long-term memory** — Never forget important context
- [ ] **Better reasoning** — Multi-step planning and reflection
- [ ] **More integrations** — Discord, Slack, email, calendar
- [ ] **Self-improvement** — Learn from feedback and mistakes

### Contributors

<a href="https://github.com/outfox/pocketfox/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=outfox/pocketfox&max=100&columns=12" />
</a>


## ⭐ Star History

<div align="center">
  <a href="https://star-history.com/#outfox/pocketfox&Date">
    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=outfox/pocketfox&type=Date&theme=dark" />
      <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=outfox/pocketfox&type=Date" />
      <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=outfox/pocketfox&type=Date" style="border-radius: 15px; box-shadow: 0 0 30px rgba(0, 217, 255, 0.3);" />
    </picture>
  </a>
</div>

<p align="center">
  <em> Thanks for visiting ✨ pocketfox!</em><br><br>
  <img src="https://visitor-badge.laobi.icu/badge?page_id=outfox.pocketfox&style=for-the-badge&color=00d4ff" alt="Views">
</p>


<p align="center">
  <sub>pocketfox is for educational, research, and technical exchange purposes only</sub>
</p>
