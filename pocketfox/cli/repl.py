"""Python REPL for pocketfox development and debugging."""

import code
import sys
from pathlib import Path
from typing import Any

from loguru import logger
from rich.console import Console

console = Console()


class PocketfoxNamespace:
    """
    Convenience namespace for REPL development.

    Provides easy access to pocketfox internals without needing
    to import everything manually.
    """

    def __init__(self, workspace: Path | None = None, dry_run: bool = True):
        self._workspace = workspace
        self._dry_run = dry_run
        self._agent: Any = None
        self._context: Any = None
        self._tools: Any = None
        self._provider: Any = None
        self._config: Any = None

    @property
    def workspace(self) -> Path:
        """Get the workspace path."""
        if self._workspace is None:
            from pocketfox.config.loader import load_config

            self._config = load_config()
            self._workspace = self._config.workspace_path
        return self._workspace

    @property
    def config(self):
        """Get the loaded config."""
        if self._config is None:
            from pocketfox.config.loader import load_config

            self._config = load_config()
        return self._config

    @property
    def context(self):
        """Get a ContextBuilder instance."""
        if self._context is None:
            from pocketfox.agent.context import ContextBuilder

            self._context = ContextBuilder(self.workspace)
        return self._context

    @property
    def tools(self):
        """Get a ToolRegistry instance with default tools."""
        if self._tools is None:
            from pocketfox.agent.tools.filesystem import (
                EditFileTool,
                ListDirTool,
                ReadFileTool,
                WriteFileTool,
            )
            from pocketfox.agent.tools.registry import ToolRegistry
            from pocketfox.agent.tools.shell import ExecTool
            from pocketfox.agent.tools.web import WebFetchTool, WebSearchTool

            self._tools = ToolRegistry()
            self._tools.register(ReadFileTool())
            self._tools.register(WriteFileTool())
            self._tools.register(EditFileTool())
            self._tools.register(ListDirTool())
            self._tools.register(ExecTool(working_dir=str(self.workspace)))
            self._tools.register(WebSearchTool(api_key=self.config.tools.web.search.api_key))
            self._tools.register(WebFetchTool())
        return self._tools

    @property
    def provider(self):
        """Get an LLM provider instance."""
        if self._provider is None:
            from pocketfox.providers.openrouter_provider import OpenRouterProvider

            cfg = self.config
            p = cfg.get_provider()
            self._provider = OpenRouterProvider(
                api_key=p.api_key if p else "",
                default_model=cfg.agents.defaults.model,
            )
        return self._provider

    def reload(self) -> None:
        """
        Reload pocketfox modules for hot-reloading during development.

        Note: This is a best-effort reload. Some state may persist.
        """
        import importlib

        # Find all pocketfox modules
        pocketfox_modules = [name for name in sys.modules.keys() if name.startswith("pocketfox")]

        # Sort by depth (reload deepest first)
        pocketfox_modules.sort(key=lambda x: x.count("."), reverse=True)

        reloaded = 0
        for name in pocketfox_modules:
            try:
                importlib.reload(sys.modules[name])
                reloaded += 1
            except Exception as e:  # noqa: BLE001
                logger.warning("Could not reload {}: {}", name, e)

        # Reset cached instances
        self._agent = None
        self._context = None
        self._tools = None
        self._provider = None

        console.print(f"[green]✓[/green] Reloaded {reloaded} modules")

    def __repr__(self) -> str:
        return (
            "PocketfoxNamespace(\n"
            f"  workspace={self.workspace},\n"
            "  .config    - loaded configuration\n"
            "  .context   - ContextBuilder instance\n"
            "  .tools     - ToolRegistry with default tools\n"
            "  .provider  - LLM provider instance\n"
            "  .reload()  - hot-reload pocketfox modules\n"
            ")"
        )


def start_repl(
    use_ipython: bool = False,
    workspace: Path | None = None,
) -> None:
    """
    Start the pocketfox Python REPL.

    Args:
        use_ipython: If True, try to use IPython if available.
        workspace: Override workspace path.
    """
    from pocketfox import __logo__, __version__

    # Create convenience namespace
    nb = PocketfoxNamespace(workspace=workspace)

    # Build the namespace for the REPL
    namespace: dict[str, Any] = {
        "nb": nb,
        "Path": Path,
    }

    # Pre-import common modules
    try:
        from pocketfox.agent.context import ContextBuilder
        from pocketfox.agent.loop import AgentLoop
        from pocketfox.agent.tools.registry import ToolRegistry
        from pocketfox.bus.events import InboundMessage, OutboundMessage
        from pocketfox.bus.queue import MessageBus
        from pocketfox.config.loader import load_config
        from pocketfox.session.manager import SessionManager

        namespace.update(
            {
                "ContextBuilder": ContextBuilder,
                "ToolRegistry": ToolRegistry,
                "AgentLoop": AgentLoop,
                "MessageBus": MessageBus,
                "InboundMessage": InboundMessage,
                "OutboundMessage": OutboundMessage,
                "SessionManager": SessionManager,
                "load_config": load_config,
            }
        )
    except ImportError as e:
        console.print(f"[yellow]Warning: Could not import some modules: {e}[/yellow]")

    # Banner
    banner = f"""
{__logo__} pocketfox REPL v{__version__}

Pre-loaded:
  nb          - PocketfoxNamespace (convenience object)
  nb.config   - loaded configuration
  nb.context  - ContextBuilder instance
  nb.tools    - ToolRegistry with default tools
  nb.provider - LLM provider
  nb.reload() - hot-reload modules

Classes:
  ContextBuilder, ToolRegistry, AgentLoop
  MessageBus, InboundMessage, OutboundMessage
  SessionManager, load_config

Type 'nb' for more info. Ctrl+D to exit.
"""

    console.print(banner)

    # Try IPython first if requested
    if use_ipython:
        try:
            from IPython import embed

            embed(user_ns=namespace, colors="neutral")
            return
        except ImportError:
            console.print("[yellow]IPython not available, using standard REPL[/yellow]")

    # Fall back to standard REPL
    code.interact(
        banner="",  # Already printed our banner
        local=namespace,
        exitmsg="Goodbye! 🐱",
    )
