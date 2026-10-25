from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, utcnow


class OffertaRinnovo(Base):
    """
    "The renewal offer for this renewal has been sent to the customer."

    A contract without automatic renewal stops at a renewal PROPOSAL: the
    customer has to say yes (see app/services/occorrenze.py). Before that
    happens somebody has to send them an offer, and this table records that it
    was done — see app/services/offerte.py for the page built on it.

    The row belongs to ONE renewal, identified by its date, not to the
    contract: once the renewal is confirmed the next proposal is a different
    date, which has no row yet and so starts again as "to send". Nothing has to
    be reset by hand.

    A row exists only while the offer counts as sent: un-marking it deletes the
    row. It is kept apart from OverrideImporto on purpose — that table is about
    billing an occurrence, this one about a step that comes before it.

    Deleted together with the parent service (cascade on the Servizio side).
    """
    __tablename__ = "offerte_rinnovo"
    __table_args__ = (
        UniqueConstraint("servizio_id", "data_rinnovo", name="uq_offerta_servizio_data"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    servizio_id: Mapped[int] = mapped_column(ForeignKey("servizi.id"))

    # The renewal the offer is about (an occurrence date that opens a cycle).
    data_rinnovo: Mapped[date] = mapped_column(Date)

    # When the operator marked the offer as sent.
    inviata_il: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    servizio: Mapped[Servizio] = relationship(back_populates="offerte_rinnovo")

    def __repr__(self) -> str:
        return (
            f"<OffertaRinnovo id={self.id} servizio_id={self.servizio_id}"
            f" data={self.data_rinnovo}>"
        )
