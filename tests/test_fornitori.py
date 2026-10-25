"""
Tests for supplier contracts (services/fornitori.py, routes/fornitori.py).

A supplier contract has one date, the next renewal. Auto-renewing ones roll
forward by themselves and never read "Scaduto"; manual ones move only when
"Rinnovato" is pressed. They are costs, so they must never leak into the
customer-side pages.

Run with:  python -m unittest discover -s tests
Uses an isolated in-memory SQLite database; the real DB is never touched.
"""
from __future__ import annotations

import unittest
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  (registers all tables on Base.metadata)
from app.database import Base
from app.models.contratto_fornitore import ContrattoFornitore
from app.services.fornitori import (
    prossima_scadenza,
    scadenza_dopo_rinnovo,
    stato_contratto_fornitore,
)

OGGI = date(2026, 10, 25)


def _contratto(**extra) -> ContrattoFornitore:
    dati = dict(
        fornitore="NinjaOne", descrizione="RMM", data_scadenza=date(2026, 12, 1),
        cadenza_mesi=12, rinnovo_automatico=False, importo=Decimal("3.50"),
        quantita=100, valuta="EUR", preavviso_giorni=30, disdetto=False,
    )
    dati.update(extra)
    return ContrattoFornitore(**dati)


class TestScadenzeFornitore(unittest.TestCase):

    def test_manuale_mostra_la_data_salvata(self):
        c = _contratto(data_scadenza=date(2026, 9, 1))
        self.assertEqual(prossima_scadenza(c, OGGI), date(2026, 9, 1))
        self.assertEqual(stato_contratto_fornitore(c, OGGI), "scaduto")

    def test_automatico_avanza_da_solo_e_non_scade_mai(self):
        c = _contratto(data_scadenza=date(2025, 3, 15), cadenza_mesi=12, rinnovo_automatico=True)
        self.assertEqual(prossima_scadenza(c, OGGI), date(2027, 3, 15))
        self.assertEqual(stato_contratto_fornitore(c, OGGI), "attivo")

    def test_automatico_mensile_senza_deriva(self):
        c = _contratto(data_scadenza=date(2026, 1, 31), cadenza_mesi=1, rinnovo_automatico=True)
        self.assertEqual(prossima_scadenza(c, OGGI), date(2026, 10, 31))
        self.assertEqual(prossima_scadenza(c, date(2026, 11, 1)), date(2026, 11, 30))

    def test_automatico_scadenza_oggi_resta_oggi(self):
        c = _contratto(data_scadenza=date(2025, 10, 25), rinnovo_automatico=True)
        self.assertEqual(prossima_scadenza(c, OGGI), OGGI)

    def test_in_scadenza_entro_il_preavviso(self):
        c = _contratto(data_scadenza=OGGI + timedelta(days=30))
        self.assertEqual(stato_contratto_fornitore(c, OGGI), "in_scadenza")
        c = _contratto(data_scadenza=OGGI + timedelta(days=31))
        self.assertEqual(stato_contratto_fornitore(c, OGGI), "attivo")

    def test_disdetto_vince(self):
        c = _contratto(data_scadenza=date(2026, 9, 1), disdetto=True)
        self.assertEqual(stato_contratto_fornitore(c, OGGI), "disdetto")

    def test_rinnovato_avanza_di_una_cadenza(self):
        c = _contratto(data_scadenza=date(2026, 9, 1), cadenza_mesi=12)
        self.assertEqual(scadenza_dopo_rinnovo(c), date(2027, 9, 1))


