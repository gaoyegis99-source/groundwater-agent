from .app import create_app
from .composition import create_application_from_environment


application = create_application_from_environment()
app = create_app(application)
