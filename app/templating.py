"""
Shared Jinja2 templates instance.

Kept in its own module so that any route can import it without creating a
circular import with app.main.
"""
from pathlib import Path

from starlette.templating import Jinja2Templates

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
