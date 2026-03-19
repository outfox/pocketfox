"""Task-local context for asyncio concurrency safety."""

from contextvars import ContextVar
from dataclasses import dataclass


@dataclass
class TaskContext:
    """Per-task routing context, set before each agent turn."""

    context_name: str
    channel: str
    chat_id: str


current_task: ContextVar[TaskContext | None] = ContextVar("task_context", default=None)
