"""
Tests for one service's timeline (services/storico.py, GET /servizi/{id}/storico).

The delicate part is history that the engine no longer generates: occurrences
are computed from data_scadenza, so dates before the anchor stop being
produced, while their state rows — the billed ones, with the price frozen —
stay in the database. Those rows are the billing history, and the page that
exists to show the past must not drop them.

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
from app.models.override_importo import OverrideImporto
from app.models.servizio import Servizio
from app.services.storico import storico_servizio

OGGI = date(2026, 9, 9)


class TestStorico(unittest.TestCase):

    def setUp(self):
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine)
        self.db = self.SessionLocal()
        cliente = Cliente(nome="Alfa", attivo=True)
        self.db.add(cliente)
        self.db.flush()
        self.cliente_id = cliente.id

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def _servizio(self, **extra) -> Servizio:
        dati = dict(
            cliente_id=self.cliente_id, descrizione="Licenza", tipo=TipoServizio.licenza,
            data_scadenza=date(2026, 6, 15), cadenza_mesi=12, importo=Decimal("100.00"),
            quantita=1, valuta="EUR", preavviso_giorni=30, rinnovo_automatico=True,
        )
        dati.update(extra)
        s = Servizio(**dati)
        self.db.add(s)
        self.db.commit()
        return s

    def test_elenca_le_occorrenze_in_ordine(self):
        s = self._servizio()
        voci = storico_servizio(self.db, s, oggi=OGGI)
        date_voci = [v.data for v in voci]
        self.assertEqual(date_voci, sorted(date_voci))
        self.assertIn(date(2026, 6, 15), date_voci)
        self.assertIn(date(2027, 6, 15), date_voci)

    def test_orizzonte_di_due_anni_per_il_rinnovo_automatico(self):
        s = self._servizio()
        voci = storico_servizio(self.db, s, oggi=OGGI)
        self.assertTrue(all(v.data <= date(2028, 9, 30) for v in voci))
        self.assertGreaterEqual(len(voci), 2)

    def test_la_fatturazione_passata_resta_anche_se_l_ancora_e_avanzata(self):
        # The contract has been renewed: the anchor is now 2026, while the
        # 2025 occurrence was billed back when it existed.
        s = self._servizio()
        self.db.add(OverrideImporto(
            servizio_id=s.id, data_occorrenza=date(2025, 6, 15),
            importo=Decimal("90.00"), quantita=1, fatturato=True, override_manuale=False,
        ))
        self.db.commit()

        voci = storico_servizio(self.db, s, oggi=OGGI)
        vecchia = [v for v in voci if v.data == date(2025, 6, 15)]
        self.assertEqual(len(vecchia), 1, "la fatturazione passata è sparita dallo storico")
        self.assertFalse(vecchia[0].generata, "va segnalata come storico, non come occorrenza viva")
        self.assertTrue(vecchia[0].fatturato)
        # And it keeps the price it was billed at, not today's.
        self.assertEqual(vecchia[0].importo, Decimal("90.00"))

    def test_le_note_della_riga_di_stato_compaiono(self):
        s = self._servizio()
        self.db.add(OverrideImporto(
            servizio_id=s.id, data_occorrenza=date(2026, 6, 15),
            importo=Decimal("80.00"), quantita=1, fatturato=True, override_manuale=True,
            note="sconto fedeltà",
        ))
        self.db.commit()
        voci = storico_servizio(self.db, s, oggi=OGGI)
        voce = next(v for v in voci if v.data == date(2026, 6, 15))
        self.assertEqual(voce.note, "sconto fedeltà")
        self.assertTrue(voce.da_override)
        self.assertEqual(voce.importo, Decimal("80.00"))

    def test_rate_e_rinnovi_sono_distinti(self):
        # Yearly commitment billed monthly: one renewal, eleven instalments.
        s = self._servizio(cadenza_mesi=1, durata_impegno_mesi=12)
        voci = storico_servizio(self.db, s, oggi=OGGI)
        primo_anno = [v for v in voci if date(2026, 6, 15) <= v.data < date(2027, 6, 15)]
        self.assertEqual(sum(1 for v in primo_anno if v.apre_ciclo), 1)
        self.assertEqual(sum(1 for v in primo_anno if not v.apre_ciclo), 11)

    def test_totale_usa_il_prezzo_efficace(self):
        s = self._servizio(quantita=3)
        voce = storico_servizio(self.db, s, oggi=OGGI)[0]
        self.assertEqual(voce.totale, Decimal("300.00"))


class TestPaginaStorico(unittest.TestCase):

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
        cliente = Cliente(nome="Alfa", attivo=True)
        db.add(cliente)
        db.flush()
        s = Servizio(
            cliente_id=cliente.id, descrizione="Licenza antivirus", tipo=TipoServizio.licenza,
            data_scadenza=date(2026, 6, 15), cadenza_mesi=12, importo=Decimal("100.00"),
            quantita=1, valuta="EUR", preavviso_giorni=30, rinnovo_automatico=True,
            referente="Rossi",
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

    def test_la_pagina_si_apre(self):
        r = self.client.get(f"/servizi/{self.servizio_id}/storico")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Licenza antivirus", r.text)
        self.assertIn("15/06/2026", r.text)
        self.assertIn("Rossi", r.text)

    def test_servizio_inesistente_404(self):
        self.assertEqual(self.client.get("/servizi/999/storico").status_code, 404)

    def test_link_dalla_lista_servizi(self):
        h = self.client.get("/servizi").text
        self.assertIn(f"/servizi/{self.servizio_id}/storico", h)


if __name__ == "__main__":
    unittest.main()
