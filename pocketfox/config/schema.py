"""Configuration schema using Pydantic."""

from pathlib import Path

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class WhatsAppConfig(BaseModel):
    """WhatsApp channel configuration."""

    enabled: bool = False
    bridge_url: str = "ws://localhost:3001"
    allow_from: list[str] = Field(default_factory=list)  # Allowed phone numbers


class TelegramConfig(BaseModel):
    """Telegram channel configuration."""

    enabled: bool = False
    token: str = ""  # Bot token from @BotFather
    allow_from: list[str] = Field(default_factory=list)  # Allowed user IDs or usernames
    proxy: str | None = (
        None  # HTTP/SOCKS5 proxy URL, e.g. "http://127.0.0.1:7890" or "socks5://127.0.0.1:1080"
    )


class FeishuConfig(BaseModel):
    """Feishu/Lark channel configuration using WebSocket long connection."""

    enabled: bool = False
    app_id: str = ""  # App ID from Feishu Open Platform
    app_secret: str = ""  # App Secret from Feishu Open Platform
    encrypt_key: str = ""  # Encrypt Key for event subscription (optional)
    verification_token: str = ""  # Verification Token for event subscription (optional)
    allow_from: list[str] = Field(default_factory=list)  # Allowed user open_ids


class DingTalkConfig(BaseModel):
    """DingTalk channel configuration using Stream mode."""

    enabled: bool = False
    client_id: str = ""  # AppKey
    client_secret: str = ""  # AppSecret
    allow_from: list[str] = Field(default_factory=list)  # Allowed staff_ids


class DiscordConfig(BaseModel):
    """Discord channel configuration."""

    enabled: bool = False
    token: str = ""  # Bot token from Discord Developer Portal
    allow_from: list[str] = Field(default_factory=list)  # Allowed user IDs
    gateway_url: str = "wss://gateway.discord.gg/?v=10&encoding=json"
    intents: int = 37377  # GUILDS + GUILD_MESSAGES + DIRECT_MESSAGES + MESSAGE_CONTENT


class SignalConfig(BaseModel):
    """Signal channel configuration using signal-cli-rest-api.

    Connects to signal-cli-rest-api via WebSocket for real-time message reception.
    See: https://github.com/bbernhard/signal-cli-rest-api
    """

    enabled: bool = False
    api_url: str = "http://signal:8080"  # URL to signal-cli-rest-api
    phone_number: str = ""  # Registered phone number (e.g., "+491234567890")
    allow_from: list[str] = Field(default_factory=list)  # Allowed phone numbers


class ChannelsConfig(BaseModel):
    """Configuration for chat channels."""

    whatsapp: WhatsAppConfig = Field(default_factory=WhatsAppConfig)
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    discord: DiscordConfig = Field(default_factory=DiscordConfig)
    feishu: FeishuConfig = Field(default_factory=FeishuConfig)
    dingtalk: DingTalkConfig = Field(default_factory=DingTalkConfig)
    signal: SignalConfig = Field(default_factory=SignalConfig)


class AgentDefaults(BaseModel):
    """Default agent configuration."""

    workspace: str = ""  # Empty = auto-resolved from PF_AGENT_NAME at runtime
    model: str = "anthropic/claude-sonnet-4-6"
    max_tokens: int = 8192
    temperature: float = 0.7
    max_tool_iterations: int = 20
    default_channel: str = ""  # Fallback channel for cron/heartbeat (e.g. "telegram")
    default_chat_id: str = ""  # Fallback chat ID for cron/heartbeat
    context_files: list[str] = Field(
        default_factory=lambda: ["AGENTS.md", "TOOLS.md"]
    )  # Workspace files loaded into the system prompt


class AgentsConfig(BaseModel):
    """Agent configuration."""

    defaults: AgentDefaults = Field(default_factory=AgentDefaults)


class ContextConfig(BaseModel):
    """A context defines an independent agent personality with routing rules."""

    model: str | None = None  # Override agents.defaults.model for this context
    context_files: list[str] = Field(default_factory=lambda: ["AGENTS.md", "TOOLS.md"])
    inputs: list[str] = Field(default_factory=list)
    outputs_always: list[str] = Field(default_factory=list)
    outputs_responsive: list[str] = Field(default_factory=list)
    cron: str | None = None
    cron_files: list[str] = Field(default_factory=list)


class ProviderConfig(BaseModel):
    """LLM provider configuration."""

    api_key: str = ""
    api_base: str | None = None
    extra_headers: dict[str, str] | None = None  # Custom headers (e.g. APP-Code for AiHubMix)


class ProvidersConfig(BaseModel):
    """Configuration for LLM providers.

    Primary: ``openrouter`` — routes to 300+ models via a single API key.
    Fallback: ``openai_compat`` — for vLLM/local servers or custom gateways.

    Legacy provider fields (anthropic, openai, deepseek, …) are kept for
    backward-compatible config loading but are no longer used for routing.
    If any have an api_key set, a deprecation warning is logged.
    """

    openrouter: ProviderConfig = Field(default_factory=ProviderConfig)
    openai_compat: ProviderConfig = Field(default_factory=ProviderConfig)
    groq: ProviderConfig = Field(default_factory=ProviderConfig)  # Whisper transcription

    # Legacy fields — kept so old config.toml files still load without error.
    anthropic: ProviderConfig = Field(default_factory=ProviderConfig)
    openai: ProviderConfig = Field(default_factory=ProviderConfig)
    deepseek: ProviderConfig = Field(default_factory=ProviderConfig)
    zhipu: ProviderConfig = Field(default_factory=ProviderConfig)
    dashscope: ProviderConfig = Field(default_factory=ProviderConfig)
    vllm: ProviderConfig = Field(default_factory=ProviderConfig)
    gemini: ProviderConfig = Field(default_factory=ProviderConfig)
    moonshot: ProviderConfig = Field(default_factory=ProviderConfig)
    aihubmix: ProviderConfig = Field(default_factory=ProviderConfig)


