"""Tool registry for dynamic tool management."""

import fnmatch
from typing import Any

from pocketfox.agent.tools.base import Tool


class ToolRegistry:
    """
    Registry for agent tools.

    Allows dynamic registration and execution of tools.
    """

    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        """Register a tool."""
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> None:
        """Unregister a tool by name."""
        self._tools.pop(name, None)

    def get(self, name: str) -> Tool | None:
        """Get a tool by name."""
        return self._tools.get(name)

    def has(self, name: str) -> bool:
        """Check if a tool is registered."""
        return name in self._tools

    def get_definitions(
        self,
        allowed_patterns: list[str] | tuple[str, ...] | None = None,
    ) -> list[dict[str, Any]]:
        """Get tool definitions in OpenAI format, optionally filtered by glob patterns.

        Args:
            allowed_patterns: Glob patterns (fnmatch syntax: ``*``, ``?``, ``[seq]``).
                Empty or ``None`` returns all tools (no filtering).
        """
        defs = [tool.to_schema() for tool in self._tools.values()]
        if not allowed_patterns:
            return defs
        return [d for d in defs if self._name_matches(d["function"]["name"], allowed_patterns)]

    def is_allowed(
        self,
        name: str,
        allowed_patterns: list[str] | tuple[str, ...] | None,
    ) -> bool:
        """Check if a tool name is allowed under the given whitelist.

        Empty or ``None`` patterns means allow-all.
        """
        if not allowed_patterns:
            return True
        return self._name_matches(name, allowed_patterns)

    @staticmethod
    def _name_matches(name: str, patterns: list[str] | tuple[str, ...]) -> bool:
        return any(fnmatch.fnmatchcase(name, p) for p in patterns)

    async def execute(self, name: str, params: dict[str, Any]) -> str | list[dict[str, Any]]:
        """
        Execute a tool by name with given parameters.

        Args:
            name: Tool name.
            params: Tool parameters.

        Returns:
            Tool execution result as string, or a list of content blocks
            for multimodal results (e.g., images from view_image).

        Raises:
            KeyError: If tool not found.
        """
        tool = self._tools.get(name)
        if not tool:
            return f"Error: Tool '{name}' not found"

        try:
            errors = tool.validate_params(params)
            if errors:
                return f"Error: Invalid parameters for tool '{name}': " + "; ".join(errors)
            return await tool.execute(**params)
        except Exception as e:
            return f"Error executing {name}: {str(e)}"

    def redact_params(self, name: str, params: dict[str, Any]) -> dict[str, Any]:
        """
        Redact sensitive parameters for a tool.

        Args:
            name: Tool name.
            params: Parameters to redact.

        Returns:
            Redacted parameters, or original if tool not found.
        """
        tool = self._tools.get(name)
        if tool:
            return tool.redact_params(params)
        return params

    @property
    def tool_names(self) -> list[str]:
        """Get list of registered tool names."""
        return list(self._tools.keys())

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools
