"""
Tests for the "this customer already has a service like this" warning.

Unlike customers, duplicate-looking services are often legitimate: one customer
has fifteen "Sentinel One" contracts, one per end customer, told apart by the
referente. So the warning must be confirmable, and it must NOT fire when the
referente differs — otherwise it would fire on all fifteen and teach the user
to click through it.

Run with:  python -m unittest discover -s tests
Uses an isolated in-memory SQLite database; the real DB is never touched.
"""
from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  (registers all tables on Base.metadata)
from app.database import Base
from app.models.cliente import Cliente
from app.models.enums import TipoServizio
from app.models.servizio import Servizio


class TestServiziSimili(unittest.TestCase):

    def setUp(self):
        from fastapi.testclient import TestClient

        from app.database import get_db
        from app.dependencies import require_login
        from app.main import app

        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine)

        db = self.SessionLocal()
        cliente = Cliente(nome="Beta Memory", attivo=True)
        altro = Cliente(nome="Panealba", attivo=True)
        db.add_all([cliente, altro])
        db.flush()
        self.cliente_id = cliente.id
        self.altro_id = altro.id
        db.add(Servizio(
            cliente_id=cliente.id, descrizione="Sentinel One", tipo=TipoServizio.abbonamento,
            data_scadenza=date(2026, 9, 21), cadenza_mesi=1, durata_impegno_mesi=12,
            importo=Decimal("2.75"), quantita=12, valuta="EUR", preavviso_giorni=30,
            referente="Mecsider",
        ))
        db.commit()
        self.esistente_id = db.scalar(select(Servizio.id))
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

    def _quanti(self) -> int:
        db = self.SessionLocal()
        try:
            return db.scalar(select(func.count()).select_from(Servizio))
        finally:
            db.close()

    def _crea(self, **extra):
        dati = {
            "cliente_id": str(self.cliente_id),
            "descrizione": "Sentinel One",
            "tipo": "abbonamento",
            "data_scadenza": "2026-10-15",
            "durata_impegno_mesi": "12",
            "cadenza_mesi": "1",
            "importo": "2.75",
            "quantita": "5",
            "valuta": "EUR",
            "preavviso_giorni": "30",
            "referente": "Mecsider",
        }
        dati.update(extra)
        return self.client.post("/servizi", data=dati, follow_redirects=False)

    # --- the warning fires -------------------------------------------------

    def test_stesso_cliente_descrizione_e_referente_avvisa(self):
        r = self._crea()
        self.assertEqual(r.status_code, 422)
        self.assertIn("ha già", r.text)
        self.assertEqual(self._quanti(), 1)

    def test_avviso_mostra_come_distinguerli(self):
        r = self._crea()
        # The existing contract's own date, so the user can tell which is which.
        self.assertIn("21/09/2026", r.text)

    def test_maiuscole_e_spazi_non_ingannano(self):
        r = self._crea(descrizione="  sentinel   ONE ", referente="mecsider")
        self.assertEqual(r.status_code, 422)
        self.assertEqual(self._quanti(), 1)

    def test_dopo_conferma_viene_salvato(self):
        r = self._crea(conferma_simili="1")
        self.assertEqual(r.status_code, 303, r.text)
        self.assertEqual(self._quanti(), 2)

    # --- the warning stays quiet -------------------------------------------

    def test_referente_diverso_non_avvisa(self):
        # The fifteen "Sentinel One" of one customer: different end customer,
        # different contract, no warning.
        r = self._crea(referente="Perino Infissi")
        self.assertEqual(r.status_code, 303, r.text)
        self.assertEqual(self._quanti(), 2)

    def test_altro_cliente_non_avvisa(self):
        r = self._crea(cliente_id=str(self.altro_id))
        self.assertEqual(r.status_code, 303, r.text)

    def test_descrizione_diversa_non_avvisa(self):
        r = self._crea(descrizione="Fortigate 40F UTP")
        self.assertEqual(r.status_code, 303, r.text)

    # --- editing -----------------------------------------------------------

    def test_modifica_senza_toccare_nulla_non_avvisa(self):
        r = self.client.post(
            f"/servizi/{self.esistente_id}/modifica",
            data={
                "cliente_id": str(self.cliente_id), "descrizione": "Sentinel One",
                "tipo": "abbonamento", "data_scadenza": "2026-09-21",
                "durata_impegno_mesi": "12", "cadenza_mesi": "1", "importo": "2.75",
                "quantita": "12", "valuta": "EUR", "preavviso_giorni": "30",
                "referente": "Mecsider",
            },
            follow_redirects=False,
        )
        self.assertEqual(r.status_code, 303, r.text)


if __name__ == "__main__":
    unittest.main()
