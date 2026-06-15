from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, Enum, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, utcnow
from app.models.enums import Ricorrenza, StatoServizio, TipoServizio


class Servizio(Base):
    __tablename__ = "servizi"

    id: Mapped[int] = mapped_column(primary_key=True)
    cliente_id: Mapped[int] = mapped_column(ForeignKey("clienti.id"))

    descrizione: Mapped[str] = mapped_column(String(300))
    tipo: Mapped[TipoServizio] = mapped_column(
        Enum(TipoServizio, native_enum=False, length=20)
    )
    data_scadenza: Mapped[date] = mapped_column(Date)

    # Unit price. Total = quantita * importo (see `totale` property below).
    importo: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    quantita: Mapped[int] = mapped_column(Integer, default=1)
    valuta: Mapped[str] = mapped_column(String(3), default="EUR")

    ricorrenza: Mapped[Ricorrenza] = mapped_column(
        Enum(Ricorrenza, native_enum=False, length=20)
    )
    preavviso_giorni: Mapped[int] = mapped_column(Integer, default=30)
    stato: Mapped[StatoServizio] = mapped_column(
        Enum(StatoServizio, native_enum=False, length=20), default=StatoServizio.attivo
    )

    # When True, the scheduler will advance data_scadenza by one recurrence period
    # at expiry and write a RinnovoLog entry. server_default="0" ensures existing
    # rows get False when this column is added via ALTER TABLE migration.
    rinnovo_automatico: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0"
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
    # Renewal history. Deleted together with the service (billing context gone).
    rinnovi: Mapped[list[RinnovoLog]] = relationship(
        back_populates="servizio", cascade="all, delete-orphan"
    )

    @property
    def totale(self) -> Decimal:
        """Total price for this service line: unit price × quantity."""
        return self.importo * self.quantita

    def __repr__(self) -> str:
        return f"<Servizio id={self.id} descrizione={self.descrizione!r} scadenza={self.data_scadenza}>"
