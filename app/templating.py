"""
Shared Jinja2 templates instance.

Kept in its own module so that any route can import it without creating a
circular import with app.main.
"""
from pathlib import Path

from starlette.templating import Jinja2Templates

from app.config import settings

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Available to every template without each route having to pass it: the version
# belongs to the page chrome (see base.html), not to any one view's context.
templates.env.globals["app_version"] = settings.APP_VERSION
