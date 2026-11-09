from .adapters.database import Database
from .adapters.storage import LocalStore
from .adapters.readers import READERS
from .adapters.query_backends import BACKENDS


class Application:
    def __init__(self, settings):
        self.settings = settings
        self.db = Database(settings.dsn, pool_min=1, pool_max=10, pool_timeout=3)
        self.store = LocalStore(settings.root)
        if set(READERS) != {"csv", "parquet"} or set(BACKENDS) != {"table", "metrics"}:
            raise RuntimeError("INVALID_CAPABILITY_REGISTRY")
