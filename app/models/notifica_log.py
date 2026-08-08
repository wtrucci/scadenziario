from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Enum, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, utcnow
from app.models.enums import CanalNotifica, TipoNotifica


class NotificaLog(Base):
    __tablename__ = "notifica_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    servizio_id: Mapped[int] = mapped_column(ForeignKey("servizi.id"))

    # The date this notification refers to: an occurrence date for
    # preavviso/promemoria_7_giorni, or the contract's effective end date for
    # contratto_scaduto. So dedup works per (servizio, data, tipo, canale).
    data_occorrenza: Mapped[date] = mapped_column(Date)

    # Which of the three triggers produced this notification (see TipoNotifica).
    tipo: Mapped[TipoNotifica] = mapped_column(
        Enum(TipoNotifica, native_enum=False, length=30)
    )

    canale: Mapped[CanalNotifica] = mapped_column(
        Enum(CanalNotifica, native_enum=False, length=30)
    )
    inviata_il: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # True = notification delivered successfully, False = delivery failed.
    esito: Mapped[bool] = mapped_column(Boolean)
    # Error message or API response detail; null on success.
    dettaglio: Mapped[str | None] = mapped_column(Text)

    servizio: Mapped[Servizio] = relationship(back_populates="notifiche")

    def __repr__(self) -> str:
        return (
            f"<NotificaLog id={self.id} servizio_id={self.servizio_id}"
            f" canale={self.canale.value} esito={self.esito}>"
        )
