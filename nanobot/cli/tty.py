"""TTY interface for interactive nanobot development and debugging."""

import asyncio
import json
from pathlib import Path
from typing import Callable

from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text

console = Console()


class TTYAgent:
    """
    Interactive TTY agent for development and debugging.
    
    Features:
    - Verbose mode: shows tool calls and results
    - Dry-run mode: shows what would happen without executing
    - Breakpoints: pause before tool execution
    """
    
    def __init__(
        self,
        workspace: Path,
        model: str | None = None,
        verbose: bool = True,
        dry_run: bool = False,
        breakpoints: bool = False,
    ):
        self.workspace = workspace
        self.model = model
        self.verbose = verbose
        self.dry_run = dry_run
        self.breakpoints = breakpoints
        
        self._provider = None
        self._tools = None
        self._context = None
        self._config = None
        self._session_history: list[dict] = []
    
    @property
    def config(self):
        """Lazy-load config."""
        if self._config is None:
            from nanobot.config.loader import load_config
            self._config = load_config()
        return self._config
    
    @property
    def provider(self):
        """Lazy-load provider."""
        if self._provider is None:
            from nanobot.providers.litellm_provider import LiteLLMProvider
            cfg = self.config
            p = cfg.get_provider()
            if not (p and p.api_key) and not cfg.agents.defaults.model.startswith("bedrock/"):
                raise RuntimeError("No API key configured")
            self._provider = LiteLLMProvider(
                api_key=p.api_key if p else None,
                api_base=cfg.get_api_base(),
                default_model=self.model or cfg.agents.defaults.model,
                extra_headers=p.extra_headers if p else None,
                provider_name=cfg.get_provider_name(),
            )
        return self._provider
    
    @property
    def tools(self):
        """Lazy-load tools."""
        if self._tools is None:
            from nanobot.agent.tools.registry import ToolRegistry
            from nanobot.agent.tools.filesystem import (
                ReadFileTool, WriteFileTool, EditFileTool, ListDirTool
            )
            from nanobot.agent.tools.shell import ExecTool
            from nanobot.agent.tools.web import WebSearchTool, WebFetchTool
            
            self._tools = ToolRegistry()
            self._tools.register(ReadFileTool())
            self._tools.register(WriteFileTool())
            self._tools.register(EditFileTool())
            self._tools.register(ListDirTool())
            self._tools.register(ExecTool(working_dir=str(self.workspace)))
            self._tools.register(WebSearchTool(
                api_key=self.config.tools.web.search.api_key
            ))
            self._tools.register(WebFetchTool())
        return self._tools
    
    @property
    def context(self):
        """Lazy-load context builder."""
        if self._context is None:
            from nanobot.agent.context import ContextBuilder
            self._context = ContextBuilder(self.workspace)
        return self._context
    
    def _log_tool_call(self, name: str, arguments: dict) -> None:
        """Log a tool call in verbose mode."""
        if not self.verbose:
            return
        
        args_json = json.dumps(arguments, indent=2, ensure_ascii=False)
        
        # Truncate long arguments
        if len(args_json) > 500:
            args_json = args_json[:500] + "\n... (truncated)"
        
        syntax = Syntax(args_json, "json", theme="monokai", line_numbers=False)
        panel = Panel(
            syntax,
            title=f"[cyan]tool:{name}[/cyan]",
            border_style="cyan",
            padding=(0, 1),
        )
        console.print(panel)
    
    def _log_tool_result(self, name: str, result: str) -> None:
        """Log a tool result in verbose mode."""
        if not self.verbose:
            return
        
        # Truncate long results
        display_result = result
        if len(result) > 1000:
            display_result = result[:1000] + "\n... (truncated)"
        
        text = Text(display_result)
        panel = Panel(
            text,
            title=f"[green]result:{name}[/green]",
            border_style="green",
            padding=(0, 1),
        )
        console.print(panel)
    
    def _prompt_breakpoint(self, name: str, arguments: dict) -> bool:
        """
        Prompt user at breakpoint before tool execution.
        
        Returns:
            True to execute, False to skip.
        """
        console.print(f"\n[yellow]⏸ Breakpoint before {name}[/yellow]")
        console.print("[dim]Press Enter to execute, 's' to skip, 'q' to quit[/dim]")
        
        try:
            response = input("> ").strip().lower()
            if response == 'q':
                raise KeyboardInterrupt
            if response == 's':
                return False
            return True
        except EOFError:
            raise KeyboardInterrupt
    
    async def process(self, content: str, max_iterations: int = 20) -> str:
        """
        Process a message through the agent.
        
        Args:
            content: User message
            max_iterations: Maximum tool call iterations
        
        Returns:
            Agent's final response
        """
        # Build messages
        messages = self.context.build_messages(
            history=self._session_history,
            current_message=content,
            channel="tty",
            chat_id="direct",
        )
        
        iteration = 0
        final_content = None
        
        while iteration < max_iterations:
            iteration += 1
            
            # Call LLM
            response = await self.provider.chat(
                messages=messages,
                tools=self.tools.get_definitions(),
                model=self.model or self.config.agents.defaults.model,
            )
            
            if response.has_tool_calls:
                # Add assistant message
                tool_call_dicts = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments)
                        }
                    }
                    for tc in response.tool_calls
                ]
                messages = self.context.add_assistant_message(
                    messages, response.content, tool_call_dicts,
                    reasoning_content=response.reasoning_content,
                )
                
                # Execute tools
                for tool_call in response.tool_calls:
                    self._log_tool_call(tool_call.name, tool_call.arguments)
                    
                    # Breakpoint check
                    if self.breakpoints:
                        if not self._prompt_breakpoint(tool_call.name, tool_call.arguments):
                            result = "[skipped by user]"
                            messages = self.context.add_tool_result(
                                messages, tool_call.id, tool_call.name, result
                            )
                            self._log_tool_result(tool_call.name, result)
                            continue
                    
                    # Execute or simulate
                    if self.dry_run:
                        result = f"[dry-run] Would execute {tool_call.name}"
                    else:
                        result = await self.tools.execute(
                            tool_call.name, tool_call.arguments
                        )
                    
                    messages = self.context.add_tool_result(
                        messages, tool_call.id, tool_call.name, result
                    )
                    self._log_tool_result(tool_call.name, result)
            else:
                final_content = response.content
                break
        
        if final_content is None:
            final_content = "Max iterations reached."
        
        # Update session history
        self._session_history.append({"role": "user", "content": content})
        self._session_history.append({"role": "assistant", "content": final_content})
        
        return final_content
    
    def reset(self) -> None:
        """Reset session history."""
        self._session_history = []
        console.print("[dim]Session reset.[/dim]")


