from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, utcnow
from app.models.enums import RuoloUtente


class Utente(Base):
    __tablename__ = "utenti"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    # Store only the hash, never the plaintext password.
    password_hash: Mapped[str] = mapped_column(String(255))
    ruolo: Mapped[RuoloUtente] = mapped_column(
        Enum(RuoloUtente, native_enum=False, length=20), default=RuoloUtente.utente
    )
    attivo: Mapped[bool] = mapped_column(Boolean, default=True)
    creato_il: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    def __repr__(self) -> str:
        return f"<Utente id={self.id} username={self.username!r} ruolo={self.ruolo.value}>"
