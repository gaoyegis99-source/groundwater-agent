from .api.app import create_app
from .settings import Settings

app = create_app(Settings.env())
