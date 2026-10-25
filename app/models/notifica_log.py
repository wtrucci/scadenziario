from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, Enum, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, utcnow
from app.models.enums import CanalNotifica, TipoNotifica


class NotificaLog(Base):
    """One notification attempt, successful or not.

    It is about EITHER a customer service OR a supplier contract — exactly one
    of the two ids is set, enforced by the check constraint so a row can never
    point at both or at nothing.
    """
    __tablename__ = "notifica_log"
    __table_args__ = (
        CheckConstraint(
            "(servizio_id IS NULL) <> (contratto_fornitore_id IS NULL)",
            name="ck_notifica_log_un_solo_oggetto",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    servizio_id: Mapped[int | None] = mapped_column(ForeignKey("servizi.id"), nullable=True)
    contratto_fornitore_id: Mapped[int | None] = mapped_column(
        ForeignKey("contratti_fornitore.id", name="fk_notifica_log_contratto_fornitore"),
        nullable=True,
    )

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

    servizio: Mapped[Servizio | None] = relationship(back_populates="notifiche")
    contratto_fornitore: Mapped[ContrattoFornitore | None] = relationship(
        back_populates="notifiche"
    )

    @property
    def fornitore(self) -> bool:
        """True when the row is about a supplier contract."""
        return self.contratto_fornitore_id is not None

    def __repr__(self) -> str:
        return (
            f"<NotificaLog id={self.id} servizio_id={self.servizio_id}"
            f" contratto_fornitore_id={self.contratto_fornitore_id}"
            f" canale={self.canale.value} esito={self.esito}>"
        )
