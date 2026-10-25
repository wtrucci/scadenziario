"""
Tests for renewal offers (services/offerte.py, routes/offerte.py).

The page lists the renewals that need an offer sent to the customer: contracts
without automatic renewal, whose pending renewal falls within the next
GIORNI_ORIZZONTE days, or already went by without an offer. The flag belongs to
ONE renewal, so the next one starts again as "to send".

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
from app.models.cliente import Cliente
from app.models.enums import TipoServizio
from app.models.offerta_rinnovo import OffertaRinnovo
from app.models.override_importo import OverrideImporto
from app.models.servizio import Servizio
from app.services.offerte import righe_offerte

OGGI = date(2026, 10, 25)


class _BaseDb(unittest.TestCase):

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
            data_scadenza=OGGI + timedelta(days=20), cadenza_mesi=12,
            importo=Decimal("100.00"), quantita=2, valuta="EUR", preavviso_giorni=30,
            rinnovo_automatico=False,
        )
        dati.update(extra)
        s = Servizio(**dati)
        self.db.add(s)
        self.db.commit()
        return s


class TestRigheOfferte(_BaseDb):

    def _righe(self):
        return righe_offerte(self.db.scalars(select(Servizio)).all(), oggi=OGGI)

    def test_rinnovo_vicino_non_inviato_e_urgente(self):
        self._servizio()
        [riga] = self._righe()
        self.assertEqual(riga.rinnovo.data_occorrenza, OGGI + timedelta(days=20))
        self.assertEqual(riga.giorni, 20)
        self.assertEqual(riga.stato, "urgente")
        self.assertEqual(riga.rinnovo.totale, Decimal("200.00"))

    def test_fra_30_e_60_giorni_e_da_inviare(self):
        self._servizio(data_scadenza=OGGI + timedelta(days=45))
        [riga] = self._righe()
        self.assertEqual(riga.stato, "da_inviare")
        self.assertFalse(riga.da_sollecitare)

    def test_oltre_60_giorni_non_compare(self):
        self._servizio(data_scadenza=OGGI + timedelta(days=61))
        self.assertEqual(self._righe(), [])

    def test_rinnovo_automatico_e_disdetti_esclusi(self):
        self._servizio(rinnovo_automatico=True)
        self._servizio(disdetto=True)
        self.assertEqual(self._righe(), [])

    def test_offerta_inviata(self):
        s = self._servizio()
        self.db.add(OffertaRinnovo(servizio_id=s.id, data_rinnovo=s.data_scadenza))
        self.db.commit()
        [riga] = self._righe()
        self.assertEqual(riga.stato, "inviata")
        self.assertTrue(riga.inviata)

    def test_rinnovo_passato_senza_offerta_resta_in_rosso(self):
        self._servizio(data_scadenza=OGGI - timedelta(days=10))
        [riga] = self._righe()
        self.assertEqual(riga.stato, "scaduta")
        self.assertEqual(riga.giorni, -10)
        self.assertTrue(riga.da_sollecitare)

    def test_rinnovo_passato_con_offerta_resta_in_attesa(self):
        # Offer sent, renewal date gone by, no answer yet: still pending, and
        # still on the page so the mark can be undone.
        s = self._servizio(data_scadenza=OGGI - timedelta(days=10))
        self.db.add(OffertaRinnovo(servizio_id=s.id, data_rinnovo=s.data_scadenza))
        self.db.commit()
        [riga] = self._righe()
        self.assertEqual(riga.stato, "inviata")
        self.assertFalse(riga.da_sollecitare)

    def test_rinnovo_confermato_passa_al_successivo_da_inviare(self):
        # The renewal was billed (= confirmed) after its offer went out: the
        # proposal is now a year later, outside the window, so nothing shows.
        s = self._servizio(data_scadenza=OGGI - timedelta(days=10))
        self.db.add(OffertaRinnovo(servizio_id=s.id, data_rinnovo=s.data_scadenza))
        self.db.add(OverrideImporto(servizio_id=s.id, data_occorrenza=s.data_scadenza,
                                    fatturato=True))
        self.db.commit()
        self.assertEqual(self._righe(), [])
        # A year on, the next renewal comes up with no offer sent.
        righe = righe_offerte([s], oggi=OGGI + timedelta(days=340))
        self.assertEqual(len(righe), 1)
        self.assertFalse(righe[0].inviata)

    def test_rate_non_sono_rinnovi(self):
        # Yearly commitment billed monthly, current cycle already confirmed:
        # the monthly instalments in the window are not renewals.
        s = self._servizio(data_scadenza=OGGI - timedelta(days=100), cadenza_mesi=1,
                           durata_impegno_mesi=12)
        self.db.add(OverrideImporto(servizio_id=s.id, data_occorrenza=s.data_scadenza,
                                    fatturato=True))
        self.db.commit()
        self.assertEqual(self._righe(), [])

    def test_ordinate_per_data(self):
        self._servizio(data_scadenza=OGGI + timedelta(days=40))
        self._servizio(data_scadenza=OGGI - timedelta(days=5))
        self._servizio(data_scadenza=OGGI + timedelta(days=10))
        self.assertEqual([r.giorni for r in self._righe()], [-5, 10, 40])


class TestRotteOfferte(_BaseDb):
    """The routes work on date.today(), so dates here are relative to it."""

    def setUp(self):
        super().setUp()
        from fastapi.testclient import TestClient

        from app.database import get_db
        from app.dependencies import require_login
        from app.main import app

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
        super().tearDown()

    def test_pagina_elenca_il_rinnovo(self):
        s = self._servizio(data_scadenza=self.oggi + timedelta(days=20), descrizione="Firewall")
        r = self.client.get("/offerte")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Firewall", r.text)
        self.assertIn(f"/offerte/{s.id}/{s.data_scadenza.isoformat()}/inviata", r.text)
        self.assertIn(f"/servizi/{s.id}/modifica?ritorno=/offerte", r.text)

    def test_toggle_segna_e_smarca(self):
        s = self._servizio(data_scadenza=self.oggi + timedelta(days=20))
        url = f"/offerte/{s.id}/{s.data_scadenza.isoformat()}/inviata"

        r = self.client.post(url)
        self.assertEqual(r.status_code, 200)
        self.assertIn("offerteAggiornate", r.headers["HX-Trigger"])
        self.assertIn("✓ Inviata", r.text)
        self.assertEqual(self.db.scalar(select(OffertaRinnovo).where(
            OffertaRinnovo.servizio_id == s.id)).data_rinnovo, s.data_scadenza)

        r = self.client.post(url)
        self.assertIn("Da inviare", r.text)
        self.db.expire_all()
        self.assertIsNone(self.db.scalar(select(OffertaRinnovo)))

    def test_toggle_rifiuta_date_che_non_sono_il_rinnovo(self):
        s = self._servizio(data_scadenza=self.oggi + timedelta(days=20))
        r = self.client.post(f"/offerte/{s.id}/{(s.data_scadenza + timedelta(days=1)).isoformat()}/inviata")
        self.assertEqual(r.status_code, 404)
        self.assertIsNone(self.db.scalar(select(OffertaRinnovo)))

    def test_contatore_solo_urgenti(self):
        self._servizio(data_scadenza=self.oggi + timedelta(days=20))
        self._servizio(data_scadenza=self.oggi - timedelta(days=3))
        self._servizio(data_scadenza=self.oggi + timedelta(days=50))
        self.assertEqual(self.client.get("/offerte/contatore").text, "2")

    def test_contatore_vuoto_senza_urgenze(self):
        self._servizio(data_scadenza=self.oggi + timedelta(days=50))
        self.assertEqual(self.client.get("/offerte/contatore").text, "")

    def test_filtro_card_inviate(self):
        a = self._servizio(data_scadenza=self.oggi + timedelta(days=20), descrizione="Inviata-A")
        self._servizio(data_scadenza=self.oggi + timedelta(days=25), descrizione="Aperta-B")
        self.db.add(OffertaRinnovo(servizio_id=a.id, data_rinnovo=a.data_scadenza))
        self.db.commit()
        r = self.client.get("/offerte?stato=inviate")
        self.assertIn("Inviata-A", r.text)
        self.assertNotIn("Aperta-B", r.text)

    def test_ricerca(self):
        self._servizio(data_scadenza=self.oggi + timedelta(days=20), descrizione="Firewall")
        self._servizio(data_scadenza=self.oggi + timedelta(days=20), descrizione="Antivirus")
        r = self.client.get("/offerte?q=fire")
        self.assertIn("Firewall", r.text)
        self.assertNotIn("Antivirus", r.text)

    def test_eliminare_il_servizio_elimina_le_offerte(self):
        s = self._servizio(data_scadenza=self.oggi + timedelta(days=20))
        self.db.add(OffertaRinnovo(servizio_id=s.id, data_rinnovo=s.data_scadenza))
        self.db.commit()
        self.db.delete(s)
        self.db.commit()
        self.assertIsNone(self.db.scalar(select(OffertaRinnovo)))


if __name__ == "__main__":
    unittest.main()
