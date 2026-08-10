"""
Tests for the dashboard / billing-summary logic and routes, now based on the
occurrence engine.

Covers the things that carry real business risk:
- selecting the occurrences that fall in a month (including the boundaries),
- the "active services only" filter used by the billing summary,
- grouping by customer and the subtotal / grand-total sums (override-aware).

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
from app.models.enums import TipoServizio
from app.models.override_importo import OverrideImporto
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
    db, cliente, descrizione, data_inizio, importo, *,
    cadenza_mesi=1, quantita=1, rinnovo_automatico=True,
    disdetto=False, referente=None,
):
    """Add a service. By default it is a single payment (data_fine == data_inizio)
    so each test controls exactly which occurrences exist."""
    s = Servizio(
        cliente=cliente,
        descrizione=descrizione,
        tipo=TipoServizio.abbonamento,
        data_scadenza=data_inizio,
        rinnovo_automatico=rinnovo_automatico,
        cadenza_mesi=cadenza_mesi,
        importo=Decimal(importo),
        quantita=quantita,
        valuta="EUR",
        preavviso_giorni=30,
        disdetto=disdetto,
        referente=referente,
    )
    db.add(s)
    return s


DICEMBRE = date(2026, 12, 1)


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

    def test_occorrenze_del_mese_e_confini(self):
        """Only occurrences within the month are returned; the first and last day
        of the month are included, neighbouring months are excluded."""
        _add_servizio(self.db, self.acme, "Primo giorno", date(2026, 12, 1), "10")
        _add_servizio(self.db, self.acme, "Ultimo giorno", date(2026, 12, 31), "10")
        _add_servizio(self.db, self.acme, "Mese prima", date(2026, 11, 30), "10")
        _add_servizio(self.db, self.acme, "Mese dopo", date(2027, 1, 1), "10")
        self.db.flush()

        righe = riepilogo.occorrenze_del_mese(self.db, DICEMBRE)
        date_occ = {r.occorrenza.data_occorrenza for r in righe}
        # 2026-12-30 is the renewal proposal of "Mese prima" (its own
        # occurrence is 2026-11-30, one cadence earlier): a non-auto-renewing
        # contract proposes its renewal one cadence past the end date, and
        # that proposal belongs to the month it falls in.
        self.assertEqual(
            date_occ, {date(2026, 12, 1), date(2026, 12, 30), date(2026, 12, 31)}
        )

    def test_occorrenza_mensile_compare_ogni_mese(self):
        """A monthly contract spanning several months yields one occurrence in
        the selected month (proving occurrences are computed, not one-shot)."""
        _add_servizio(self.db, self.acme, "Mensile", date(2026, 1, 10), "10", cadenza_mesi=1)
        self.db.flush()

        righe = riepilogo.occorrenze_del_mese(self.db, DICEMBRE)
        self.assertEqual(
            [r.occorrenza.data_occorrenza for r in righe], [date(2026, 12, 10)]
        )

    def test_escludi_disdetti(self):
        """The billing summary must exclude only cancelled (disdetto) contracts.

        Whether a contract is otherwise "attivo"/"in_scadenza"/"scaduto" is now
        computed from dates (see stato_contratto), not a manual flag, and does
        NOT affect this filter — only disdetto (a business decision) does."""
        _add_servizio(self.db, self.acme, "Attivo", date(2026, 12, 10), "10")
        _add_servizio(self.db, self.acme, "Disdetto", date(2026, 12, 11), "10",
                      disdetto=True)
        self.db.flush()

        tutti = riepilogo.occorrenze_del_mese(self.db, DICEMBRE)
        self.assertEqual(len(tutti), 2)

        attivi = riepilogo.occorrenze_del_mese(self.db, DICEMBRE, escludi_disdetti=True)
        self.assertEqual([r.servizio.descrizione for r in attivi], ["Attivo"])

    def test_raggruppamento_e_somme(self):
        """Grouping by customer, per-occurrence totals, subtotals, grand total."""
        # Acme: 2 x 10.00 = 20.00 ; 1 x 5.50 = 5.50  -> subtotal 25.50
        _add_servizio(self.db, self.acme, "Licenze", date(2026, 12, 5), "10.00", quantita=2)
        _add_servizio(self.db, self.acme, "Dominio", date(2026, 12, 6), "5.50", quantita=1)
        # Beta: 3 x 100.00 = 300.00 -> subtotal 300.00
        _add_servizio(self.db, self.beta, "Hosting", date(2026, 12, 7), "100.00", quantita=3)
        self.db.flush()

        righe = riepilogo.occorrenze_del_mese(self.db, DICEMBRE, escludi_disdetti=True)
        gruppi = riepilogo.raggruppa_per_cliente(righe)

        self.assertEqual([g.cliente.nome for g in gruppi], ["Acme", "Beta"])
        self.assertEqual(len(gruppi[0].righe), 2)
        self.assertEqual(gruppi[0].subtotale, Decimal("25.50"))
        self.assertEqual(gruppi[1].subtotale, Decimal("300.00"))
        self.assertEqual(riepilogo.totale_complessivo(gruppi), Decimal("325.50"))

    def test_somme_rispettano_override(self):
        """An override on a December occurrence must change that occurrence's
        amount in the subtotal (and be flagged)."""
        s = _add_servizio(self.db, self.acme, "Antivirus", date(2026, 12, 10), "10.00",
                          quantita=1)
        s.override_importi.append(
            OverrideImporto(data_occorrenza=date(2026, 12, 10),
                            importo=Decimal("99.00"), quantita=5,
                            override_manuale=True)
        )
        self.db.flush()

        righe = riepilogo.occorrenze_del_mese(self.db, DICEMBRE, escludi_disdetti=True)
        self.assertEqual(len(righe), 1)
        self.assertTrue(righe[0].occorrenza.da_override)
        self.assertEqual(righe[0].occorrenza.totale, Decimal("495.00"))

        gruppi = riepilogo.raggruppa_per_cliente(righe)
        self.assertEqual(riepilogo.totale_complessivo(gruppi), Decimal("495.00"))

    def test_mese_vuoto(self):
        """No occurrences -> empty groups and a zero grand total."""
        gruppi = riepilogo.raggruppa_per_cliente(
            riepilogo.occorrenze_del_mese(self.db, DICEMBRE, escludi_disdetti=True)
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

    def test_ultimo_giorno_mese(self):
        self.assertEqual(periodi.ultimo_giorno_mese(date(2026, 12, 1)), date(2026, 12, 31))
        self.assertEqual(periodi.ultimo_giorno_mese(date(2024, 2, 1)), date(2024, 2, 29))  # leap
        self.assertEqual(periodi.ultimo_giorno_mese(date(2025, 2, 1)), date(2025, 2, 28))

    def test_etichetta_cadenza(self):
        self.assertEqual(periodi.etichetta_cadenza(1), "mensile")
        self.assertEqual(periodi.etichetta_cadenza(3), "trimestrale")
        self.assertEqual(periodi.etichetta_cadenza(6), "semestrale")
        self.assertEqual(periodi.etichetta_cadenza(12), "annuale")
        self.assertEqual(periodi.etichetta_cadenza(4), "ogni 4 mesi")

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
                      disdetto=True)                  # excluded from summary
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

    def test_dashboard_mostra_mese_e_occorrenze(self):
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
