"""Shell execution tool with optional bubblewrap sandboxing."""

import asyncio
import os
import re
import shutil
from pathlib import Path
from typing import Any

from pocketfox.agent.tools.base import Tool


class ExecTool(Tool):
    """Tool to execute shell commands with optional sandbox isolation.
    
    When sandbox_dir is configured, commands run inside a bubblewrap (bwrap)
    sandbox with only the specified directory visible as /workspace.
    This prevents access to credentials, prompt files, and system configs.
    """
    
    def __init__(
        self,
        timeout: int = 60,
        working_dir: str | None = None,
        deny_patterns: list[str] | None = None,
        allow_patterns: list[str] | None = None,
        restrict_to_workspace: bool = False,
        sandbox_dir: str | None = None,
        sandbox_readonly_paths: list[str] | None = None,
    ):
        """Initialize the exec tool.
        
        Args:
            timeout: Maximum seconds to wait for command completion
            working_dir: Default working directory for commands
            deny_patterns: Regex patterns to block (safety guard)
            allow_patterns: If set, only matching commands are allowed
            restrict_to_workspace: Enable path traversal checks (legacy)
            sandbox_dir: If set, run commands in bwrap sandbox with this as /workspace
            sandbox_readonly_paths: Additional paths to mount read-only in sandbox
        """
        self.timeout = timeout
        self.working_dir = working_dir
        self.sandbox_dir = sandbox_dir
        self.sandbox_readonly_paths = sandbox_readonly_paths or []
        self.deny_patterns = deny_patterns or [
            r"\brm\s+-[rf]{1,2}\b",          # rm -r, rm -rf, rm -fr
            r"\bdel\s+/[fq]\b",              # del /f, del /q
            r"\brmdir\s+/s\b",               # rmdir /s
            r"\b(format|mkfs|diskpart)\b",   # disk operations
            r"\bdd\s+if=",                   # dd
            r">\s*/dev/sd",                  # write to disk
            r"\b(shutdown|reboot|poweroff)\b",  # system power
            r":\(\)\s*\{.*\};\s*:",          # fork bomb
        ]
        self.allow_patterns = allow_patterns or []
        self.restrict_to_workspace = restrict_to_workspace
        
        # Check if bwrap is available when sandbox is requested
        self._bwrap_available: bool | None = None
    
    @property
    def name(self) -> str:
        return "exec"
    
    @property
    def description(self) -> str:
        if self.sandbox_dir:
            return (
                "Execute a shell command in an isolated sandbox. "
                "Only /workspace is accessible (mapped to the configured sandbox directory). "
                "System files, credentials, and prompt files are not visible."
            )
        return "Execute a shell command and return its output. Use with caution."
    
    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to execute"
                },
                "working_dir": {
                    "type": "string",
                    "description": "Optional working directory (relative to /workspace in sandbox mode)"
                }
            },
            "required": ["command"]
        }
    
    def _check_bwrap(self) -> bool:
        """Check if bubblewrap is available."""
        if self._bwrap_available is None:
            self._bwrap_available = shutil.which("bwrap") is not None
        return self._bwrap_available
    
    def _build_bwrap_command(self, command: str, working_dir: str) -> list[str]:
        """Build the bwrap command with appropriate mounts.
        
        The sandbox provides:
        - /workspace: read-write, mapped to sandbox_dir
        - /usr, /bin, /lib, /lib64: read-only system paths
        - /tmp: isolated tmpfs
        - /dev/null, /dev/zero, /dev/urandom: essential devices
        - No network (--unshare-net)
        - No access to home, /etc, or any other paths
        """
        sandbox_path = Path(self.sandbox_dir).resolve()
        
        # Calculate working directory relative to sandbox
        if working_dir:
            work_path = Path(working_dir).resolve()
            try:
                rel_work = work_path.relative_to(sandbox_path)
                sandbox_cwd = f"/workspace/{rel_work}"
            except ValueError:
                # working_dir is outside sandbox, default to /workspace
                sandbox_cwd = "/workspace"
        else:
            sandbox_cwd = "/workspace"
        
        bwrap_args = [
            "bwrap",
            # Isolation
            "--unshare-all",           # Unshare all namespaces
            "--share-net",             # But keep network (needed for git, curl, etc.)
            "--die-with-parent",       # Kill sandbox if parent dies
            
            # Essential system paths (read-only)
            "--ro-bind", "/usr", "/usr",
            "--ro-bind", "/bin", "/bin",
            "--ro-bind", "/etc/resolv.conf", "/etc/resolv.conf",  # DNS
            "--ro-bind", "/etc/ssl", "/etc/ssl",  # SSL certs
            "--ro-bind", "/etc/ca-certificates", "/etc/ca-certificates",
            
            # Symlinks for lib paths (needed on most distros)
            "--symlink", "usr/lib", "/lib",
            "--symlink", "usr/lib64", "/lib64",
            
            # Isolated /tmp
            "--tmpfs", "/tmp",
            
            # Essential devices
            "--dev", "/dev",
            
            # The workspace - read-write
            "--bind", str(sandbox_path), "/workspace",
            
            # Working directory
            "--chdir", sandbox_cwd,
        ]
        
        # Add any additional read-only paths
        for ro_path in self.sandbox_readonly_paths:
            if Path(ro_path).exists():
                # Mount at the same path inside sandbox
                bwrap_args.extend(["--ro-bind", ro_path, ro_path])
        
        # Add the actual command
        bwrap_args.extend(["sh", "-c", command])
        
        return bwrap_args
    
    async def execute(self, command: str, working_dir: str | None = None, **kwargs: Any) -> str:
        # Determine working directory
        cwd = working_dir or self.working_dir or os.getcwd()
        
        # Apply safety guards
        guard_error = self._guard_command(command, cwd)
        if guard_error:
            return guard_error
        
        # Decide execution mode
        use_sandbox = self.sandbox_dir is not None
        
        if use_sandbox:
            if not self._check_bwrap():
                return (
                    "Error: Sandbox mode requested but 'bwrap' (bubblewrap) is not installed. "
                    "Install with: apt-get install bubblewrap"
                )
            
            # Verify sandbox directory exists
            sandbox_path = Path(self.sandbox_dir).resolve()
            if not sandbox_path.exists():
                return f"Error: Sandbox directory does not exist: {self.sandbox_dir}"
            
            bwrap_cmd = self._build_bwrap_command(command, cwd)
            actual_command = bwrap_cmd
            shell_mode = False
            exec_cwd = None  # bwrap handles cwd internally
        else:
            actual_command = command
            shell_mode = True
            exec_cwd = cwd
        
        try:
            if shell_mode:
                process = await asyncio.create_subprocess_shell(
                    actual_command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=exec_cwd,
                )
            else:
                process = await asyncio.create_subprocess_exec(
                    *actual_command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=self.timeout
                )
            except asyncio.TimeoutError:
                process.kill()
                return f"Error: Command timed out after {self.timeout} seconds"
            
            output_parts = []
            
            if stdout:
                output_parts.append(stdout.decode("utf-8", errors="replace"))
            
            if stderr:
                stderr_text = stderr.decode("utf-8", errors="replace")
                if stderr_text.strip():
                    output_parts.append(f"STDERR:\n{stderr_text}")
            
            if process.returncode != 0:
                output_parts.append(f"\nExit code: {process.returncode}")
            
            result = "\n".join(output_parts) if output_parts else "(no output)"
            
            # Truncate very long output
            max_len = 10000
            if len(result) > max_len:
                result = result[:max_len] + f"\n... (truncated, {len(result) - max_len} more chars)"
            
            return result
            
        except Exception as e:
            return f"Error executing command: {str(e)}"

    def _guard_command(self, command: str, cwd: str) -> str | None:
        """Best-effort safety guard for potentially destructive commands."""
        cmd = command.strip()
        lower = cmd.lower()

        for pattern in self.deny_patterns:
            if re.search(pattern, lower):
                return "Error: Command blocked by safety guard (dangerous pattern detected)"

        if self.allow_patterns:
            if not any(re.search(p, lower) for p in self.allow_patterns):
                return "Error: Command blocked by safety guard (not in allowlist)"

        if self.restrict_to_workspace:
            if "..\\" in cmd or "../" in cmd:
                return "Error: Command blocked by safety guard (path traversal detected)"

            cwd_path = Path(cwd).resolve()

            win_paths = re.findall(r"[A-Za-z]:\\[^\\\"']+", cmd)
            posix_paths = re.findall(r"/[^\s\"']+", cmd)

            for raw in win_paths + posix_paths:
                try:
                    p = Path(raw).resolve()
                except Exception:
                    continue
                if cwd_path not in p.parents and p != cwd_path:
                    return "Error: Command blocked by safety guard (path outside working dir)"

        return None
