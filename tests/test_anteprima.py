"""
Tests for the service sheet (GET /servizi/{id}/anteprima): the read-only
summary that opens in a dialog when a table row is clicked.

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


class TestAnteprima(unittest.TestCase):

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
        cliente = Cliente(nome="Acme", attivo=True)
        db.add(cliente)
        db.flush()
        s = Servizio(
            cliente_id=cliente.id, descrizione="Firewall gestito", tipo=TipoServizio.contratto,
            data_scadenza=date(2026, 3, 15), cadenza_mesi=1, importo=Decimal("2.75"),
            quantita=30, valuta="EUR", preavviso_giorni=30, rinnovo_automatico=True,
            referente="3T", numero_seriale="FGT60F-XYZ", luogo_installazione="Alba",
            note="Sconto concordato\nrinnovo a voce",
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

    def test_mostra_tutti_i_dati(self):
        h = self.client.get(f"/servizi/{self.servizio_id}/anteprima").text
        for atteso in ("Firewall gestito", "Acme", "3T", "Alba", "FGT60F-XYZ",
                       "mensile", "82.50 EUR",       # 30 × 2.75 per occurrence
                       "990.00 EUR",                  # × 12 a year
                       "Sconto concordato", "Rinnovo automatico"):
            with self.subTest(atteso=atteso):
                self.assertIn(atteso, h)

    def test_modifica_riporta_alla_pagina_di_partenza(self):
        h = self.client.get(f"/servizi/{self.servizio_id}/anteprima",
                            params={"ritorno": "/servizi?cliente=1"}).text
        self.assertIn(f"/servizi/{self.servizio_id}/modifica?ritorno=/servizi%3Fcliente%3D1", h)

    def test_ritorno_esterno_ignorato(self):
        h = self.client.get(f"/servizi/{self.servizio_id}/anteprima",
                            params={"ritorno": "https://evil.example"}).text
        self.assertIn(f"/servizi/{self.servizio_id}/modifica?ritorno=/servizi", h)
        self.assertNotIn("evil.example", h)

    def test_servizio_inesistente(self):
        self.assertEqual(self.client.get("/servizi/9999/anteprima").status_code, 404)

    def test_elimina_solo_se_richiesto(self):
        # Only the services list asks for it (?elimina=1): elsewhere a row is
        # an occurrence or a renewal, not the contract.
        senza = self.client.get(f"/servizi/{self.servizio_id}/anteprima").text
        self.assertNotIn("hx-delete", senza)
        con = self.client.get(f"/servizi/{self.servizio_id}/anteprima", params={"elimina": "1"}).text
        self.assertIn(f'hx-delete="/servizi/{self.servizio_id}?da=scheda"', con)
        self.assertIn("hx-confirm=", con)

    def test_elimina_dalla_scheda_ricarica_la_pagina(self):
        r = self.client.delete(f"/servizi/{self.servizio_id}", params={"da": "scheda"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers.get("HX-Refresh"), "true")
        self.assertEqual(self.client.get(f"/servizi/{self.servizio_id}/anteprima").status_code, 404)

    def test_lista_servizi_senza_colonna_azioni(self):
        h = self.client.get("/servizi").text
        intestazione = h[h.index("<thead>"):h.index("</thead>")]
        self.assertNotIn("Azioni", intestazione)
        self.assertNotIn('class="col-actions"', h)
        # The column picker survives, on the last header.
        self.assertIn("col-toggle-btn", h)

    def test_le_righe_dei_servizi_aprono_la_scheda(self):
        h = self.client.get("/servizi").text
        self.assertIn(f'data-anteprima="/servizi/{self.servizio_id}/anteprima?elimina=1"', h)
        self.assertIn('id="anteprima-dialog"', h)


if __name__ == "__main__":
    unittest.main()
