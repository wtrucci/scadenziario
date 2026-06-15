from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Integer, Numeric
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, utcnow


class RinnovoLog(Base):
    """
    Immutable snapshot of one automatic renewal cycle.

    Written by the scheduler BEFORE advancing data_scadenza on the parent
    Servizio, so every billable period is preserved even if the service is
    later modified or deleted (cascade keeps the log alive only as long as
    the service exists — if the service is deleted the log is deleted too,
    since the billing context no longer makes sense).
    """
    __tablename__ = "rinnovo_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    servizio_id: Mapped[int] = mapped_column(ForeignKey("servizi.id"))

    # The expiry date that triggered this renewal (what we're advancing FROM).
    data_rinnovo: Mapped[date] = mapped_column(Date)
    # The new expiry date after the advance (what data_scadenza becomes).
    nuova_scadenza: Mapped[date] = mapped_column(Date)

    # Economic snapshot at the moment of renewal. Stored explicitly so that
    # later edits to the service do not alter the historical billing record.
    importo: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    quantita: Mapped[int] = mapped_column(Integer)
    totale: Mapped[Decimal] = mapped_column(Numeric(10, 2))

    creato_il: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    servizio: Mapped[Servizio] = relationship(back_populates="rinnovi")

    def __repr__(self) -> str:
        return (
            f"<RinnovoLog id={self.id} servizio_id={self.servizio_id}"
            f" {self.data_rinnovo} -> {self.nuova_scadenza}>"
        )
