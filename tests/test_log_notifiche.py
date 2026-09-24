"""
Tests for the notification log page (GET /notifiche).

NotificaLog used to be write-only: a failed Telegram delivery left a trace
only in the container log. What matters here is that failures are visible and
findable — the counts must cover the whole log, not just the page, or a
failure older than the last few hundred rows would be invisible exactly when
it matters.

Run with:  python -m unittest discover -s tests
Uses an isolated in-memory SQLite database; the real DB is never touched.
"""
from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  (registers all tables on Base.metadata)
from app.database import Base
from app.models.cliente import Cliente
from app.models.enums import CanalNotifica, TipoNotifica, TipoServizio
from app.models.notifica_log import NotificaLog
from app.models.servizio import Servizio


class TestLogNotifiche(unittest.TestCase):

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
        servizio = Servizio(
            cliente_id=cliente.id, descrizione="Licenza antivirus",
            tipo=TipoServizio.licenza, data_scadenza=date(2026, 9, 21), cadenza_mesi=12,
            importo=Decimal("100"), quantita=1, valuta="EUR", preavviso_giorni=30,
        )
        db.add(servizio)
        db.flush()
        adesso = datetime(2026, 9, 1, 21, 40, tzinfo=timezone.utc)
        db.add_all([
            NotificaLog(
                servizio_id=servizio.id, data_occorrenza=date(2026, 9, 21),
                tipo=TipoNotifica.preavviso, canale=CanalNotifica.telegram,
                inviata_il=adesso, esito=True, dettaglio=None,
            ),
            NotificaLog(
                servizio_id=servizio.id, data_occorrenza=date(2026, 9, 21),
                tipo=TipoNotifica.promemoria_7_giorni, canale=CanalNotifica.telegram,
                inviata_il=adesso + timedelta(days=13), esito=False,
                dettaglio="401 Unauthorized: bot token non valido",
            ),
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

    def test_elenca_le_notifiche(self):
        h = self.client.get("/notifiche").text
        self.assertIn("Licenza antivirus", h)
        self.assertIn("Inviata", h)
        self.assertIn("Fallita", h)

    def test_mostra_il_dettaglio_dell_errore(self):
        h = self.client.get("/notifiche").text
        self.assertIn("401 Unauthorized", h)

    def test_orario_mostrato_nel_fuso_configurato(self):
        # Stored 21:40 UTC; Europe/Rome in September is UTC+2 -> 23:40.
        h = self.client.get("/notifiche").text
        self.assertIn("23:40", h)

    def test_orario_convertito_anche_se_il_db_perde_il_fuso(self):
        """SQLite hands timestamps back naive: they must still be read as UTC.

        Without this the conversion is a no-op on a machine already set to TZ
        (so it looks right in development) and wrong everywhere else.
        """
        from app.routes.notifiche import _in_locale

        naive = datetime(2026, 9, 1, 21, 40)  # what SQLite returns
        self.assertEqual(_in_locale(naive).strftime("%H:%M"), "23:40")

    def test_filtro_fallite(self):
        h = self.client.get("/notifiche", params={"esito": "fallite"}).text
        self.assertIn("401 Unauthorized", h)
        self.assertNotIn("preavviso", h)

    def test_filtro_inviate(self):
        h = self.client.get("/notifiche", params={"esito": "inviate"}).text
        self.assertNotIn("401 Unauthorized", h)

    def test_card_attiva_azzera_il_filtro(self):
        h = self.client.get("/notifiche", params={"esito": "fallite"}).text
        self.assertIn("summary-card-attiva", h)
        # The active card links back to the unfiltered page.
        self.assertIn('href="/notifiche"', h)

    def test_ricerca_per_cliente(self):
        self.assertIn("Licenza antivirus", self.client.get("/notifiche", params={"q": "alfa"}).text)
        self.assertNotIn("Licenza antivirus", self.client.get("/notifiche", params={"q": "beta"}).text)

    def test_ricerca_nel_dettaglio_errore(self):
        h = self.client.get("/notifiche", params={"q": "unauthorized"}).text
        self.assertIn("401 Unauthorized", h)

    def test_conteggi_su_tutto_il_log(self):
        h = self.client.get("/notifiche", params={"esito": "inviate"}).text
        # Even while filtered to the successful ones, the failure count stays
        # visible: that is the whole point of the card.
        blocco = h[h.index('<div class="summary-cards">'):h.index("</div>", h.index("Fallite"))]
        self.assertIn("Fallite", blocco)

    def test_pagina_vuota_lo_spiega(self):
        db = self.SessionLocal()
        db.query(NotificaLog).delete()
        db.commit()
        db.close()
        h = self.client.get("/notifiche").text
        self.assertIn("Nessuna notifica inviata finora", h)

    def test_voce_nel_menu(self):
        h = self.client.get("/notifiche").text
        self.assertIn('href="/notifiche"', h)


if __name__ == "__main__":
    unittest.main()


class TestInvioProva(unittest.TestCase):
    """The "Invia messaggio di prova" button on the notifiche page.

    invia_telegram is replaced by a stand-in: the test must never send a real
    message, and what matters here is how the page reports each outcome.
    """

    def setUp(self):
        from unittest.mock import patch

        from fastapi.testclient import TestClient

        from app.database import get_db
        from app.dependencies import require_login
        from app.main import app

        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine)

        def override_get_db():
            db = self.SessionLocal()
            try:
                yield db
            finally:
                db.close()

        self.app = app
        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[require_login] = lambda: SimpleNamespace(username="walter")
        self.client = TestClient(app)
        self.patch = patch
        self.inviati = []

    def tearDown(self):
        self.app.dependency_overrides.clear()
        self.engine.dispose()

    def _invia(self, esito, dettaglio=None):
        def finto(testo):
            self.inviati.append(testo)
            return esito, dettaglio
        with self.patch("app.routes.notifiche.invia_telegram", finto):
            return self.client.post("/notifiche/test")

    def test_successo(self):
        r = self._invia(True)
        self.assertEqual(r.status_code, 200)
        self.assertIn("Messaggio di prova inviato", r.text)

    def test_errore_mostra_il_motivo(self):
        r = self._invia(False, "HTTP 401: Unauthorized")
        self.assertIn("Invio non riuscito", r.text)
        self.assertIn("HTTP 401: Unauthorized", r.text)
        self.assertIn("TELEGRAM_BOT_TOKEN", r.text)

    def test_configurazione_mancante(self):
        r = self._invia(False, "TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID non configurati")
        self.assertIn("non configurati", r.text)

    def test_il_messaggio_si_presenta_come_prova(self):
        self._invia(True)
        self.assertEqual(len(self.inviati), 1)
        self.assertIn("prova", self.inviati[0])
        self.assertIn("walter", self.inviati[0])

    def test_la_prova_non_finisce_nel_log(self):
        self._invia(True)
        db = self.SessionLocal()
        try:
            self.assertEqual(db.query(NotificaLog).count(), 0)
        finally:
            db.close()

    def test_il_pulsante_e_nella_pagina(self):
        h = self.client.get("/notifiche").text
        self.assertIn('hx-post="/notifiche/test"', h)
