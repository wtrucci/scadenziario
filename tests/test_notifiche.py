"""
Tests for the expiration-notification logic (app/services/notifiche.py): the
three independent triggers (preavviso, promemoria_7_giorni, contratto_scaduto)
and that a successful send is never repeated for the same
(service, date, tipo, channel).

No real HTTP call is made: invia_telegram is monkeypatched.

Run with:  python -m unittest discover -s tests
Uses an isolated in-memory SQLite database; the real DB is never touched.
"""
from __future__ import annotations

import unittest
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  (registers all tables on Base.metadata)
from app.database import Base
from app.models.cliente import Cliente
from app.models.enums import TipoServizio, TipoNotifica
from app.models.notifica_log import NotificaLog
from app.models.servizio import Servizio
from app.services import notifiche

OGGI = date(2026, 8, 8)


def _make_engine():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return engine


class TestNotifiche(unittest.TestCase):

    def setUp(self):
        self.engine = _make_engine()
        SessionLocal = sessionmaker(bind=self.engine)
        self.db = SessionLocal()

        cliente = Cliente(nome="Acme", attivo=True)
        self.db.add(cliente)
        self.db.flush()
        self.cliente = cliente

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def _servizio(self, **kwargs) -> Servizio:
        base = dict(
            cliente=self.cliente, descrizione="Assistenza", tipo=TipoServizio.contratto,
            cadenza_mesi=1, rinnovo_automatico=True,
            importo=Decimal("50.00"), quantita=1, valuta="EUR",
            preavviso_giorni=30,
        )
        base.update(kwargs)
        s = Servizio(**base)
        self.db.add(s)
        self.db.commit()
        return s

    # --- preavviso personalizzato -----------------------------------------

    def test_occorrenza_in_scadenza_e_candidata(self):
        self._servizio(data_scadenza=OGGI + timedelta(days=5))
        candidati = notifiche.occorrenze_da_notificare(self.db, oggi=OGGI)
        tipi = {c.tipo for c in candidati}
        self.assertIn(TipoNotifica.preavviso, tipi)

    def test_servizio_disdetto_escluso(self):
        s = self._servizio(
            data_scadenza=OGGI + timedelta(days=5), disdetto=True
        )
        candidati = notifiche.occorrenze_da_notificare(self.db, oggi=OGGI)
        self.assertEqual(candidati, [])

    def test_occorrenza_fuori_finestra_di_preavviso_non_e_candidata(self):
        self._servizio(
            data_scadenza=OGGI + timedelta(days=90), preavviso_giorni=5
        )
        candidati = notifiche.occorrenze_da_notificare(self.db, oggi=OGGI)
        self.assertEqual(candidati, [])

    def test_occorrenza_gia_fatturata_non_e_candidata(self):
        from app.models.override_importo import OverrideImporto

        s = self._servizio(data_scadenza=OGGI + timedelta(days=5))
        self.db.add(OverrideImporto(
            servizio_id=s.id, data_occorrenza=s.data_scadenza, fatturato=True, fatturato_il=OGGI,
        ))
        self.db.commit()
        candidati = notifiche.occorrenze_da_notificare(self.db, oggi=OGGI)
        self.assertEqual(candidati, [])

    # --- promemoria fisso 7 giorni ------------------------------------------

    def test_promemoria_7_giorni_indipendente_dal_preavviso(self):
        # preavviso_giorni=3 would NOT trigger "preavviso" for an occurrence
        # due in 6 days, but the fixed 7-day reminder must still fire.
        self._servizio(
            data_scadenza=OGGI + timedelta(days=6), preavviso_giorni=3
        )
        candidati = notifiche.occorrenze_da_notificare(self.db, oggi=OGGI)
        tipi = {c.tipo for c in candidati}
        self.assertIn(TipoNotifica.promemoria_7_giorni, tipi)
        self.assertNotIn(TipoNotifica.preavviso, tipi)

    def test_promemoria_7_giorni_fuori_finestra_non_e_candidato(self):
        self._servizio(
            data_scadenza=OGGI + timedelta(days=8), preavviso_giorni=3
        )
        candidati = notifiche.occorrenze_da_notificare(self.db, oggi=OGGI)
        self.assertEqual(candidati, [])

    # --- contratto scaduto ---------------------------------------------------

    def test_contratto_scaduto_e_candidato(self):
        self._servizio(
            data_scadenza=OGGI - timedelta(days=1), rinnovo_automatico=False,
        )
        candidati = notifiche.occorrenze_da_notificare(self.db, oggi=OGGI)
        tipi = [c for c in candidati if c.tipo is TipoNotifica.contratto_scaduto]
        self.assertEqual(len(tipi), 1)

    def test_contratto_scaduto_con_rinnovo_automatico_non_notifica(self):
        self._servizio(
            data_scadenza=OGGI - timedelta(days=1), rinnovo_automatico=True,
        )
        candidati = notifiche.occorrenze_da_notificare(self.db, oggi=OGGI)
        tipi = [c for c in candidati if c.tipo is TipoNotifica.contratto_scaduto]
        self.assertEqual(tipi, [])

    # --- invio e dedup ---------------------------------------------------

    def test_invio_riuscito_non_viene_ripetuto(self):
        self._servizio(data_scadenza=OGGI + timedelta(days=5))
        with patch.object(notifiche, "invia_telegram", return_value=(True, None)):
            inviate = notifiche.invia_notifiche_scadenza(self.db)
        self.assertGreater(inviate, 0)
        totale_log = self.db.query(NotificaLog).count()
        self.assertEqual(totale_log, inviate)

        with patch.object(notifiche, "invia_telegram", return_value=(True, None)) as mock_invio:
            notifiche.invia_notifiche_scadenza(self.db)
        mock_invio.assert_not_called()

    def test_invio_fallito_viene_ritentato(self):
        self._servizio(data_scadenza=OGGI + timedelta(days=5))
        with patch.object(notifiche, "invia_telegram", return_value=(False, "errore di rete")):
            notifiche.invia_notifiche_scadenza(self.db)
        self.assertTrue(all(not log.esito for log in self.db.query(NotificaLog).all()))

        with patch.object(notifiche, "invia_telegram", return_value=(True, None)) as mock_invio:
            inviate = notifiche.invia_notifiche_scadenza(self.db)
        mock_invio.assert_called()
        self.assertGreater(inviate, 0)


if __name__ == "__main__":
    unittest.main()
