from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, utcnow


class Cliente(Base):
    __tablename__ = "clienti"

    id: Mapped[int] = mapped_column(primary_key=True)
    nome: Mapped[str] = mapped_column(String(200))
    note: Mapped[str | None] = mapped_column(Text)
    attivo: Mapped[bool] = mapped_column(Boolean, default=True)
    creato_il: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    # No delete cascade: deleting a customer with active services must be blocked
    # at the application layer (see routes/clienti.py) and at the DB layer
    # (PRAGMA foreign_keys=ON in database.py). passive_deletes=True tells
    # SQLAlchemy not to load services into memory before deleting the customer —
    # if the delete slips past the app-level check, the DB FK constraint fires.
    servizi: Mapped[list[Servizio]] = relationship(
        back_populates="cliente",
        cascade="save-update, merge",
        passive_deletes=True,
    )

    def __repr__(self) -> str:
        return f"<Cliente id={self.id} nome={self.nome!r}>"
