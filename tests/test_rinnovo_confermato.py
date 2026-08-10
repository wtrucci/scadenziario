"""
Tests for the manually-confirmed renewal flow, end to end through the route.

A contract WITHOUT auto-renewal stops generating at the first unbilled cycle
opening: that occurrence is the renewal the customer still has to confirm, and
nothing past it is known. Billing it IS the confirmation, and generation
resumes on its own — no field on the contract is written, so un-billing puts
the pending renewal straight back.

Billing an instalment inside a commitment is plain bookkeeping and must not
change what is generated beyond it.

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
from app.services.occorrenze import occorrenze_nel_periodo

SCADENZA = date(2026, 8, 24)
ORIZZONTE = (date(2020, 1, 1), date(2032, 12, 31))


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
        # Yearly licence to be confirmed each time: every occurrence is itself
        # a renewal (durata_impegno_mesi left empty).
        s = Servizio(
            cliente=cliente, descrizione="Licenza", tipo=TipoServizio.licenza,
            data_scadenza=SCADENZA, cadenza_mesi=12, rinnovo_automatico=False,
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
        r = self.client.post(
            f"/servizi/{self.servizio_id}/occorrenze/{data_occorrenza.isoformat()}/fatturato",
            data={"mese": data_occorrenza.strftime("%Y-%m"), "vista": "dashboard"},
        )
        self.assertEqual(r.status_code, 200, r.text)

    def _occorrenze(self) -> list[date]:
        db = self.SessionLocal()
        try:
            s = db.get(Servizio, self.servizio_id)
            return [o.data_occorrenza for o in occorrenze_nel_periodo(s, *ORIZZONTE)]
        finally:
            db.close()

    # --- tests -----------------------------------------------------------

    def test_si_ferma_al_rinnovo_pendente(self):
        self.assertEqual(self._occorrenze(), [SCADENZA])

    def test_fatturare_il_rinnovo_fa_ripartire_la_generazione(self):
        self._toggle(SCADENZA)
        self.assertEqual(self._occorrenze(), [SCADENZA, date(2027, 8, 24)])

    def test_conferme_successive_avanzano_di_un_anno_ciascuna(self):
        self._toggle(SCADENZA)
        self._toggle(date(2027, 8, 24))
        self.assertEqual(
            self._occorrenze(), [SCADENZA, date(2027, 8, 24), date(2028, 8, 24)])

    def test_smarcare_riporta_il_rinnovo_in_sospeso(self):
        """Un-billing needs no bookkeeping to unwind: the pending renewal is
        back simply because its state row says it is not billed."""
        self._toggle(SCADENZA)
        self._toggle(date(2027, 8, 24))
        self._toggle(date(2027, 8, 24))          # un-bill the last confirmation
        self.assertEqual(self._occorrenze(), [SCADENZA, date(2027, 8, 24)])

    def test_il_contratto_non_viene_mai_riscritto(self):
        """The whole flow is driven by billing state: data_scadenza itself must
        never move, so history and per-occurrence state stay attached to it."""
        self._toggle(SCADENZA)
        self._toggle(date(2027, 8, 24))
        db = self.SessionLocal()
        try:
            self.assertEqual(db.get(Servizio, self.servizio_id).data_scadenza, SCADENZA)
        finally:
            db.close()


class TestRateDentroImpegno(unittest.TestCase):
    """A commitment billed in instalments: only its opening is a renewal."""

    def setUp(self):
        self.engine = _make_engine()
        self.db = sessionmaker(bind=self.engine)()
        cliente = Cliente(nome="Acme", attivo=True)
        self.db.add(cliente)
        self.db.flush()
        # Yearly commitment invoiced monthly.
        self.s = Servizio(
            cliente=cliente, descrizione="Abbonamento", tipo=TipoServizio.abbonamento,
            data_scadenza=date(2026, 7, 1), cadenza_mesi=1, durata_impegno_mesi=12,
            rinnovo_automatico=False, importo=Decimal("4.00"), quantita=8,
            valuta="EUR", preavviso_giorni=30,
        )
        self.db.add(self.s)
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_senza_conferma_c_e_solo_il_rinnovo(self):
        occ = [o.data_occorrenza for o in occorrenze_nel_periodo(self.s, *ORIZZONTE)]
        self.assertEqual(occ, [date(2026, 7, 1)])

    def test_confermato_l_impegno_le_rate_sono_certe(self):
        from app.models.override_importo import OverrideImporto
        self.s.override_importi.append(
            OverrideImporto(data_occorrenza=date(2026, 7, 1), fatturato=True))
        occ = [o.data_occorrenza for o in occorrenze_nel_periodo(self.s, *ORIZZONTE)]
        self.assertEqual(len(occ), 13)                      # 12 instalments + next renewal
        self.assertEqual(occ[-1], date(2027, 7, 1))


if __name__ == "__main__":
    unittest.main()
