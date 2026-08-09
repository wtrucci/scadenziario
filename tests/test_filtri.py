"""
Tests for the list filters (services page + dashboard) and the partial/full-page
HTMX pattern.

Covers:
- SQL filtering by customer in occorrenze_del_mese,
- the Python filter on the computed per-occurrence visual state,
- route-level combinations (customer + state) on both pages,
- that filters survive month navigation (nav links carry them),
- the HTMX path (HX-Request -> only the results partial) vs a direct/bookmarked
  URL (full page, with the filter form pre-populated from the querystring).

Run with:  python -m unittest discover -s tests
Uses an isolated in-memory SQLite database; the real DB is never touched.
"""
from __future__ import annotations

import re
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
from app.services import riepilogo

# A month safely in the past, so its occurrences are always "da_fatturare"
# (past, not billed) regardless of the day the tests run.
PASSATO = date(2020, 6, 1)


def _make_engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine


def _add_servizio(db, cliente, descrizione, data_inizio, importo, *,
                  data_fine=None, disdetto=False, referente=None):
    s = Servizio(
        cliente=cliente,
        descrizione=descrizione,
        tipo=TipoServizio.abbonamento,
        data_inizio=data_inizio,
        data_fine=data_fine if data_fine is not None else data_inizio,
        cadenza_mesi=1,
        importo=Decimal(importo),
        quantita=1,
        valuta="EUR",
        preavviso_giorni=30,
        disdetto=disdetto,
        referente=referente,
    )
    db.add(s)
    return s


class TestFiltriLogica(unittest.TestCase):
    """Service-layer filtering."""

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

    def test_filtro_cliente_in_sql(self):
        _add_servizio(self.db, self.acme, "A1", date(2026, 12, 5), "10")
        _add_servizio(self.db, self.beta, "B1", date(2026, 12, 6), "10")
        self.db.flush()

        righe = riepilogo.occorrenze_del_mese(
            self.db, date(2026, 12, 1), cliente_id=self.acme.id
        )
        self.assertEqual([r.servizio.descrizione for r in righe], ["A1"])

    def test_filtro_referente_in_sql(self):
        _add_servizio(self.db, self.acme, "A1", date(2026, 12, 5), "10", referente="Mario")
        _add_servizio(self.db, self.beta, "B1", date(2026, 12, 6), "10", referente="Luisa")
        self.db.flush()

        righe = riepilogo.occorrenze_del_mese(
            self.db, date(2026, 12, 1), referente="Mario"
        )
        self.assertEqual([r.servizio.descrizione for r in righe], ["A1"])

    def test_filtra_per_stato_visivo(self):
        """The computed-state filter keeps only matching rows, and is a no-op
        when no state is requested."""
        def riga(stato_visivo, fatturato=False, disdetto=False):
            return SimpleNamespace(
                occorrenza=SimpleNamespace(stato_visivo=stato_visivo, fatturato=fatturato),
                servizio=SimpleNamespace(disdetto=disdetto),
            )

        righe = [
            riga("da_fatturare"),
            riga("fatturato", fatturato=True),
            riga("normale"),
        ]
        solo_fatt = riepilogo.filtra_per_stato_visivo(righe, "fatturato")
        self.assertEqual([r.occorrenza.stato_visivo for r in solo_fatt], ["fatturato"])
        # Empty/None -> unchanged.
        self.assertIs(riepilogo.filtra_per_stato_visivo(righe, None), righe)
        self.assertIs(riepilogo.filtra_per_stato_visivo(righe, ""), righe)

    def test_filtra_da_fatturare_include_tutte_le_non_fatturate(self):
        """"da_fatturare" selects every unbilled row of a non-cancelled
        contract — overdue, approaching AND future alike (the same population
        the dashboard's card counts) — but never billed or cancelled ones."""
        def riga(stato_visivo, fatturato=False, disdetto=False):
            return SimpleNamespace(
                occorrenza=SimpleNamespace(stato_visivo=stato_visivo, fatturato=fatturato),
                servizio=SimpleNamespace(disdetto=disdetto),
            )

        righe = [
            riga("da_fatturare"),                  # overdue          -> kept
            riga("in_scadenza"),                   # approaching      -> kept
            riga("normale"),                       # future, unbilled -> kept
            riga("fatturato", fatturato=True),     # billed           -> dropped
            riga("normale", disdetto=True),        # cancelled        -> dropped
        ]
        filtrate = riepilogo.filtra_per_stato_visivo(righe, "da_fatturare")
        self.assertEqual(
            [r.occorrenza.stato_visivo for r in filtrate],
            ["da_fatturare", "in_scadenza", "normale"],
        )


