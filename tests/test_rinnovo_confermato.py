"""
Tests for the manually-confirmed renewal flow (routes/servizi.py):

Billing the "renewal proposal" occurrence of a contract WITHOUT auto-renewal
(the one occurrence past data_fine — see occorrenze_nel_periodo) is the
client's confirmation: it extends the contract by one renewal block, moving
data_fine forward. Un-billing that same occurrence retracts the block (the
billing click was a mistake). Billing an occurrence WITHIN the contract
period is plain bookkeeping and must not move any date.

Run with:  python -m unittest discover -s tests
Uses an isolated in-memory SQLite database; the real DB is never touched.
"""
from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  (registers all tables on Base.metadata)
from app.database import Base
from app.models.cliente import Cliente
from app.models.enums import TipoServizio
from app.models.servizio import Servizio


def _make_engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine


class TestRinnovoConfermato(unittest.TestCase):

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
        # Yearly license, no auto-renewal: period 2025-08-24 .. 2026-08-23,
        # renewal proposal at 2026-08-24.
        s = Servizio(
            cliente=cliente, descrizione="Licenza", tipo=TipoServizio.licenza,
            data_inizio=date(2025, 8, 24), data_fine=date(2026, 8, 23),
            durata_mesi=12, cadenza_mesi=12, rinnovo_automatico=False,
            importo=Decimal("100.00"), quantita=1, valuta="EUR",
            preavviso_giorni=30,
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

    def _servizio(self) -> Servizio:
        db = self.SessionLocal()
        try:
            return db.get(Servizio, self.servizio_id)
        finally:
            db.close()

    # --- tests -----------------------------------------------------------

    def test_fatturare_la_proposta_estende_il_contratto(self):
        self._toggle(date(2026, 8, 24))  # bill the renewal proposal

        s = self._servizio()
        self.assertEqual(s.durata_mesi, 24)
        self.assertEqual(s.data_fine, date(2027, 8, 23))
        # The block length is remembered explicitly, so the NEXT confirmation
        # extends by another 12 months, not by the accumulated 24.
        self.assertEqual(s.durata_rinnovo_mesi, 12)

    def test_conferme_successive_estendono_di_un_blocco_ciascuna(self):
        self._toggle(date(2026, 8, 24))
        self._toggle(date(2027, 8, 24))  # next year's proposal

        s = self._servizio()
        self.assertEqual(s.durata_mesi, 36)
        self.assertEqual(s.data_fine, date(2028, 8, 23))

    def test_smarcare_la_proposta_ritira_il_blocco(self):
        self._toggle(date(2026, 8, 24))  # bill (extends to 24 months)
        self._toggle(date(2026, 8, 24))  # un-bill: it was a mistake

        s = self._servizio()
        self.assertEqual(s.durata_mesi, 12)
        self.assertEqual(s.data_fine, date(2026, 8, 23))

    def test_fatturare_dentro_il_periodo_non_muove_le_date(self):
        self._toggle(date(2025, 8, 24))  # the occurrence within the period

        s = self._servizio()
        self.assertEqual(s.durata_mesi, 12)
        self.assertEqual(s.data_fine, date(2026, 8, 23))
        self.assertIsNone(s.durata_rinnovo_mesi)

    def test_smarcare_dentro_il_periodo_non_muove_le_date(self):
        self._toggle(date(2025, 8, 24))
        self._toggle(date(2025, 8, 24))

        s = self._servizio()
        self.assertEqual(s.durata_mesi, 12)
        self.assertEqual(s.data_fine, date(2026, 8, 23))


if __name__ == "__main__":
    unittest.main()
