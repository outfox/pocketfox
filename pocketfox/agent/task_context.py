"""Task-local context for asyncio concurrency safety."""

from contextvars import ContextVar
from dataclasses import dataclass

_DEFAULT = None  # Lazy-initialized singleton


@dataclass
class TaskContext:
    """Per-task routing context, set before each agent turn."""

    context_name: str
    channel: str
    chat_id: str
    model: str | None = None


current_task: ContextVar[TaskContext | None] = ContextVar("task_context", default=None)


def get_task_context() -> TaskContext:
    """Return the current TaskContext, or a default if none is set.

    Provides consistent fallback values across all call sites.
    """
    tc = current_task.get()
    if tc is not None:
        return tc
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = TaskContext(context_name="default", channel="cli", chat_id="direct")
    return _DEFAULT
