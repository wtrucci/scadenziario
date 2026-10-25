from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, utcnow


class ContrattoFornitore(Base):
    """
    Something WE buy from a supplier and resell (an RMM platform, S3 storage…).

    Its deadlines are the supplier's, not the customers': the customer services
    built on it renew on their own dates. So it lives apart from Servizio — it
    is a cost, and must never show up in the dashboard, the monthly summary or
    the billing cards.

    Deliberately simpler than Servizio: there is no invoicing to track, hence
    no occurrences and no per-occurrence state. ``data_scadenza`` is the next
    date the contract has to be renewed (or paid, or cancelled) by:

    * with ``rinnovo_automatico`` the supplier renews it by itself, so the
      shown expiry is computed by stepping ``cadenza_mesi`` from this date
      (see app/services/fornitori.py) and it never reads "Scaduto";
    * without it, renewing is an action — the "Rinnovato" button moves this
      date forward by one cadence. Until then a past date reads "Scaduto".

    ``fornitore`` is plain text (with suggestions in the form) rather than a
    table of suppliers: there are a handful, and nothing else hangs off them.
    """
    __tablename__ = "contratti_fornitore"

    id: Mapped[int] = mapped_column(primary_key=True)

    fornitore: Mapped[str] = mapped_column(String(200))
    descrizione: Mapped[str] = mapped_column(String(300))
    # Optional: contract number, account or tenant id at the supplier.
    riferimento: Mapped[str | None] = mapped_column(String(200))

    # Next renewal date (see the class docstring for how it moves).
    data_scadenza: Mapped[date] = mapped_column(Date, index=True)
    # Months per renewal period (1 = monthly, 12 = yearly, ...).
    cadenza_mesi: Mapped[int] = mapped_column(Integer)
    rinnovo_automatico: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # What we pay per period: quantita * importo (see ``totale``).
    importo: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    quantita: Mapped[int] = mapped_column(Integer, default=1)
    valuta: Mapped[str] = mapped_column(String(3), default="EUR")

    preavviso_giorni: Mapped[int] = mapped_column(Integer, default=30)
    # We cancelled it: no more warnings, shown as "Disdetto".
    disdetto: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    note: Mapped[str | None] = mapped_column(Text)

    creato_il: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    aggiornato_il: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    # Notifications sent about this contract; meaningless without it.
    notifiche: Mapped[list[NotificaLog]] = relationship(
        back_populates="contratto_fornitore", cascade="all, delete-orphan"
    )

    @property
    def totale(self) -> Decimal:
        """Cost of one period: unit price × quantity."""
        return self.importo * self.quantita

    def __repr__(self) -> str:
        return f"<ContrattoFornitore id={self.id} fornitore={self.fornitore!r} descrizione={self.descrizione!r}>"
