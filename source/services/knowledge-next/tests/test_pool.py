import pytest
from psycopg_pool import PoolTimeout

from platform_knowledge_next.config import Settings
from platform_knowledge_next.database import Database
from platform_knowledge_next.errors import KnowledgeProblemError


def test_database_pool_exhaustion_is_a_stable_service_error() -> None:
    class ExhaustedPool:
        def connection(self):
            raise PoolTimeout()

    database = Database(Settings("postgresql://unused"))
    database.pool = ExhaustedPool()
    database._pool_open = True
    with pytest.raises(KnowledgeProblemError) as failure:
        with database.transaction():
            pass
    assert (failure.value.code, failure.value.status) == (
        "DATABASE_POOL_EXHAUSTED",
        503,
    )
