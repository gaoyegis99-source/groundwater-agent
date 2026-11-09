"""Metadata for application commands that atomically span Kernel owners.

The decorator intentionally has no runtime transaction magic.  A command must
still open its database transaction explicitly; the declaration makes the
affected state owners inspectable by the architecture gate and by maintainers.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar


Command = TypeVar("Command", bound=Callable[..., object])


def transaction_command(*, name: str, tables: tuple[str, ...]):
    """Declare the persistent tables owned by one atomic application command."""

    if not name or not tables or len(set(tables)) != len(tables):
        raise ValueError("TRANSACTION_COMMAND_INVALID")

    def declare(command: Command) -> Command:
        setattr(command, "__kernel_transaction_command__", name)
        setattr(command, "__kernel_transaction_tables__", tables)
        return command

    return declare