class TestRotteFornitori(unittest.TestCase):

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
        self.db = self.SessionLocal()

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
        self.oggi = date.today()

    def tearDown(self):
        self.app.dependency_overrides.clear()
        self.db.close()
        self.engine.dispose()

    def _salva(self, **extra) -> ContrattoFornitore:
        c = _contratto(**extra)
        self.db.add(c)
        self.db.commit()
        return c

    def _payload(self, **extra) -> dict:
        dati = {
            "fornitore": "Wasabi", "descrizione": "Storage S3", "riferimento": "acct-42",
            "data_scadenza": "2027-01-15", "cadenza_mesi": "1", "importo": "6,99",
            "quantita": "3", "valuta": "eur", "preavviso_giorni": "15",
            "rinnovo_automatico": "on", "note": "",
        }
        dati.update(extra)
        return dati

    def test_crea_contratto(self):
        r = self.client.post("/fornitori", data=self._payload(), follow_redirects=False)
        self.assertEqual(r.status_code, 303, r.text)
        c = self.db.scalar(select(ContrattoFornitore))
        self.assertEqual(c.fornitore, "Wasabi")
        self.assertEqual(c.importo, Decimal("6.99"))
        self.assertEqual(c.valuta, "EUR")
        self.assertTrue(c.rinnovo_automatico)
        self.assertFalse(c.disdetto)
        self.assertEqual(c.riferimento, "acct-42")

    def test_errori_rimostrano_il_form(self):
        r = self.client.post("/fornitori", data=self._payload(fornitore=" ", importo="abc"))
        self.assertEqual(r.status_code, 422)
        self.assertIn("Il fornitore è obbligatorio", r.text)
        self.assertIn("Costo non valido", r.text)
        # What was typed is kept.
        self.assertIn('value="Storage S3"', r.text)
        self.assertIsNone(self.db.scalar(select(ContrattoFornitore)))

    def test_modifica(self):
        c = self._salva()
        dati = self._payload(descrizione="RMM 2")
        del dati["rinnovo_automatico"]  # an unticked checkbox is not posted at all
        r = self.client.post(f"/fornitori/{c.id}/modifica", data=dati, follow_redirects=False)
        self.assertEqual(r.status_code, 303, r.text)
        self.db.expire_all()
        self.assertEqual(c.descrizione, "RMM 2")
        self.assertFalse(c.rinnovo_automatico)

    def test_form_di_un_automatico_mostra_la_scadenza_calcolata(self):
        c = self._salva(data_scadenza=self.oggi - timedelta(days=400), cadenza_mesi=1,
                        rinnovo_automatico=True)
        r = self.client.get(f"/fornitori/{c.id}/modifica")
        attesa = prossima_scadenza(c, self.oggi).strftime("%d/%m/%Y")
        self.assertIn(f"Prossima scadenza: <strong>{attesa}</strong>", r.text)

    def test_lista_e_card(self):
        self._salva(descrizione="Scaduto-A", data_scadenza=self.oggi - timedelta(days=3))
        self._salva(descrizione="Vicino-B", data_scadenza=self.oggi + timedelta(days=10))
        self._salva(descrizione="Lontano-C", data_scadenza=self.oggi + timedelta(days=200))
        r = self.client.get("/fornitori")
        for d in ("Scaduto-A", "Vicino-B", "Lontano-C"):
            self.assertIn(d, r.text)
        r = self.client.get("/fornitori?stato=scaduto")
        self.assertIn("Scaduto-A", r.text)
        self.assertNotIn("Vicino-B", r.text)

    def test_ricerca(self):
        self._salva(fornitore="NinjaOne", descrizione="RMM")
        self._salva(fornitore="Wasabi", descrizione="Storage")
        r = self.client.get("/fornitori?q=wasa")
        self.assertIn("Storage", r.text)
        self.assertNotIn("NinjaOne", r.text)

    def test_rinnovato_sposta_la_scadenza(self):
        c = self._salva(data_scadenza=self.oggi - timedelta(days=3), cadenza_mesi=12)
        attesa = scadenza_dopo_rinnovo(c)
        r = self.client.post(f"/fornitori/{c.id}/rinnovato")
        self.assertEqual(r.status_code, 200)
        self.db.expire_all()
        self.assertEqual(c.data_scadenza, attesa)

    def test_rinnovato_rifiutato_per_i_contratti_automatici(self):
        c = self._salva(rinnovo_automatico=True)
        prima = c.data_scadenza
        r = self.client.post(f"/fornitori/{c.id}/rinnovato")
        self.assertEqual(r.status_code, 400)
        self.db.expire_all()
        self.assertEqual(c.data_scadenza, prima)

    def test_elimina(self):
        c = self._salva()
        r = self.client.delete(f"/fornitori/{c.id}")
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(self.db.scalar(select(ContrattoFornitore)))

    def test_non_compare_nelle_pagine_dei_clienti(self):
        self._salva(descrizione="Costo-Fornitore-XYZ", data_scadenza=self.oggi + timedelta(days=5))
        for url in ("/", "/servizi", "/riepilogo", "/offerte"):
            with self.subTest(url=url):
                self.assertNotIn("Costo-Fornitore-XYZ", self.client.get(url).text)


if __name__ == "__main__":
    unittest.main()
