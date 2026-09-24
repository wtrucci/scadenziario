"""
Tests for GET /servizi/export/pdf — the services list as filtered on screen.

The export must contain exactly what the page lists (same filters, same
search, plus the browser-side "nascondi scaduti e disdetti" preference passed
as ?nascondi=1), grouped by customer, with totals built on each contract's
annual value — summing a monthly fee with a yearly one would give a number
that belongs to no period.

The PDF bytes are compressed, so the tests look at what the route hands to
the generator, plus one end-to-end check that a real PDF comes out.

Run with:  python -m unittest discover -s tests
Uses an isolated in-memory SQLite database; the real DB is never touched.
"""
from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  (registers all tables on Base.metadata)
from app.database import Base
from app.models.cliente import Cliente
from app.models.enums import TipoServizio
from app.models.servizio import Servizio


class TestExportServiziPdf(unittest.TestCase):

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
        beta = Cliente(nome="Beta Memory", attivo=True)
        alfa = Cliente(nome="Alfa", attivo=True)
        db.add_all([beta, alfa])
        db.flush()
        self.beta_id = beta.id

        def servizio(cliente, descrizione, referente, cadenza, importo, quantita, **extra):
            dati = dict(
                cliente_id=cliente.id, descrizione=descrizione, tipo=TipoServizio.abbonamento,
                data_scadenza=date(2026, 10, 1), cadenza_mesi=cadenza, importo=Decimal(importo),
                quantita=quantita, valuta="EUR", preavviso_giorni=30, rinnovo_automatico=True,
                referente=referente,
            )
            dati.update(extra)
            return Servizio(**dati)

        db.add_all([
            servizio(beta, "Sentinel One", "Perino Infissi", 1, "2.75", 12),   # 396.00/anno
            servizio(beta, "Sentinel One", "Mecsider", 1, "2.75", 10),         # 330.00/anno
            servizio(beta, "Fortigate 40F", None, 12, "400.00", 1),            # 400.00/anno
            servizio(alfa, "Licenza", None, 12, "100.00", 1, disdetto=True),   # disdetto
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

    def _cattura(self, **params):
        """Call the export and return (gruppi, filtri) as handed to the generator."""
        preso = {}

        def finto(gruppi, filtri, generato_il):
            preso["gruppi"], preso["filtri"] = gruppi, filtri
            return b"%PDF-finto"

        with patch("app.routes.servizi.genera_pdf_servizi", finto):
            r = self.client.get("/servizi/export/pdf", params=params)
        self.assertEqual(r.status_code, 200)
        return preso["gruppi"], preso["filtri"]

    def test_produce_un_pdf_vero(self):
        r = self.client.get("/servizi/export/pdf")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["content-type"], "application/pdf")
        self.assertTrue(r.content.startswith(b"%PDF"))
        self.assertIn("attachment", r.headers["content-disposition"])

    def test_raggruppato_per_cliente_in_ordine_alfabetico(self):
        gruppi, _ = self._cattura()
        self.assertEqual([g.cliente.nome for g in gruppi], ["Alfa", "Beta Memory"])

    def test_dentro_il_gruppo_per_descrizione_poi_referente(self):
        gruppi, _ = self._cattura()
        beta = gruppi[1]
        self.assertEqual(
            [(r.servizio.descrizione, r.servizio.referente) for r in beta.righe],
            [("Fortigate 40F", None), ("Sentinel One", "Mecsider"),
             ("Sentinel One", "Perino Infissi")],
        )

    def test_subtotale_sul_valore_annuo(self):
        gruppi, _ = self._cattura()
        # 396 + 330 + 400, NOT 33 + 27.50 + 400 (the per-occurrence totals).
        self.assertEqual(gruppi[1].subtotali(), {"EUR": Decimal("1126.00")})

    def test_il_disdetto_e_elencato_ma_non_conta(self):
        gruppi, _ = self._cattura()
        alfa = gruppi[0]
        self.assertEqual(len(alfa.righe), 1)
        self.assertIsNone(alfa.righe[0].valore_annuo)
        self.assertEqual(alfa.subtotali(), {})

    def test_rispetta_i_filtri_della_pagina(self):
        gruppi, filtri = self._cattura(cliente=str(self.beta_id), q="sentinel")
        self.assertEqual(len(gruppi), 1)
        self.assertEqual(len(gruppi[0].righe), 2)
        self.assertIn("Cliente: Beta Memory", filtri)
        self.assertIn("Ricerca: sentinel", filtri)

    def test_nascondi_toglie_scaduti_e_disdetti(self):
        gruppi, filtri = self._cattura(nascondi="1")
        self.assertEqual([g.cliente.nome for g in gruppi], ["Beta Memory"])
        self.assertIn("esclusi scaduti e disdetti", filtri)

    def test_nessun_risultato_produce_comunque_un_pdf(self):
        r = self.client.get("/servizi/export/pdf", params={"q": "inesistente"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.content.startswith(b"%PDF"))

    def test_il_pulsante_e_nella_pagina(self):
        h = self.client.get("/servizi").text
        self.assertIn("data-export-pdf", h)


class TestValoreAnnuo(unittest.TestCase):

    def _s(self, cadenza, importo, quantita, disdetto=False):
        return SimpleNamespace(cadenza_mesi=cadenza, importo=Decimal(importo),
                               quantita=quantita, disdetto=disdetto)

    def test_cadenze(self):
        from app.services.servizi import valore_annuo
        self.assertEqual(valore_annuo(self._s(1, "2.75", 12)), Decimal("396.00"))
        self.assertEqual(valore_annuo(self._s(3, "100", 1)), Decimal("400.00"))
        self.assertEqual(valore_annuo(self._s(12, "400", 1)), Decimal("400.00"))
        self.assertEqual(valore_annuo(self._s(36, "1000", 1)), Decimal("333.33"))

    def test_resta_decimal(self):
        from app.services.servizi import valore_annuo
        self.assertIsInstance(valore_annuo(self._s(1, "2.75", 12)), Decimal)

    def test_disdetto_non_vale_nulla(self):
        from app.services.servizi import valore_annuo
        self.assertIsNone(valore_annuo(self._s(12, "400", 1, disdetto=True)))


if __name__ == "__main__":
    unittest.main()