class GatewayConfig(BaseModel):
    """Gateway/server configuration."""

    host: str = "0.0.0.0"
    port: int = 18790


class WebSearchConfig(BaseModel):
    """Web search tool configuration."""

    api_key: str = ""  # Brave Search API key
    max_results: int = 5


class WebToolsConfig(BaseModel):
    """Web tools configuration."""

    search: WebSearchConfig = Field(default_factory=WebSearchConfig)


class ExecToolConfig(BaseModel):
    """Shell exec tool configuration.

    When sandbox_dir is set, commands run inside a bubblewrap (bwrap) sandbox
    with only the specified directory visible as /workspace. This prevents
    access to credentials, prompt files, and system configs.
    """

    timeout: int = 60
    sandbox_dir: str | None = None  # If set, run commands in bwrap sandbox
    sandbox_readonly_paths: list[str] = Field(default_factory=list)  # Additional read-only mounts


class VoiceToolConfig(BaseModel):
    """Voice/TTS tool configuration using ElevenLabs."""

    api_key: str = ""  # ElevenLabs API key
    default_voice_id: str = "JBFqnCBsd6RMkjVDRZzb"  # George - clear English, no strong accent
    default_stability: float = 0.5  # 0.0=creative, 0.5=natural, 1.0=robust
    default_speed: float = 1.0  # 0.6=slow, 1.0=natural, 1.2=fast


class ToolsConfig(BaseModel):
    """Tools configuration."""

    web: WebToolsConfig = Field(default_factory=WebToolsConfig)
    exec: ExecToolConfig = Field(default_factory=ExecToolConfig)
    voice: VoiceToolConfig = Field(default_factory=VoiceToolConfig)
    restrict_to_workspace: bool = False  # If true, restrict all tool access to workspace directory


class Config(BaseSettings):
    """Root configuration for pocketfox."""

    agents: AgentsConfig = Field(default_factory=AgentsConfig)
    channels: ChannelsConfig = Field(default_factory=ChannelsConfig)
    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)
    gateway: GatewayConfig = Field(default_factory=GatewayConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    contexts: dict[str, ContextConfig] = Field(default_factory=dict)

    @property
    def workspace_path(self) -> Path:
        """Get expanded workspace path, resolved from PF_AGENT_NAME if not explicitly set."""
        from pocketfox.utils.helpers import get_paths

        ws = self.agents.defaults.workspace
        if ws:
            return Path(ws).expanduser()
        return get_paths().workspace

    def resolve_contexts(self) -> dict[str, ContextConfig]:
        """Return contexts, synthesizing a default if none are configured.

        For backward compat: if no [contexts.*] sections exist, build a
        "default" context from agents.defaults.context_files with all enabled
        channels as wildcard inputs/outputs.
        """
        if self.contexts:
            return dict(self.contexts)

        # Synthesize a default context from legacy config
        enabled_channels: list[str] = []
        for name in self.channels.model_fields:
            ch = getattr(self.channels, name)
            if getattr(ch, "enabled", False):
                enabled_channels.append(name)

        wildcards = [f"{ch}:*" for ch in enabled_channels]
        defaults = self.agents.defaults

        return {
            "default": ContextConfig(
                context_files=list(defaults.context_files),
                inputs=list(wildcards),
                outputs_responsive=list(wildcards),
            )
        }

    _LEGACY_PROVIDERS = (
        "anthropic", "openai", "deepseek", "zhipu",
        "dashscope", "vllm", "gemini", "moonshot", "aihubmix",
    )

    def _match_provider(
        self, model: str | None = None
    ) -> tuple["ProviderConfig | None", str | None]:
        """Match provider config and its name. Returns (config, provider_name).

        Priority: openrouter > openai_compat > legacy fields (with warning).
        """
        if self.providers.openrouter.api_key:
            return self.providers.openrouter, "openrouter"

        if self.providers.openai_compat.api_key:
            return self.providers.openai_compat, "openai_compat"

        # Check legacy fields and warn
        for name in self._LEGACY_PROVIDERS:
            p = getattr(self.providers, name, None)
            if p and p.api_key:
                from loguru import logger
                logger.warning(
                    f"Provider '{name}' is deprecated — "
                    f"migrate to [providers.openrouter] or [providers.openai_compat]"
                )
                return p, name

        return None, None

    def get_provider(self, model: str | None = None) -> ProviderConfig | None:
        """Get matched provider config."""
        p, _ = self._match_provider(model)
        return p

    def get_provider_name(self, model: str | None = None) -> str | None:
        """Get the name of the matched provider (e.g. "openrouter", "openai_compat")."""
        _, name = self._match_provider(model)
        return name

    def get_api_key(self, model: str | None = None) -> str | None:
        """Get API key for the matched provider."""
        p = self.get_provider(model)
        return p.api_key if p else None

    def get_api_base(self, model: str | None = None) -> str | None:
        """Get API base URL for the matched provider."""
        p, _ = self._match_provider(model)
        if p and p.api_base:
            return p.api_base
        return None

    model_config = SettingsConfigDict(env_prefix="pf_", env_nested_delimiter="__")
