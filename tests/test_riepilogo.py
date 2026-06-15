"""
Tests for the dashboard / billing-summary logic and routes.

Covers the three things that carry real business risk:
- month filtering (the half-open date range, including its boundaries),
- the "active services only" filter used by the billing summary,
- grouping by customer and the subtotal / grand-total sums.

Run with:  python -m unittest discover -s tests
Uses an isolated in-memory SQLite database; the real DB is never touched.
"""
from __future__ import annotations

import csv
import io
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
from app.models.enums import Ricorrenza, StatoServizio, TipoServizio
from app.models.servizio import Servizio
from app.services import periodi, riepilogo


def _make_engine():
    """A fresh in-memory SQLite engine, shared across connections via StaticPool
    so the FastAPI TestClient and the test code see the same data."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine


def _add_servizio(
    db, cliente, descrizione, scadenza, importo, *,
    quantita=1, stato=StatoServizio.attivo, referente=None,
):
    s = Servizio(
        cliente=cliente,
        descrizione=descrizione,
        tipo=TipoServizio.abbonamento,
        data_scadenza=scadenza,
        importo=Decimal(importo),
        quantita=quantita,
        valuta="EUR",
        ricorrenza=Ricorrenza.annuale,
        preavviso_giorni=30,
        stato=stato,
        referente=referente,
    )
    db.add(s)
    return s


class TestLogicaRiepilogo(unittest.TestCase):
    """Unit tests for the service-layer functions."""

    def setUp(self):
        self.engine = _make_engine()
        self.db = sessionmaker(bind=self.engine)()
        self.acme = Cliente(nome="Acme", attivo=True)
        self.beta = Cliente(nome="Beta", attivo=True)
        self.db.add_all([self.acme, self.beta])
        self.db.flush()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_filtro_mese_e_confini(self):
        """Only services expiring within the month are returned; the last day of
        the month is included and the first day of the next month is excluded."""
        _add_servizio(self.db, self.acme, "Dentro inizio", date(2026, 12, 1), "10")
        _add_servizio(self.db, self.acme, "Dentro fine", date(2026, 12, 31), "10")
        _add_servizio(self.db, self.acme, "Mese prima", date(2026, 11, 30), "10")
        _add_servizio(self.db, self.acme, "Mese dopo", date(2027, 1, 1), "10")
        self.db.flush()

        servizi = riepilogo.servizi_del_mese(self.db, date(2026, 12, 1))
        descrizioni = {s.descrizione for s in servizi}
        self.assertEqual(descrizioni, {"Dentro inizio", "Dentro fine"})

    def test_solo_attivi(self):
        """The billing summary must exclude disdetti and rinnovati."""
        _add_servizio(self.db, self.acme, "Attivo", date(2026, 12, 10), "10",
                      stato=StatoServizio.attivo)
        _add_servizio(self.db, self.acme, "Disdetto", date(2026, 12, 11), "10",
                      stato=StatoServizio.disdetto)
        _add_servizio(self.db, self.acme, "Rinnovato", date(2026, 12, 12), "10",
                      stato=StatoServizio.rinnovato)
        _add_servizio(self.db, self.acme, "Scaduto", date(2026, 12, 13), "10",
                      stato=StatoServizio.scaduto)
        self.db.flush()

        tutti = riepilogo.servizi_del_mese(self.db, date(2026, 12, 1))
        self.assertEqual(len(tutti), 4)

        attivi = riepilogo.servizi_del_mese(self.db, date(2026, 12, 1), solo_attivi=True)
        self.assertEqual([s.descrizione for s in attivi], ["Attivo"])

    def test_raggruppamento_e_somme(self):
        """Grouping by customer, per-line totals (qty x unit price), per-customer
        subtotals and the grand total."""
        # Acme: 2 x 10.00 = 20.00 ; 1 x 5.50 = 5.50  -> subtotal 25.50
        _add_servizio(self.db, self.acme, "Licenze", date(2026, 12, 5), "10.00", quantita=2)
        _add_servizio(self.db, self.acme, "Dominio", date(2026, 12, 6), "5.50", quantita=1)
        # Beta: 3 x 100.00 = 300.00 -> subtotal 300.00
        _add_servizio(self.db, self.beta, "Hosting", date(2026, 12, 7), "100.00", quantita=3)
        self.db.flush()

        servizi = riepilogo.servizi_del_mese(self.db, date(2026, 12, 1), solo_attivi=True)
        gruppi = riepilogo.raggruppa_per_cliente(servizi)

        # Sorted by customer name: Acme before Beta.
        self.assertEqual([g.cliente.nome for g in gruppi], ["Acme", "Beta"])
        self.assertEqual(len(gruppi[0].servizi), 2)
        self.assertEqual(gruppi[0].subtotale, Decimal("25.50"))
        self.assertEqual(gruppi[1].subtotale, Decimal("300.00"))

        self.assertEqual(riepilogo.totale_complessivo(gruppi), Decimal("325.50"))

    def test_totale_mese_vuoto(self):
        """No services -> empty groups and a zero grand total."""
        gruppi = riepilogo.raggruppa_per_cliente(
            riepilogo.servizi_del_mese(self.db, date(2026, 12, 1), solo_attivi=True)
        )
        self.assertEqual(gruppi, [])
        self.assertEqual(riepilogo.totale_complessivo(gruppi), Decimal("0"))


class TestPeriodi(unittest.TestCase):
    """Unit tests for month navigation helpers."""

    def test_parse_mese_valido(self):
        self.assertEqual(periodi.parse_mese("2026-03"), date(2026, 3, 1))

    def test_parse_mese_malformato_torna_al_corrente(self):
        atteso = periodi.mese_corrente()
        self.assertEqual(periodi.parse_mese("non-valido"), atteso)
        self.assertEqual(periodi.parse_mese(None), atteso)

    def test_mese_successivo_e_precedente_cambio_anno(self):
        self.assertEqual(periodi.mese_successivo(date(2026, 12, 1)), date(2027, 1, 1))
        self.assertEqual(periodi.mese_precedente(date(2026, 1, 1)), date(2025, 12, 1))

    def test_etichetta_e_chiave(self):
        self.assertEqual(periodi.etichetta_mese(date(2026, 12, 1)), "Dicembre 2026")
        self.assertEqual(periodi.chiave_mese(date(2026, 3, 1)), "2026-03")


class TestRotte(unittest.TestCase):
    """Route-level tests: dashboard, summary page and CSV export.

    Authentication and the DB session are replaced with overrides so the routes
    can be exercised against the in-memory database without a real login."""

    def setUp(self):
        from fastapi.testclient import TestClient

        from app.database import get_db
        from app.dependencies import require_login
        from app.main import app

        self.engine = _make_engine()
        self.SessionLocal = sessionmaker(bind=self.engine)

        db = self.SessionLocal()
        acme = Cliente(nome="Acme", attivo=True)
        beta = Cliente(nome="Beta", attivo=True)
        db.add_all([acme, beta])
        db.flush()
        _add_servizio(db, acme, "Antivirus", date(2026, 12, 5), "25.00",
                      quantita=2, referente="Mario Rossi")          # 50.00
        _add_servizio(db, beta, "Hosting", date(2026, 12, 20), "90.00")  # 90.00
        _add_servizio(db, acme, "Vecchio", date(2026, 12, 9), "10.00",
                      stato=StatoServizio.disdetto)                  # excluded from summary
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

    def test_dashboard_mostra_mese_e_servizi(self):
        r = self.client.get("/?mese=2026-12")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Dicembre 2026", r.text)
        self.assertIn("Antivirus", r.text)
        self.assertIn("Hosting", r.text)
        # Dashboard shows all services regardless of state.
        self.assertIn("Vecchio", r.text)

    def test_riepilogo_raggruppa_ed_esclude_non_attivi(self):
        r = self.client.get("/riepilogo?mese=2026-12")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Antivirus", r.text)
        self.assertIn("Hosting", r.text)
        # Disdetto service must NOT appear in the billing summary.
        self.assertNotIn("Vecchio", r.text)
        # Grand total: 50.00 + 90.00 = 140.00
        self.assertIn("140.00", r.text)

    def test_export_csv_formato(self):
        r = self.client.get("/riepilogo/export?mese=2026-12")
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/csv", r.headers["content-type"])
        self.assertIn("attachment", r.headers["content-disposition"])
        self.assertIn("riepilogo_2026-12.csv", r.headers["content-disposition"])

        # BOM present so Excel detects UTF-8.
        self.assertTrue(r.content.startswith(b"\xef\xbb\xbf"))

        testo = r.content.decode("utf-8-sig")
        righe = list(csv.reader(io.StringIO(testo), delimiter=";"))
        intestazione = righe[0]
        self.assertEqual(intestazione[0], "Cliente")
        self.assertIn("Totale", intestazione)

        dati = righe[1:]
        clienti = {r[0] for r in dati if r}
        # Only active services exported (no "Vecchio" / disdetto).
        self.assertEqual(clienti, {"Acme", "Beta"})
        descrizioni = {r[1] for r in dati if r}
        self.assertNotIn("Vecchio", descrizioni)

        # Italian decimal formatting: comma separator (e.g. Antivirus total 50,00).
        antivirus = next(r for r in dati if r and r[1] == "Antivirus")
        self.assertEqual(antivirus[6], "50,00")  # Totale column


if __name__ == "__main__":
    unittest.main()
