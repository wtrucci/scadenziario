"""
Tests for GET /riepilogo/export/pdf-mese — the whole month as one PDF.

It must cover exactly what the riepilogo page and the CSV cover (cancelled
contracts and already-billed occurrences out), so the three always agree on
the month's total. The PDF bytes are compressed, so the tests look at what the
route hands to the generator, plus an end-to-end check that a real PDF comes
out.

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
from app.models.override_importo import OverrideImporto
from app.models.servizio import Servizio

MESE = "2026-10"


class TestRiepilogoPdfMese(unittest.TestCase):

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
        zeta = Cliente(nome="Zeta", attivo=True)
        db.add_all([beta, alfa, zeta])
        db.flush()

        def servizio(cliente, descrizione, giorno, importo, quantita, **extra):
            dati = dict(
                cliente_id=cliente.id, descrizione=descrizione, tipo=TipoServizio.abbonamento,
                data_scadenza=date(2026, 10, giorno), cadenza_mesi=12,
                importo=Decimal(importo), quantita=quantita, valuta="EUR",
                preavviso_giorni=30, rinnovo_automatico=True,
            )
            dati.update(extra)
            return Servizio(**dati)

        fatturato = servizio(alfa, "Già fatturata", 20, "999.00", 1)
        db.add_all([
            servizio(beta, "Sentinel One", 6, "2.75", 30),                    # 82.50
            servizio(beta, "Fortigate 40F", 11, "120.00", 1),                 # 120.00
            servizio(alfa, "Licenza", 5, "400.00", 1),                        # 400.00
            fatturato,
            servizio(zeta, "Disdetto", 3, "500.00", 1, disdetto=True),        # out
        ])
        db.flush()
        db.add(OverrideImporto(servizio_id=fatturato.id, data_occorrenza=date(2026, 10, 20),
                               fatturato=True, override_manuale=False))
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

    def _cattura(self, mese=MESE):
        preso = {}

        def finto(gruppi, etichetta, totale, generato_il):
            preso.update(gruppi=gruppi, etichetta=etichetta, totale=totale)
            return b"%PDF-finto"

        with patch("app.routes.dashboard.genera_pdf_mese", finto):
            r = self.client.get("/riepilogo/export/pdf-mese", params={"mese": mese})
        self.assertEqual(r.status_code, 200)
        return preso

    def test_produce_un_pdf_vero(self):
        r = self.client.get("/riepilogo/export/pdf-mese", params={"mese": MESE})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["content-type"], "application/pdf")
        self.assertTrue(r.content.startswith(b"%PDF"))
        self.assertIn(f"riepilogo_{MESE}.pdf", r.headers["content-disposition"])

    def test_clienti_in_ordine_alfabetico(self):
        preso = self._cattura()
        self.assertEqual([g.cliente.nome for g in preso["gruppi"]], ["Alfa", "Beta Memory"])

    def test_esclusi_disdetti_e_gia_fatturati(self):
        preso = self._cattura()
        descrizioni = [r.servizio.descrizione for g in preso["gruppi"] for r in g.righe]
        self.assertNotIn("Disdetto", descrizioni)
        self.assertNotIn("Già fatturata", descrizioni)

    def test_totali(self):
        preso = self._cattura()
        subtotali = {g.cliente.nome: g.subtotale for g in preso["gruppi"]}
        self.assertEqual(subtotali, {"Alfa": Decimal("400.00"), "Beta Memory": Decimal("202.50")})
        self.assertEqual(preso["totale"], Decimal("602.50"))

    def test_stesso_totale_della_pagina(self):
        preso = self._cattura()
        h = self.client.get("/riepilogo", params={"mese": MESE}).text
        self.assertIn(f"{preso['totale']:.2f}", h)

    def test_mese_vuoto_produce_comunque_un_pdf(self):
        r = self.client.get("/riepilogo/export/pdf-mese", params={"mese": "2026-03"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.content.startswith(b"%PDF"))

    def test_il_pulsante_e_nella_pagina_col_mese_giusto(self):
        h = self.client.get("/riepilogo", params={"mese": MESE}).text
        self.assertIn(f'href="/riepilogo/export/pdf-mese?mese={MESE}"', h)

    def test_il_pdf_del_singolo_cliente_funziona_ancora(self):
        from sqlalchemy import select
        db = self.SessionLocal()
        cid = db.scalar(select(Cliente.id).where(Cliente.nome == "Beta Memory"))
        db.close()
        r = self.client.get("/riepilogo/export/pdf", params={"cliente_id": cid, "mese": MESE})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.content.startswith(b"%PDF"))


if __name__ == "__main__":
    unittest.main()
