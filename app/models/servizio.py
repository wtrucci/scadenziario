from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, Enum, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, utcnow
from app.models.enums import StatoServizio, TipoServizio


class Servizio(Base):
    """
    A recurring billable service (a contract).

    A service is valid from ``data_inizio`` to ``data_fine`` (inclusive) and is
    billed every ``cadenza_mesi`` months. The individual billable dates
    ("occorrenze") are NOT stored: they are computed on the fly from
    data_inizio + cadenza_mesi steps until data_fine. A single payment is
    modelled as data_fine == data_inizio.

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

    # Contract period: the service is valid from data_inizio to data_fine
    # (inclusive). Each occurrence falls on the day-of-month of data_inizio.
    data_inizio: Mapped[date] = mapped_column(Date)
    data_fine: Mapped[date] = mapped_column(Date)

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
    stato: Mapped[StatoServizio] = mapped_column(
        Enum(StatoServizio, native_enum=False, length=20), default=StatoServizio.attivo
    )

    # Optional: the colleague/contact to invoice when different from the end customer.
    referente: Mapped[str | None] = mapped_column(String(200))
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
