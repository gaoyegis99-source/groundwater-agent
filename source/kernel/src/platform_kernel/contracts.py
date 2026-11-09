"""Canonical cross-service execution contracts.

Business code imports these names from Kernel for local cohesion; definitions live in
platform-protocol-contracts so Kernel and Executor validate the same wire format.
"""

from platform_protocol_contracts import DataVersionRefV2, JobRequestV6, JobStatusV6

__all__ = ["DataVersionRefV2", "JobRequestV6", "JobStatusV6"]
