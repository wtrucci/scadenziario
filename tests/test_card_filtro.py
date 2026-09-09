"""
Tests for the dashboard summary cards used as filters.

Each card counts exactly one filter, so clicking it must apply that filter,
clicking the active one must clear it, and the customer/referente filters
already in place must survive the click — otherwise the cards would reset the
view instead of narrowing it.

Run with:  python -m unittest discover -s tests
Uses an isolated in-memory SQLite database; the real DB is never touched.
"""
from __future__ import annotations

import re
import unittest
from datetime import date, timedelta
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

OGGI = date.today()
MESE = OGGI.strftime("%Y-%m")


class TestCardFiltro(unittest.TestCase):

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
        self.cliente_id = cliente.id
        db.add(Servizio(
            cliente_id=cliente.id, descrizione="Licenza", tipo=TipoServizio.licenza,
            data_scadenza=OGGI, cadenza_mesi=12, importo=Decimal("100"), quantita=1,
            valuta="EUR", preavviso_giorni=30, rinnovo_automatico=True, referente="Rossi",
        ))
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

    def _card(self, testo: str, stato: str) -> str:
        """The href of the card whose label is `testo`."""
        h = self.client.get("/", params={"mese": MESE, **({"stato": stato} if stato else {})}).text
        blocco = h[h.index('<div class="summary-cards">'):h.index("</div>", h.index("Totale da fatturare"))]
        for pezzo in re.findall(r'<a class="summary-card[^"]*"\s+href="([^"]*)"(.*?)</a>', blocco, re.S):
            if testo in pezzo[1]:
                return pezzo[0]
        self.fail(f"card {testo!r} non trovata")

    def test_card_filtra(self):
        self.assertIn("stato=da_fatturare", self._card("Da fatturare", ""))
        self.assertIn("stato=in_scadenza", self._card("In scadenza", ""))
        self.assertIn("stato=fatturato", self._card("Fatturato", ""))

    def test_card_attiva_azzera_il_filtro(self):
        href = self._card("Da fatturare", "da_fatturare")
        self.assertNotIn("stato=", href)
        self.assertIn("mese=", href)

    def test_card_attiva_e_segnalata(self):
        h = self.client.get("/", params={"mese": MESE, "stato": "da_fatturare"}).text
        self.assertIn("summary-card-attiva", h)
        self.assertIn('aria-pressed="true"', h)

    def test_le_card_conservano_gli_altri_filtri(self):
        h = self.client.get(
            "/", params={"mese": MESE, "referente": "Rossi"}
        ).text
        blocco = h[h.index('<div class="summary-cards">'):]
        self.assertIn("referente=Rossi", blocco)

    def test_il_filtro_della_card_restringe_la_tabella(self):
        # The single occurrence is due today: not billed, so it is counted as
        # "da fatturare" and hidden by the "fatturato" filter.
        con_filtro = self.client.get("/", params={"mese": MESE, "stato": "fatturato"}).text
        self.assertNotIn("Licenza", con_filtro)
        senza = self.client.get("/", params={"mese": MESE}).text
        self.assertIn("Licenza", senza)

    def test_totale_non_e_un_link(self):
        h = self.client.get("/", params={"mese": MESE}).text
        blocco = h[h.index("Totale da fatturare") - 200:h.index("Totale da fatturare")]
        self.assertNotIn("<a class=\"summary-card", blocco)


if __name__ == "__main__":
    unittest.main()
