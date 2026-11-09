"""Stable public exports for control-proposal behavior.

The module remains a compatibility surface for external callers.  Kernel
implementation code must import the concrete intent, query, target, or command
layer that owns its dependency instead of routing through this façade.
"""

from .control_intents import rule_intent
from .control_proposal_commands import (
    claim_control_proposal,
    create_control_request,
    create_status_request,
    dismiss_control_proposal,
    finish_control_proposal,
)
from .control_proposal_queries import (
    confirmed_control_proposals,
    control_source_message,
    list_control_messages_connection,
    list_control_proposals_connection,
)

__all__ = (
    "claim_control_proposal",
    "confirmed_control_proposals",
    "control_source_message",
    "create_control_request",
    "create_status_request",
    "dismiss_control_proposal",
    "finish_control_proposal",
    "list_control_messages_connection",
    "list_control_proposals_connection",
    "rule_intent",
)
