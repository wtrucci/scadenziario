"""
Tests for POST /servizi (crea_servizio), focused on the "past occurrences are
billed by definition" rule (see _fattura_occorrenze_passate in
app/routes/servizi.py): a service entered into the system with occurrences
already in the past (importing pre-existing licenses, catching up on
equipment installed months ago, ...) must not surface those as outstanding
"da fatturare" alerts — the system only watches a service from today forward.

Run with:  python -m unittest discover -s tests
Uses an isolated in-memory SQLite database; the real DB is never touched.
"""
from __future__ import annotations

import unittest
from datetime import date, timedelta
from types import SimpleNamespace

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  (registers all tables on Base.metadata)
from app.database import Base
from app.models.cliente import Cliente
from app.models.enums import TipoServizio
from app.models.override_importo import OverrideImporto
from app.models.servizio import Servizio

OGGI = date.today()


def _make_engine():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return engine


class TestCreaServizio(unittest.TestCase):

    def setUp(self):
        from fastapi.testclient import TestClient

        from app.database import get_db
        from app.dependencies import require_login
        from app.main import app

        self.engine = _make_engine()
        self.SessionLocal = sessionmaker(bind=self.engine)

        db = self.SessionLocal()
        cliente = Cliente(nome="Acme", attivo=True)
        db.add(cliente)
        db.commit()
        self.cliente_id = cliente.id
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

    # --- helpers ---------------------------------------------------------

    def _crea(self, *, data_scadenza: date, durata_impegno_mesi="", cadenza_mesi=12,
               rinnovo_automatico=False):
        payload = {
            "cliente_id": str(self.cliente_id),
            "descrizione": "Licenza test",
            "tipo": "licenza",
            "data_scadenza": data_scadenza.isoformat(),
            "durata_impegno_mesi": str(durata_impegno_mesi),
            "cadenza_mesi": str(cadenza_mesi),
            "importo": "10.00",
            "quantita": "1",
            "valuta": "EUR",
            "preavviso_giorni": "30",
        }
        if rinnovo_automatico:
            payload["rinnovo_automatico"] = "on"
        r = self.client.post("/servizi", data=payload, follow_redirects=False)
        self.assertEqual(r.status_code, 303, r.text)
        db = self.SessionLocal()
        try:
            return db.scalars(select(Servizio).order_by(Servizio.id.desc())).first().id
        finally:
            db.close()

    def _stati(self, servizio_id: int) -> list[OverrideImporto]:
        db = self.SessionLocal()
        try:
            return db.scalars(
                select(OverrideImporto).where(OverrideImporto.servizio_id == servizio_id)
            ).all()
        finally:
            db.close()

    # --- tests -------------------------------------------------------------

    def test_occorrenza_passata_viene_fatturata_automaticamente(self):
        # 400 days back at a yearly cadence: the cycle opening and its first
        # anniversary are both in the past, so both get settled.
        servizio_id = self._crea(data_scadenza=OGGI - timedelta(days=400), rinnovo_automatico=True)
        stati = self._stati(servizio_id)
        self.assertEqual(len(stati), 2)
        self.assertTrue(all(st.fatturato for st in stati))

    def test_occorrenza_futura_non_viene_toccata(self):
        servizio_id = self._crea(data_scadenza=OGGI + timedelta(days=10))
        stati = self._stati(servizio_id)
        self.assertEqual(stati, [])

    def test_occorrenza_di_oggi_viene_fatturata(self):
        # "oggi" counts as already past for this rule (<=), not upcoming.
        servizio_id = self._crea(data_scadenza=OGGI, rinnovo_automatico=True)
        stati = self._stati(servizio_id)
        self.assertEqual(len(stati), 1)
        self.assertTrue(stati[0].fatturato)

    def test_rinnovo_automatico_fattura_tutti_gli_arretrati_non_il_futuro(self):
        # Started ~4 years ago, monthly cadence, auto-renewing: many past
        # occurrences accumulate, but only the ones up to today should be
        # auto-billed — future ones must remain open for real alerts/notifications.
        servizio_id = self._crea(
            data_scadenza=OGGI - timedelta(days=4 * 365), cadenza_mesi=1,
            rinnovo_automatico=True,
        )
        stati = self._stati(servizio_id)
        self.assertGreater(len(stati), 1)
        self.assertTrue(all(s.fatturato for s in stati))
        self.assertTrue(all(s.data_occorrenza <= OGGI for s in stati))

    def test_non_sovrascrive_una_riga_di_stato_gia_esistente(self):
        # Simulates: a state row was already created for some other reason
        # (e.g. a manual override) before this occurrence would otherwise be
        # auto-billed — the rule must never clobber it.
        db = self.SessionLocal()
        cliente = db.get(Cliente, self.cliente_id)
        data_inizio = OGGI - timedelta(days=5)
        s = Servizio(
            cliente=cliente, descrizione="Preesistente", tipo=TipoServizio.licenza,
            data_scadenza=data_inizio, cadenza_mesi=1, rinnovo_automatico=True,
            importo=10, quantita=1, valuta="EUR", preavviso_giorni=30,
        )
        db.add(s)
        db.flush()
        db.add(OverrideImporto(
            servizio_id=s.id, data_occorrenza=data_inizio,
            fatturato=False, override_manuale=True, note="Da tenere aperta",
        ))
        db.commit()
        servizio_id = s.id
        db.close()

        from app.routes.servizi import _fattura_occorrenze_passate
        db = self.SessionLocal()
        try:
            s = db.get(Servizio, servizio_id)
            _fattura_occorrenze_passate(db, s, OGGI)
            db.commit()
            stato = db.scalars(
                select(OverrideImporto).where(OverrideImporto.servizio_id == servizio_id)
            ).one()
            self.assertFalse(stato.fatturato)
            self.assertEqual(stato.note, "Da tenere aperta")
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
