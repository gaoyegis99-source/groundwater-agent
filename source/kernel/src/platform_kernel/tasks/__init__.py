"""Kernel task intent, immutable revisions and derived completion."""
from .models import TaskUpdate
from .persistence import (
    bind_confirmation, commit_updates, context_for_run, get_task,
    refresh_task, require_current_confirmation, stage_update, task_in_connection,
)
from .preparation import (
    get_preparation,
    preparation_in_connection,
    save_preparation,
    save_preparation_connection,
)

__all__ = ["TaskUpdate", "bind_confirmation", "commit_updates", "context_for_run",
           "get_task", "refresh_task", "require_current_confirmation", "stage_update",
           "task_in_connection"]
__all__ += [
    "get_preparation", "preparation_in_connection", "save_preparation",
    "save_preparation_connection",
]
