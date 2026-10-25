"""
Tests for notifications about supplier contracts (app/services/notifiche.py)
and how they show up in the notification log.

Same three triggers as the customer side, on the contract's next renewal. The
log row points at the supplier contract instead of a service — never both,
never neither.

No real HTTP call is made: invia_telegram is patched.

Run with:  python -m unittest discover -s tests
Uses an isolated in-memory SQLite database; the real DB is never touched.
"""
from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  (registers all tables on Base.metadata)
from app.database import Base
from app.models.contratto_fornitore import ContrattoFornitore
from app.models.enums import CanalNotifica, TipoNotifica
from app.models.notifica_log import NotificaLog
from app.services import notifiche

OGGI = date(2026, 10, 25)


class _BaseDb(unittest.TestCase):

    def setUp(self):
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine)
        self.db = self.SessionLocal()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def _contratto(self, **extra) -> ContrattoFornitore:
        dati = dict(
            fornitore="NinjaOne", descrizione="Piattaforma RMM", riferimento="T-42",
            data_scadenza=OGGI + timedelta(days=20), cadenza_mesi=12,
            rinnovo_automatico=False, importo=Decimal("3.50"), quantita=100,
            valuta="EUR", preavviso_giorni=30, disdetto=False,
        )
        dati.update(extra)
        c = ContrattoFornitore(**dati)
        self.db.add(c)
        self.db.commit()
        return c


class TestCandidatiFornitore(_BaseDb):

    def _tipi(self, oggi=OGGI):
        return {c.tipo for c in notifiche.contratti_fornitore_da_notificare(self.db, oggi=oggi)}

    def test_preavviso(self):
        self._contratto(data_scadenza=OGGI + timedelta(days=20))
        self.assertEqual(self._tipi(), {TipoNotifica.preavviso})

    def test_sette_giorni(self):
        self._contratto(data_scadenza=OGGI + timedelta(days=5))
        self.assertEqual(self._tipi(), {TipoNotifica.preavviso, TipoNotifica.promemoria_7_giorni})

    def test_fuori_preavviso_niente(self):
        self._contratto(data_scadenza=OGGI + timedelta(days=31))
        self.assertEqual(self._tipi(), set())

    def test_scaduto(self):
        self._contratto(data_scadenza=OGGI - timedelta(days=2))
        self.assertEqual(self._tipi(), {TipoNotifica.contratto_scaduto})

    def test_disdetto_mai(self):
        self._contratto(data_scadenza=OGGI - timedelta(days=2), disdetto=True)
        self._contratto(data_scadenza=OGGI + timedelta(days=5), disdetto=True)
        self.assertEqual(self._tipi(), set())

    def test_automatico_avvisa_sul_prossimo_rinnovo_mai_scaduto(self):
        # Anchor years back, quarterly: the next renewal is computed.
        self._contratto(data_scadenza=date(2024, 11, 1), cadenza_mesi=3,
                        rinnovo_automatico=True)
        candidati = notifiche.contratti_fornitore_da_notificare(self.db, oggi=OGGI)
        self.assertEqual({c.data_riferimento for c in candidati}, {date(2026, 11, 1)})
        self.assertEqual(self._tipi(), {TipoNotifica.preavviso, TipoNotifica.promemoria_7_giorni})

    def test_automatico_mensile_non_avvisa_mai(self):
        self._contratto(data_scadenza=date(2024, 11, 1), cadenza_mesi=1,
                        rinnovo_automatico=True)
        self.assertEqual(self._tipi(), set())

    def test_mensile_manuale_avvisa(self):
        # Only the AUTOMATIC monthly ones are silent: a manual one must be
        # renewed by someone, so it still gets its warnings.
        self._contratto(data_scadenza=OGGI + timedelta(days=5), cadenza_mesi=1)
        self.assertIn(TipoNotifica.promemoria_7_giorni, self._tipi())

    def test_gia_inviata_non_si_ripete_ma_la_fallita_si(self):
        c = self._contratto(data_scadenza=OGGI + timedelta(days=20))
        self.db.add(NotificaLog(
            contratto_fornitore_id=c.id, data_occorrenza=c.data_scadenza,
            tipo=TipoNotifica.preavviso, canale=CanalNotifica.telegram, esito=False,
        ))
        self.db.commit()
        self.assertEqual(self._tipi(), {TipoNotifica.preavviso})
        self.db.add(NotificaLog(
            contratto_fornitore_id=c.id, data_occorrenza=c.data_scadenza,
            tipo=TipoNotifica.preavviso, canale=CanalNotifica.telegram, esito=True,
        ))
        self.db.commit()
        self.db.expire_all()
        self.assertEqual(self._tipi(), set())

    def test_dopo_rinnovato_si_riavvisa_sul_nuovo_rinnovo(self):
        c = self._contratto(data_scadenza=OGGI + timedelta(days=20))
        self.db.add(NotificaLog(
            contratto_fornitore_id=c.id, data_occorrenza=c.data_scadenza,
            tipo=TipoNotifica.preavviso, canale=CanalNotifica.telegram, esito=True,
        ))
        self.db.commit()
        # A year on, renewed: the new date has no log yet.
        c.data_scadenza = date(2027, 11, 14)
        self.db.commit()
        self.assertEqual(self._tipi(oggi=date(2027, 10, 25)), {TipoNotifica.preavviso})


