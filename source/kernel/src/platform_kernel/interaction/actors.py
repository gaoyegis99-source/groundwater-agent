"""The one compatibility default for direct Interaction persistence callers.

HTTP and composed application writes always carry an explicit actor.  This
constant exists only for narrow direct/test callers that predate the composed
identity boundary, so they cannot silently drift to a separate hard-coded
local subject.
"""

from platform_protocol_contracts import LocalIdentity


DEFAULT_LOCAL_ACTOR_SUBJECT = LocalIdentity().subject
