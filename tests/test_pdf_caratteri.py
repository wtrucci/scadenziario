"""
Tests for text the core PDF fonts cannot encode.

Helvetica in a PDF only covers Latin-1, and fpdf2 refuses the whole document
over a single character outside it. A referente typed as "Atelier dell’auto"
(curly apostrophe, as phones and Macs insert it) made the riepilogo PDF of
that customer an HTTP 500 — for every month containing that service.

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
from app.services.pdf import _latin1


class TestLatin1(unittest.TestCase):

    def test_caratteri_tipografici_diventano_semplici(self):
        self.assertEqual(_latin1("Atelier dell’auto"), "Atelier dell'auto")
        self.assertEqual(_latin1("“Studio” – Rossi — sede…"), '"Studio" - Rossi - sede...')

    def test_accenti_italiani_restano(self):
        self.assertEqual(_latin1("Società Àlfa più"), "Società Àlfa più")

    def test_euro_diventa_codice(self):
        self.assertEqual(_latin1("10 €"), "10 EUR")

    def test_il_resto_diventa_punto_interrogativo(self):
        self.assertEqual(_latin1("Zhōu 周"), "Zh?u ?")


class TestRiepilogoPdfConApostrofo(unittest.TestCase):

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
        db.add(cliente)
        db.flush()
        self.cliente_id = cliente.id
        db.add(Servizio(
            cliente_id=cliente.id, descrizione="Fortigate 40F UTP — “firewall”",
            tipo=TipoServizio.licenza, data_scadenza=date(2027, 7, 18), cadenza_mesi=12,
            importo=Decimal("400"), quantita=1, valuta="EUR", preavviso_giorni=30,
            referente="Atelier dell’auto",
        ))
        db.commit()
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

    def test_il_pdf_del_riepilogo_si_genera(self):
        r = self.client.get(
            "/riepilogo/export/pdf", params={"cliente_id": self.cliente_id, "mese": "2027-07"}
        )
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.content.startswith(b"%PDF"))


if __name__ == "__main__":
    unittest.main()