class TestMessaggiFornitore(_BaseDb):

    def _messaggio(self, **extra) -> str:
        c = self._contratto(**extra)
        cand = next(x for x in notifiche.contratti_fornitore_da_notificare(self.db, oggi=OGGI)
                    if x.contratto.id == c.id)
        return notifiche.messaggio_fornitore(cand)

    def test_si_presenta_come_fornitore(self):
        testo = self._messaggio()
        self.assertTrue(testo.startswith("🛒"))
        self.assertIn("Fornitore: NinjaOne", testo)
        self.assertIn("Contratto: Piattaforma RMM", testo)
        self.assertIn("Riferimento: T-42", testo)
        self.assertNotIn("Cliente", testo)
        self.assertIn("\n", testo)
        self.assertNotIn("\\n", testo)

    def test_automatico_ricorda_di_disdire_prima(self):
        testo = self._messaggio(data_scadenza=date(2024, 11, 1), cadenza_mesi=3,
                                rinnovo_automatico=True)
        self.assertIn("Si rinnova automaticamente il: 01/11/2026", testo)

    def test_scaduto(self):
        testo = self._messaggio(data_scadenza=OGGI - timedelta(days=2))
        self.assertIn("Contratto fornitore scaduto", testo)


class TestInvioERegistro(_BaseDb):

    def test_invio_registra_sul_contratto(self):
        c = self._contratto(data_scadenza=OGGI + timedelta(days=20))
        with patch.object(notifiche, "invia_telegram", return_value=(True, None)) as invio, \
                patch.object(notifiche, "date") as finto_date:
            finto_date.today.return_value = OGGI
            n = notifiche.invia_notifiche_scadenza(self.db)
        self.assertEqual(n, 1)
        invio.assert_called_once()
        log = self.db.scalar(select(NotificaLog))
        self.assertEqual(log.contratto_fornitore_id, c.id)
        self.assertIsNone(log.servizio_id)
        self.assertTrue(log.fornitore)

    def test_vincolo_un_solo_oggetto(self):
        self.db.add(NotificaLog(
            data_occorrenza=OGGI, tipo=TipoNotifica.preavviso,
            canale=CanalNotifica.telegram, esito=True,
        ))
        with self.assertRaises(IntegrityError):
            self.db.commit()

    def test_eliminare_il_contratto_elimina_le_sue_notifiche(self):
        c = self._contratto()
        self.db.add(NotificaLog(
            contratto_fornitore_id=c.id, data_occorrenza=c.data_scadenza,
            tipo=TipoNotifica.preavviso, canale=CanalNotifica.telegram, esito=True,
        ))
        self.db.commit()
        self.db.delete(c)
        self.db.commit()
        self.assertIsNone(self.db.scalar(select(NotificaLog)))


class TestPaginaNotifiche(_BaseDb):

    def setUp(self):
        super().setUp()
        from fastapi.testclient import TestClient

        from app.database import get_db
        from app.dependencies import require_login
        from app.main import app

        c = self._contratto()
        self.db.add(NotificaLog(
            contratto_fornitore_id=c.id, data_occorrenza=c.data_scadenza,
            tipo=TipoNotifica.preavviso, canale=CanalNotifica.telegram, esito=True,
            inviata_il=datetime(2026, 10, 1, 8, 0, tzinfo=timezone.utc),
        ))
        self.db.commit()

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
        super().tearDown()

    def test_la_riga_del_fornitore_compare(self):
        h = self.client.get("/notifiche").text
        self.assertIn("NinjaOne", h)
        self.assertIn("Piattaforma RMM", h)
        self.assertIn(">fornitore</span>", h)

    def test_ricerca_per_fornitore(self):
        self.assertIn("Piattaforma RMM", self.client.get("/notifiche?q=ninja").text)
        self.assertNotIn("Piattaforma RMM", self.client.get("/notifiche?q=zzz").text)


if __name__ == "__main__":
    unittest.main()
