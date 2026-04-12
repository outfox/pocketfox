"""Context router — matches inbound messages to contexts and resolves outputs."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pocketfox.config.schema import ContextConfig


class ContextRouter:
    """Routes messages to matching contexts and resolves output targets.

    Lookup structures:
    - _exact[channel:chat_id] → list of context names (exact match)
    - _wildcard[channel] → list of context names (channel:* patterns)
    """

    def __init__(self, contexts: dict[str, ContextConfig]):
        self.contexts = contexts
        self._exact: dict[str, list[str]] = {}
        self._wildcard: dict[str, list[str]] = {}

        for name, cfg in contexts.items():
            for inp in cfg.inputs:
                channel, chat_id = self._parse_target(inp)
                if chat_id == "*":
                    self._wildcard.setdefault(channel, []).append(name)
                else:
                    key = f"{channel}:{chat_id}"
                    self._exact.setdefault(key, []).append(name)

    @staticmethod
    def _parse_target(spec: str) -> tuple[str, str]:
        """Parse 'channel:chat_id' into (channel, chat_id)."""
        channel, _, chat_id = spec.partition(":")
        return channel, chat_id or "*"

    def match(self, channel: str, chat_id: str) -> list[str]:
        """Return all context names matching this inbound message."""
        matched: list[str] = []
        # Exact match first
        key = f"{channel}:{chat_id}"
        if key in self._exact:
            matched.extend(self._exact[key])
        # Wildcard match
        if channel in self._wildcard:
            for name in self._wildcard[channel]:
                if name not in matched:
                    matched.append(name)
        return matched

    def get_context_files(self, name: str) -> list[str]:
        """Return context_files for a context."""
        return list(self.contexts[name].context_files)

    def get_cron_context_files(self, name: str) -> list[str]:
        """Return context_files + cron_files for cron-triggered turns."""
        cfg = self.contexts[name]
        return list(cfg.context_files) + list(cfg.cron_files)

    def get_outputs(
        self,
        name: str,
        trigger_channel: str | None = None,
        trigger_chat_id: str | None = None,
    ) -> list[tuple[str, str]]:
        """Deduplicated output targets. trigger=None → only outputs_always."""
        cfg = self.contexts[name]
        seen: set[tuple[str, str]] = set()
        result: list[tuple[str, str]] = []

        def _add(channel: str, chat_id: str) -> None:
            pair = (channel, chat_id)
            if pair not in seen:
                seen.add(pair)
                result.append(pair)

        # Always-on outputs (literal targets)
        for spec in cfg.outputs_always:
            ch, cid = self._parse_target(spec)
            _add(ch, cid)

        # Responsive outputs (only when there's a trigger)
        if trigger_channel and trigger_chat_id:
            for spec in cfg.outputs_responsive:
                ch, cid = self._parse_target(spec)
                # Wildcard resolves to the triggering chat_id
                if cid == "*" and ch == trigger_channel:
                    _add(ch, trigger_chat_id)
                elif cid != "*":
                    _add(ch, cid)

        return result

    def get_cron_contexts(self) -> list[tuple[str, ContextConfig]]:
        """Return contexts that have a cron schedule."""
        return [(name, cfg) for name, cfg in self.contexts.items() if cfg.cron]

    def has_context(self, name: str) -> bool:
        """Check if a context name exists."""
        return name in self.contexts

    def get_config(self, name: str) -> ContextConfig:
        """Return the ContextConfig for a context name."""
        return self.contexts[name]
