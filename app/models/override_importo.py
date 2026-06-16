from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, utcnow


class OverrideImporto(Base):
    """
    Per-occurrence STATE row for a single service date.

    Occurrences are computed on the fly from the parent Servizio and are not
    stored as rows. This table is where we persist anything that makes a
    SPECIFIC occurrence special, namely:

    * ``importo`` / ``quantita`` — an optional price/quantity for that one
      occurrence. Both are nullable: when NULL the engine falls back to the
      service defaults. They can be set for two DIFFERENT reasons, told apart by
      ``override_manuale``:
        - ``override_manuale=True``: a deliberate correction by the user (e.g. a
          discount). The dashboard shows the "Override" badge for these.
        - ``override_manuale=False`` but ``importo`` set: an automatic SNAPSHOT
          taken at billing time (see below). This is just the normal price,
          frozen — NO "Override" badge.
    * ``fatturato`` / ``fatturato_il`` — whether the user has marked that
      occurrence as billed, and when. On billing the app snapshots the current
      effective price/quantity here so a later change to the service price does
      NOT alter what was already billed. On un-billing the snapshot is cleared
      (importo/quantita back to NULL) ONLY if it was not a manual override.
    * ``note`` — free text for a manual override (e.g. the reason for a discount).

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

    # Optional price/quantity for that occurrence (NULL = use the service
    # default). May hold a manual override or an automatic billing snapshot;
    # override_manuale tells the two apart.
    importo: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    quantita: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # True only for a deliberate user correction (drives the "Override" badge);
    # False for a plain billing snapshot.
    override_manuale: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Free text for a manual override (e.g. the reason for a discount).
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Billing state for that occurrence.
    fatturato: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    fatturato_il: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    creato_il: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    servizio: Mapped[Servizio] = relationship(back_populates="override_importi")

    def __repr__(self) -> str:
        return (
            f"<OverrideImporto id={self.id} servizio_id={self.servizio_id}"
            f" data={self.data_occorrenza} fatturato={self.fatturato}>"
        )
