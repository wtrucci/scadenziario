from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, utcnow


class OverrideImporto(Base):
    """
    Per-occurrence STATE row for a single service date.

    Occurrences are computed on the fly from the parent Servizio and are not
    stored as rows. This table is where we persist anything that makes a
    SPECIFIC occurrence special, namely:

    * ``importo`` / ``quantita`` — an optional price/quantity correction for
      that one occurrence. Both are nullable: when NULL the engine falls back
      to the service defaults. They are overridden together or not at all in
      the UI, but the engine treats each independently.
    * ``fatturato`` / ``fatturato_il`` — whether the user has marked that
      occurrence as billed, and when. This is persistent per-occurrence state;
      since occurrences are not rows, this table is the only place to keep it.

    A row may therefore exist purely to carry the ``fatturato`` flag, with
    ``importo``/``quantita`` left NULL. Toggling "fatturato" creates or updates
    the row for (servizio_id, data_occorrenza).

    Deleted together with the parent service (cascade on the Servizio side).
    """
    __tablename__ = "override_importo"
    # An occurrence date is unique per service: at most one state row per date.
    __table_args__ = (
        UniqueConstraint("servizio_id", "data_occorrenza", name="uq_override_servizio_data"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    servizio_id: Mapped[int] = mapped_column(ForeignKey("servizi.id"))

    # The specific occurrence date this state row applies to.
    data_occorrenza: Mapped[date] = mapped_column(Date)

    # Optional price/quantity correction for that occurrence (NULL = use the
    # service default).
    importo: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    quantita: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Billing state for that occurrence.
    fatturato: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    fatturato_il: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    creato_il: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    servizio: Mapped[Servizio] = relationship(back_populates="override_importi")

    @property
    def ha_override_importo(self) -> bool:
        """True if this row actually corrects price or quantity (not just a flag)."""
        return self.importo is not None or self.quantita is not None

    def __repr__(self) -> str:
        return (
            f"<OverrideImporto id={self.id} servizio_id={self.servizio_id}"
            f" data={self.data_occorrenza} fatturato={self.fatturato}>"
        )
