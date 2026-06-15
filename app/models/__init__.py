# Import all models here so SQLAlchemy registers their tables with Base.metadata.
# Alembic's env.py imports this module to discover all tables before autogenerating migrations.
from app.models.cliente import Cliente
from app.models.notifica_log import NotificaLog
from app.models.override_importo import OverrideImporto
from app.models.servizio import Servizio
from app.models.utente import Utente

__all__ = ["Cliente", "Servizio", "Utente", "NotificaLog", "OverrideImporto"]