def start_tty(
    workspace: Path | None = None,
    model: str | None = None,
    verbose: bool = True,
    dry_run: bool = False,
    breakpoints: bool = False,
) -> None:
    """
    Start the interactive TTY interface.
    
    Args:
        workspace: Override workspace path
        model: Override model
        verbose: Show tool calls and results
        dry_run: Don't actually execute tools
        breakpoints: Pause before each tool execution
    """
    import atexit
    import os
    import signal
    import sys
    import select
    from pathlib import Path
    
    from nanobot import __logo__, __version__
    from nanobot.config.loader import load_config
    
    # Load config for workspace
    config = load_config()
    ws = workspace or config.workspace_path
    
    # Create agent
    agent = TTYAgent(
        workspace=ws,
        model=model,
        verbose=verbose,
        dry_run=dry_run,
        breakpoints=breakpoints,
    )
    
    # Readline setup (borrowed from commands.py)
    _readline = None
    _history_file = Path.home() / ".nanobot" / "history" / "tty_history"
    _history_file.parent.mkdir(parents=True, exist_ok=True)
    _using_libedit = False
    
    try:
        import readline
        _readline = readline
        _using_libedit = "libedit" in (readline.__doc__ or "").lower()
        
        if _using_libedit:
            readline.parse_and_bind("bind ^I rl_complete")
        else:
            readline.parse_and_bind("tab: complete")
        
        try:
            readline.read_history_file(str(_history_file))
        except Exception:
            pass
    except ImportError:
        pass
    
    def save_history():
        if _readline:
            try:
                _readline.write_history_file(str(_history_file))
            except Exception:
                pass
    
    atexit.register(save_history)
    
    # Build prompt
    def prompt_text():
        if _readline is None:
            return "You: "
        if _using_libedit:
            return "\033[1;34mYou:\033[0m "
        return "\001\033[1;34m\002You:\001\033[0m\002 "
    
    # Banner
    mode_flags = []
    if verbose:
        mode_flags.append("[cyan]verbose[/cyan]")
    if dry_run:
        mode_flags.append("[yellow]dry-run[/yellow]")
    if breakpoints:
        mode_flags.append("[magenta]breakpoints[/magenta]")
    
    mode_str = " ".join(mode_flags) if mode_flags else "[dim]normal[/dim]"
    
    console.print(f"\n{__logo__} nanobot TTY v{__version__}")
    console.print(f"Mode: {mode_str}")
    console.print(f"Workspace: [dim]{ws}[/dim]")
    console.print("\nCommands: /reset (clear history), /verbose, /dry-run, /breakpoints, /quit")
    console.print("Ctrl+C or /quit to exit\n")
    
    # Signal handler
    def exit_handler(signum, frame):
        save_history()
        console.print("\nGoodbye! 🐱")
        os._exit(0)
    
    signal.signal(signal.SIGINT, exit_handler)
    
    # Flush pending input
    def flush_input():
        try:
            fd = sys.stdin.fileno()
            if not os.isatty(fd):
                return
            while True:
                ready, _, _ = select.select([fd], [], [], 0)
                if not ready:
                    break
                if not os.read(fd, 4096):
                    break
        except Exception:
            pass
    
    # Main loop
    async def run_loop():
        nonlocal verbose, dry_run, breakpoints
        
        while True:
            try:
                flush_input()
                user_input = await asyncio.to_thread(input, prompt_text())
                
                if not user_input.strip():
                    continue
                
                # Handle commands
                cmd = user_input.strip().lower()
                if cmd == "/quit" or cmd == "/exit":
                    break
                elif cmd == "/reset":
                    agent.reset()
                    continue
                elif cmd == "/verbose":
                    agent.verbose = not agent.verbose
                    console.print(f"Verbose: {'on' if agent.verbose else 'off'}")
                    continue
                elif cmd == "/dry-run":
                    agent.dry_run = not agent.dry_run
                    console.print(f"Dry-run: {'on' if agent.dry_run else 'off'}")
                    continue
                elif cmd == "/breakpoints":
                    agent.breakpoints = not agent.breakpoints
                    console.print(f"Breakpoints: {'on' if agent.breakpoints else 'off'}")
                    continue
                elif cmd.startswith("/"):
                    console.print(f"[red]Unknown command: {cmd}[/red]")
                    continue
                
                # Process message
                response = await agent.process(user_input)
                console.print(f"\n{__logo__} {response}\n")
                
            except KeyboardInterrupt:
                break
            except EOFError:
                break
    
    try:
        asyncio.run(run_loop())
    finally:
        save_history()
        console.print("Goodbye! 🐱")
