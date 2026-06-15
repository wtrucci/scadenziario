from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Integer, Numeric, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, utcnow


class OverrideImporto(Base):
    """
    Per-occurrence override of price and/or quantity for a single service date.

    Occurrences are computed on the fly from the parent Servizio; this table
    lets the user correct importo/quantita for ONE specific occurrence date
    without changing the service defaults. The occurrence-calculation logic
    (a later step) looks up an override by (servizio_id, data_occorrenza) and
    falls back to the service defaults when none exists.

    Deleted together with the parent service (cascade on the Servizio side).
    """
    __tablename__ = "override_importo"
    # An occurrence date is unique per service: at most one override per date.
    __table_args__ = (
        UniqueConstraint("servizio_id", "data_occorrenza", name="uq_override_servizio_data"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    servizio_id: Mapped[int] = mapped_column(ForeignKey("servizi.id"))

    # The specific occurrence date this override applies to.
    data_occorrenza: Mapped[date] = mapped_column(Date)

    # Overridden unit price and quantity for that occurrence.
    importo: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    quantita: Mapped[int] = mapped_column(Integer)

    creato_il: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    servizio: Mapped[Servizio] = relationship(back_populates="override_importi")

    @property
    def totale(self) -> Decimal:
        """Total for the overridden occurrence: unit price × quantity."""
        return self.importo * self.quantita

    def __repr__(self) -> str:
        return (
            f"<OverrideImporto id={self.id} servizio_id={self.servizio_id}"
            f" data={self.data_occorrenza}>"
        )