class TestFiltriRotteDashboard(unittest.TestCase):
    """Dashboard route: filter combinations, month-nav preservation, HTMX."""

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
        sa = _add_servizio(db, acme, "PassatoAcme", PASSATO.replace(day=10), "10")
        _add_servizio(db, beta, "PassatoBeta", PASSATO.replace(day=11), "10")
        db.flush()
        # Acme's occurrence is billed -> "fatturato"; Beta's stays "da_fatturare".
        sa.override_importi.append(
            OverrideImporto(data_occorrenza=PASSATO.replace(day=10), fatturato=True)
        )
        db.commit()
        self.acme_id = acme.id
        self.beta_id = beta.id
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

    def test_nessun_filtro_mostra_tutti(self):
        r = self.client.get("/?mese=2020-06")
        self.assertEqual(r.status_code, 200)
        self.assertIn("PassatoAcme", r.text)
        self.assertIn("PassatoBeta", r.text)

    def test_filtro_cliente(self):
        r = self.client.get(f"/?mese=2020-06&cliente={self.acme_id}")
        self.assertEqual(r.status_code, 200)
        self.assertIn("PassatoAcme", r.text)
        self.assertNotIn("PassatoBeta", r.text)

    def test_filtro_stato_occorrenza(self):
        # Only the billed occurrence (Acme) for stato=fatturato.
        r = self.client.get("/?mese=2020-06&stato=fatturato")
        self.assertIn("PassatoAcme", r.text)
        self.assertNotIn("PassatoBeta", r.text)
        # Only the unbilled one (Beta) for stato=da_fatturare.
        r = self.client.get("/?mese=2020-06&stato=da_fatturare")
        self.assertIn("PassatoBeta", r.text)
        self.assertNotIn("PassatoAcme", r.text)

    def test_combinazione_cliente_e_stato(self):
        # Acme is billed, so filtering Acme + da_fatturare yields nothing.
        r = self.client.get(f"/?mese=2020-06&cliente={self.acme_id}&stato=da_fatturare")
        self.assertNotIn("PassatoAcme", r.text)
        self.assertNotIn("PassatoBeta", r.text)
        # Beta + da_fatturare yields Beta only.
        r = self.client.get(f"/?mese=2020-06&cliente={self.beta_id}&stato=da_fatturare")
        self.assertIn("PassatoBeta", r.text)
        self.assertNotIn("PassatoAcme", r.text)

    def test_filtri_conservati_nella_navigazione_mese(self):
        """Month-nav links must carry the active filters and the right months."""
        r = self.client.get(f"/?mese=2020-06&cliente={self.acme_id}")
        self.assertEqual(r.status_code, 200)
        # The "&" is HTML-escaped to "&amp;" in href attributes (the browser
        # reads it back as "&"). Previous month: 2020-05 with the filter kept.
        self.assertIn(f"mese=2020-05&amp;cliente={self.acme_id}", r.text)
        # Next month: 2020-07 with the filter kept.
        self.assertIn(f"mese=2020-07&amp;cliente={self.acme_id}", r.text)

    def test_url_filtrato_prevalorizza_il_form(self):
        """Opening a bookmarked filtered URL must select the right option."""
        r = self.client.get(f"/?mese=2020-06&cliente={self.acme_id}")
        self.assertIn(f'value="{self.acme_id}" selected', r.text)

    def test_richiesta_htmx_serve_solo_il_partial(self):
        """With HX-Request only the results partial is returned (no page chrome,
        no filter form)."""
        r = self.client.get("/?mese=2020-06", headers={"HX-Request": "true"})
        self.assertEqual(r.status_code, 200)
        # Results are present...
        self.assertIn("PassatoAcme", r.text)
        self.assertIn("Giugno 2020", r.text)
        # ...but the full-page chrome and filter form are not.
        self.assertNotIn("Scadenze del mese", r.text)
        self.assertNotIn('class="filtri"', r.text)


class TestFiltriRotteServizi(unittest.TestCase):
    """Services page route: filters on service columns + HTMX partial."""

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
        # data_fine far in the future so these read as "attivo" regardless of
        # the actual date the test suite runs on.
        FUTURO = date(2099, 12, 31)
        _add_servizio(db, acme, "AcmeAttivo", date(2026, 1, 5), "10",
                      data_fine=FUTURO, referente="Mario")
        _add_servizio(db, acme, "AcmeDisdetto", date(2026, 1, 6), "10",
                      data_fine=FUTURO, disdetto=True)
        _add_servizio(db, beta, "BetaAttivo", date(2026, 1, 7), "10",
                      data_fine=FUTURO)
        db.commit()
        self.acme_id = acme.id
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

    def test_filtro_cliente(self):
        r = self.client.get(f"/servizi?cliente={self.acme_id}")
        self.assertEqual(r.status_code, 200)
        self.assertIn("AcmeAttivo", r.text)
        self.assertIn("AcmeDisdetto", r.text)
        self.assertNotIn("BetaAttivo", r.text)

    def test_filtro_stato_servizio(self):
        r = self.client.get("/servizi?stato=disdetto")
        self.assertIn("AcmeDisdetto", r.text)
        self.assertNotIn("AcmeAttivo", r.text)
        self.assertNotIn("BetaAttivo", r.text)

    def test_combinazione_cliente_e_stato(self):
        # Acme + attivo -> only AcmeAttivo (not the disdetto, not Beta).
        r = self.client.get(f"/servizi?cliente={self.acme_id}&stato=attivo")
        self.assertIn("AcmeAttivo", r.text)
        self.assertNotIn("AcmeDisdetto", r.text)
        self.assertNotIn("BetaAttivo", r.text)

    def test_richiesta_htmx_serve_solo_il_partial(self):
        r = self.client.get("/servizi", headers={"HX-Request": "true"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("AcmeAttivo", r.text)
        # No full-page chrome / filter form in the partial.
        self.assertNotIn('class="filtri"', r.text)
        self.assertNotIn("+ Nuovo servizio", r.text)

    def test_righe_espongono_lo_stato_calcolato(self):
        """Every row must carry its computed contract state in data-stato: the
        "nascondi scaduti e disdetti" display preference (client-side, see
        base.html) hides rows by reading that attribute, so dropping it would
        silently break the preference."""
        r = self.client.get("/servizi")
        stati = re.findall(r'<tr id="servizio-\d+" data-stato="([^"]+)"', r.text)
        self.assertEqual(len(stati), 3)
        self.assertEqual(sorted(stati), ["attivo", "attivo", "disdetto"])


if __name__ == "__main__":
    unittest.main()
