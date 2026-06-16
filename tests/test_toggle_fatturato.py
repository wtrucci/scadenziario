"""
Tests for the "fatturato" toggle endpoint, focused on the price-freezing rule:

Billing an occurrence must snapshot its effective price/quantity, so a later
change to the service price does not alter what was already billed. Un-billing
clears that snapshot so the occurrence tracks the service price again — unless
the row is a deliberate manual override, which must be preserved.

Run with:  python -m unittest discover -s tests
Uses an isolated in-memory SQLite database; the real DB is never touched.
"""
from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  (registers all tables on Base.metadata)
from app.database import Base
from app.models.cliente import Cliente
from app.models.enums import StatoServizio, TipoServizio
from app.models.override_importo import OverrideImporto
from app.models.servizio import Servizio
from app.services.occorrenze import occorrenze_nel_periodo

GEN = date(2026, 1, 10)
FEB = date(2026, 2, 10)


def _make_engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine


class TestToggleFatturato(unittest.TestCase):

    def setUp(self):
        from fastapi.testclient import TestClient

        from app.database import get_db
        from app.dependencies import require_login
        from app.main import app

        self.engine = _make_engine()
        self.SessionLocal = sessionmaker(bind=self.engine)

        db = self.SessionLocal()
        cliente = Cliente(nome="Acme", attivo=True)
        db.add(cliente)
        db.flush()
        s = Servizio(
            cliente=cliente, descrizione="Servizio", tipo=TipoServizio.abbonamento,
            data_inizio=GEN, data_fine=date(2026, 3, 10), cadenza_mesi=1,
            importo=Decimal("100.00"), quantita=1, valuta="EUR",
            preavviso_giorni=30, stato=StatoServizio.attivo,
        )
        db.add(s)
        db.commit()
        self.servizio_id = s.id
        db.close()

        def override_get_db():
            db = self.SessionLocal()
            try:
                yield db
            finally:
                db.close()

        self.app = app
        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[require_login] = lambda: SimpleNamespace(username="tester")
        self.client = TestClient(app)

    def tearDown(self):
        self.app.dependency_overrides.clear()
        self.engine.dispose()

    # --- helpers ---------------------------------------------------------

    def _toggle(self, data_occorrenza: date):
        url = f"/servizi/{self.servizio_id}/occorrenze/{data_occorrenza.isoformat()}/fatturato"
        r = self.client.post(url)
        self.assertEqual(r.status_code, 200)

    def _importo_occorrenza(self, data_occorrenza: date) -> Decimal:
        """Compute the effective unit price of an occurrence via the engine."""
        db = self.SessionLocal()
        try:
            s = db.get(Servizio, self.servizio_id)
            occ = occorrenze_nel_periodo(s, data_occorrenza, data_occorrenza)[0]
            return occ.importo
        finally:
            db.close()

    def _set_prezzo_servizio(self, nuovo: str):
        db = self.SessionLocal()
        try:
            db.get(Servizio, self.servizio_id).importo = Decimal(nuovo)
            db.commit()
        finally:
            db.close()

    def _get_stato(self, data_occorrenza: date) -> OverrideImporto | None:
        db = self.SessionLocal()
        try:
            return db.scalars(
                select(OverrideImporto)
                .where(OverrideImporto.servizio_id == self.servizio_id)
                .where(OverrideImporto.data_occorrenza == data_occorrenza)
            ).first()
        finally:
            db.close()

    # --- tests -----------------------------------------------------------

    def test_fatturato_congela_il_prezzo(self):
        """Bill at 100, raise the service to 120: the billed occurrence stays
        100, an unbilled one becomes 120."""
        self._toggle(GEN)                     # bill January (snapshot 100)
        self._set_prezzo_servizio("120.00")   # service price changes afterwards

        self.assertEqual(self._importo_occorrenza(GEN), Decimal("100.00"))  # frozen
        self.assertEqual(self._importo_occorrenza(FEB), Decimal("120.00"))  # tracks service

    def test_smarcatura_riattiva_il_prezzo_corrente(self):
        """Un-billing (no manual override) clears the snapshot: the occurrence
        tracks the service price again."""
        self._toggle(GEN)                     # bill (snapshot 100)
        self._set_prezzo_servizio("120.00")
        self._toggle(GEN)                     # un-bill -> snapshot cleared

        stato = self._get_stato(GEN)
        self.assertFalse(stato.fatturato)
        self.assertIsNone(stato.importo)
        self.assertIsNone(stato.quantita)
        self.assertIsNone(stato.fatturato_il)
        self.assertEqual(self._importo_occorrenza(GEN), Decimal("120.00"))

    def test_smarcatura_non_cancella_override_manuale(self):
        """Un-billing a deliberate manual override must NOT wipe the correction."""
        # Pre-existing manual override (e.g. a discount to 80) on January.
        db = self.SessionLocal()
        db.add(OverrideImporto(
            servizio_id=self.servizio_id, data_occorrenza=GEN,
            importo=Decimal("80.00"), quantita=1, override_manuale=True,
            note="Sconto concordato",
        ))
        db.commit()
        db.close()

        self._toggle(GEN)   # bill (snapshot keeps the 80 override)
        self._toggle(GEN)   # un-bill

        stato = self._get_stato(GEN)
        self.assertFalse(stato.fatturato)
        self.assertIsNone(stato.fatturato_il)
        # The manual override (price, flag, note) survives.
        self.assertTrue(stato.override_manuale)
        self.assertEqual(stato.importo, Decimal("80.00"))
        self.assertEqual(stato.note, "Sconto concordato")
        self.assertEqual(self._importo_occorrenza(GEN), Decimal("80.00"))


if __name__ == "__main__":
    unittest.main()
