"""
Tests for the free-text search on the services page (GET /servizi?q=...).

What matters here: the search looks into the fields you actually have in hand
(serial number, site, referente, description, customer name), every word must
match somewhere so several words narrow the result instead of widening it, and
searching combines with the existing dropdown filters rather than replacing
them.

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


class TestRicercaServizi(unittest.TestCase):

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
        beta = Cliente(nome="Beta Memory", attivo=True)
        altro = Cliente(nome="Panealba", attivo=True)
        db.add_all([beta, altro])
        db.flush()
        self.beta_id = beta.id

        def servizio(cliente, descrizione, referente=None, seriale=None, sede=None):
            return Servizio(
                cliente_id=cliente.id, descrizione=descrizione, tipo=TipoServizio.abbonamento,
                data_scadenza=date(2026, 9, 21), cadenza_mesi=12, importo=Decimal("2.75"),
                quantita=1, valuta="EUR", preavviso_giorni=30, referente=referente,
                numero_seriale=seriale, luogo_installazione=sede,
            )

        db.add_all([
            servizio(beta, "Sentinel One", referente="Mecsider"),
            servizio(beta, "Sentinel One", referente="Perino Infissi"),
            servizio(beta, "Fortigate 40F UTP", referente="Mecsider",
                     seriale="FGT40FTK21023087", sede="Magazzino"),
            servizio(altro, "Fortianalyzer"),
        ])
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

    def _descrizioni(self, **params) -> list[str]:
        """The (descrizione, referente) pairs listed by the page, in order."""
        import re
        r = self.client.get("/servizi", params=params)
        self.assertEqual(r.status_code, 200)
        corpo = r.text
        if "<tbody>" not in corpo:
            return []
        corpo = corpo[corpo.index("<tbody>"):corpo.index("</tbody>")]
        righe = re.findall(r"<tr[^>]*>(.*?)</tr>", corpo, re.S)
        risultati = []
        for riga in righe:
            celle = re.findall(r"<td[^>]*>(.*?)</td>", riga, re.S)
            testo = [re.sub(r"<[^>]+>|\s+", " ", c).strip() for c in celle]
            risultati.append(f"{testo[3]} / {testo[1]}")
        return risultati

    def test_senza_ricerca_ci_sono_tutti(self):
        self.assertEqual(len(self._descrizioni()), 4)

    def test_cerca_per_seriale(self):
        self.assertEqual(self._descrizioni(q="FGT40FTK21023087"),
                         ["Fortigate 40F UTP / Mecsider"])

    def test_cerca_per_seriale_parziale_e_minuscolo(self):
        self.assertEqual(len(self._descrizioni(q="fgt40")), 1)

    def test_cerca_per_referente(self):
        self.assertEqual(len(self._descrizioni(q="perino")), 1)

    def test_cerca_per_nome_cliente(self):
        self.assertEqual(len(self._descrizioni(q="panealba")), 1)

    def test_cerca_per_sede(self):
        self.assertEqual(len(self._descrizioni(q="magazzino")), 1)

    def test_piu_parole_restringono(self):
        # Both words must match somewhere: this is the row that has both.
        self.assertEqual(self._descrizioni(q="perino sentinel"),
                         ["Sentinel One / Perino Infissi"])

    def test_ordine_delle_parole_indifferente(self):
        self.assertEqual(self._descrizioni(q="sentinel perino"),
                         self._descrizioni(q="perino sentinel"))

    def test_ricerca_senza_risultati(self):
        self.assertEqual(self._descrizioni(q="inesistente"), [])

    def test_ricerca_e_filtri_si_combinano(self):
        # Search alone: two Sentinel One. With the customer filter of the other
        # customer: none, instead of the filter being ignored.
        self.assertEqual(len(self._descrizioni(q="sentinel")), 2)
        self.assertEqual(len(self._descrizioni(q="sentinel", referente="Mecsider")), 1)

    def test_spazi_soli_non_filtrano_nulla(self):
        self.assertEqual(len(self._descrizioni(q="   ")), 4)


if __name__ == "__main__":
    unittest.main()
