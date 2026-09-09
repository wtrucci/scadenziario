"""
Tests for editing a service straight from a dashboard row.

The dashboard shows occurrences, not services, and the pencil in each row opens
the service form carrying a "ritorno" URL, so that saving (or cancelling) comes
back to the month and filters the user was looking at. That URL arrives from the
browser, so it must never be able to redirect outside the application.

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
from app.routes.servizi import _destinazione


class TestDestinazione(unittest.TestCase):
    """_destinazione must accept only paths inside this application."""

    def test_percorso_interno_accettato(self):
        self.assertEqual(_destinazione("/?mese=2026-09&cliente=3"), "/?mese=2026-09&cliente=3")

    def test_vuoto_torna_alla_lista_servizi(self):
        self.assertEqual(_destinazione(""), "/servizi")

    def test_url_esterni_rifiutati(self):
        for velenoso in ("https://evil.example", "//evil.example", "/\\evil.example",
                         "javascript:alert(1)"):
            with self.subTest(velenoso=velenoso):
                self.assertEqual(_destinazione(velenoso), "/servizi")


class TestModificaDaDashboard(unittest.TestCase):

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
        servizio = Servizio(
            cliente_id=cliente.id,
            descrizione="Licenza test",
            tipo=TipoServizio.licenza,
            data_scadenza=date(2026, 9, 10),
            cadenza_mesi=12,
            importo=Decimal("100.00"),
            quantita=1,
            valuta="EUR",
            preavviso_giorni=30,
            rinnovo_automatico=True,
        )
        db.add(servizio)
        db.commit()
        self.cliente_id = cliente.id
        self.servizio_id = servizio.id
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

    def _payload(self, **extra) -> dict:
        dati = {
            "cliente_id": str(self.cliente_id),
            "descrizione": "Licenza test",
            "tipo": "licenza",
            "data_scadenza": "2026-09-10",
            "durata_impegno_mesi": "",
            "cadenza_mesi": "12",
            "importo": "100.00",
            "quantita": "1",
            "valuta": "EUR",
            "preavviso_giorni": "30",
            "rinnovo_automatico": "on",
        }
        dati.update(extra)
        return dati

    def test_riga_dashboard_ha_il_link_di_modifica_col_ritorno(self):
        r = self.client.get("/?mese=2026-09&cliente=" + str(self.cliente_id))
        self.assertEqual(r.status_code, 200)
        self.assertIn(f"/servizi/{self.servizio_id}/modifica?ritorno=", r.text)
        # The return URL keeps both the month and the active filter.
        self.assertIn("mese%3D2026-09", r.text)
        self.assertIn("cliente%3D" + str(self.cliente_id), r.text)

    def test_form_precompila_il_ritorno(self):
        r = self.client.get(
            f"/servizi/{self.servizio_id}/modifica", params={"ritorno": "/?mese=2026-09"}
        )
        self.assertIn('name="ritorno" value="/?mese=2026-09"', r.text)

    def test_salvataggio_torna_alla_dashboard(self):
        r = self.client.post(
            f"/servizi/{self.servizio_id}/modifica",
            data=self._payload(ritorno="/?mese=2026-09"),
            follow_redirects=False,
        )
        self.assertEqual(r.status_code, 303, r.text)
        self.assertEqual(r.headers["location"], "/?mese=2026-09")

    def test_salvataggio_senza_ritorno_va_alla_lista_servizi(self):
        r = self.client.post(
            f"/servizi/{self.servizio_id}/modifica",
            data=self._payload(),
            follow_redirects=False,
        )
        self.assertEqual(r.headers["location"], "/servizi")

    def test_ritorno_esterno_ignorato(self):
        r = self.client.post(
            f"/servizi/{self.servizio_id}/modifica",
            data=self._payload(ritorno="https://evil.example"),
            follow_redirects=False,
        )
        self.assertEqual(r.headers["location"], "/servizi")


if __name__ == "__main__":
    unittest.main()
