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

    A service is valid from ``data_inizio`` to ``data_fine`` (inclusive) and is
    billed every ``cadenza_mesi`` months. The individual billable dates
    ("occorrenze") are NOT stored: they are computed on the fly from
    data_inizio + cadenza_mesi steps until the EFFECTIVE end date (see
    ``data_fine_effettiva`` in app/services/occorrenze.py). A single payment is
    modelled as durata_mesi <= cadenza_mesi (so only the first occurrence ever
    falls within the contract period).

    The per-occurrence total defaults to ``quantita * importo`` (see the
    ``totale`` property), unless an OverrideImporto exists for that specific
    occurrence date.

    ``cadenza_mesi`` (how often it's billed) is independent from
    ``durata_mesi`` (how long the contract runs): e.g. a monthly-billed
    12-month contract has cadenza_mesi=1, durata_mesi=12, and produces 12
    occurrences. ``data_fine`` is derived from data_inizio + durata_mesi (see
    ``calcola_data_fine``) and stored, so it stays a plain indexable column for
    the SQL "which contracts overlap this month" filter; it is NOT meant to be
    edited directly. If ``rinnovo_automatico`` is set, the contract rolls
    forward by another durata_mesi block whenever it would otherwise have
    expired — computed on the fly (see ``data_fine_effettiva``), nothing here
    is updated by a background job.
    """
    __tablename__ = "servizi"

    id: Mapped[int] = mapped_column(primary_key=True)
    cliente_id: Mapped[int] = mapped_column(ForeignKey("clienti.id"))

    descrizione: Mapped[str] = mapped_column(String(300))
    tipo: Mapped[TipoServizio] = mapped_column(
        Enum(TipoServizio, native_enum=False, length=20)
    )

    # Contract period: the service is valid from data_inizio to data_fine
    # (inclusive). Each occurrence falls on the day-of-month of data_inizio.
    # data_fine is derived from data_inizio + durata_mesi, not entered directly
    # (see the class docstring).
    data_inizio: Mapped[date] = mapped_column(Date)
    data_fine: Mapped[date] = mapped_column(Date)

    # Contract duration in months, used to compute data_fine and, together with
    # rinnovo_automatico, to roll it forward on renewal. Nullable only for rows
    # that predate this field; new/edited services always set it.
    durata_mesi: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # If True, the contract auto-renews for another durata_mesi block each time
    # it expires (see the class docstring and data_fine_effettiva).
    rinnovo_automatico: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Billing cadence in months (1 = monthly, 3 = quarterly, 6 = half-yearly,
    # 12 = yearly, ...). Replaces the old `ricorrenza` enum; there is no longer
    # a "one-off" recurrence (use data_fine == data_inizio for a single payment).
    cadenza_mesi: Mapped[int] = mapped_column(Integer)

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
            f" {self.data_inizio}..{self.data_fine} ogni {self.cadenza_mesi}m>"
        )
