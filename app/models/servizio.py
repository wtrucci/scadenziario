from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, Enum, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, utcnow
from app.models.enums import TipoServizio


class Servizio(Base):
    """
    A recurring billable service (a contract).

    Everything about time hangs off ONE date: ``data_scadenza``, the day the
    current billing cycle starts. For the overwhelmingly common contract —
    one invoice per cycle — that date IS the renewal date, and confirming a
    renewal simply moves it forward. The individual billable dates
    ("occorrenze") are NOT stored: they are computed on the fly by stepping
    ``cadenza_mesi`` months from ``data_scadenza`` (see
    app/services/occorrenze.py).

    ``durata_impegno_mesi`` is what the customer is committed to. Leave it
    empty and the commitment is exactly one billing, so every occurrence is a
    renewal (37 of 38 real contracts). Set it when a commitment is billed in
    instalments — a yearly subscription invoiced monthly has cadenza_mesi=1
    and durata_impegno_mesi=12: the twelve instalments are certain, and only
    what follows needs the customer's go-ahead.

    ``rinnovo_automatico`` means the cycle repeats without asking: occurrences
    keep being generated indefinitely. Without it, generation stops one
    occurrence past the commitment — the "renewal proposal" — because nothing
    beyond it is known until the customer confirms.

    A previous model derived all of this from ``data_inizio`` plus a duration
    and a cadence. It could express the same contract in two ways that meant
    different things, and mis-entering one silently scheduled invoices inside
    a period the customer had already paid for. ``data_inizio`` survives only
    as a note of when the relationship began; nothing computes from it.

    The per-occurrence total defaults to ``quantita * importo`` (see the
    ``totale`` property), unless an OverrideImporto exists for that specific
    occurrence date.
    """
    __tablename__ = "servizi"

    id: Mapped[int] = mapped_column(primary_key=True)
    cliente_id: Mapped[int] = mapped_column(ForeignKey("clienti.id"))

    descrizione: Mapped[str] = mapped_column(String(300))
    tipo: Mapped[TipoServizio] = mapped_column(
        Enum(TipoServizio, native_enum=False, length=20)
    )

    # The single date everything is computed from: start of the current billing
    # cycle, and the day-of-month every occurrence falls on. Indexed because the
    # "which contracts fall in this month" query filters on it.
    data_scadenza: Mapped[date] = mapped_column(Date, index=True)

    # Purely informational: when the service started, for reference in the UI.
    # Deliberately drives NO calculation (see the class docstring).
    data_inizio: Mapped[date | None] = mapped_column(Date, nullable=True)

    # If True the cycle repeats without needing confirmation, so occurrences are
    # generated indefinitely. If False, generation stops one occurrence past the
    # commitment: that occurrence is the renewal proposal, and billing it is the
    # customer's confirmation (it moves data_scadenza forward).
    rinnovo_automatico: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Billing cadence in months (1 = monthly, 3 = quarterly, 12 = yearly, ...).
    cadenza_mesi: Mapped[int] = mapped_column(Integer)

    # How many months the customer is committed to. NULL = one billing per
    # commitment (every occurrence is a renewal), which is the common case.
    # Set it only when a commitment is billed in instalments.
    durata_impegno_mesi: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Default unit price of each occurrence. Occurrence total = quantita * importo
    # (see the `totale` property), unless overridden per date via OverrideImporto.
    importo: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    quantita: Mapped[int] = mapped_column(Integer, default=1)
    valuta: Mapped[str] = mapped_column(String(3), default="EUR")

    preavviso_giorni: Mapped[int] = mapped_column(Integer, default=30)

    # The only MANUAL lifecycle flag left: the customer cancelled the contract
    # early, which dates alone can't tell you. Everything else shown as "Stato"
    # (attivo/in_scadenza/scaduto) is computed from dates — see stato_contratto
    # in app/services/occorrenze.py. disdetto always wins over the computed
    # states and, when set, occurrences stop being flagged "da_fatturare" /
    # "in_scadenza" (see _stato_visivo in the same module).
    disdetto: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Optional: the colleague/contact to invoice when different from the end customer.
    referente: Mapped[str | None] = mapped_column(String(200))
    # Optional: serial number of the licensed/covered device or software.
    # Requested capacity was "at least 70 characters"; 100 gives headroom.
    numero_seriale: Mapped[str | None] = mapped_column(String(100))
    # Optional: which site the service is installed at, for customers with
    # multiple locations (multisede).
    luogo_installazione: Mapped[str | None] = mapped_column(String(200))
    note: Mapped[str | None] = mapped_column(Text)

    creato_il: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    aggiornato_il: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    cliente: Mapped[Cliente] = relationship(back_populates="servizi")

    # cascade="all, delete-orphan": notification logs are meaningless without their service.
    notifiche: Mapped[list[NotificaLog]] = relationship(
        back_populates="servizio", cascade="all, delete-orphan"
    )
    # Per-occurrence price overrides. Deleted together with the service.
    override_importi: Mapped[list[OverrideImporto]] = relationship(
        back_populates="servizio", cascade="all, delete-orphan"
    )

    @property
    def totale(self) -> Decimal:
        """Default total for one occurrence: unit price × quantity.

        This is the default used when no OverrideImporto applies to a given
        occurrence date."""
        return self.importo * self.quantita

    def __repr__(self) -> str:
        return (
            f"<Servizio id={self.id} descrizione={self.descrizione!r}"
            f" scadenza={self.data_scadenza} ogni {self.cadenza_mesi}m>"
        )
