"""
Tests for the customer-name rules (app/services/clienti.py and POST /clienti):

* a customer entered twice, differing only in casing or spacing, is refused;
* a customer whose name merely LOOKS like an existing one is warned about once
  and saved on confirmation — two similar names can be two real customers;
* the database index refuses a case-insensitive duplicate even if the
  application check is bypassed.

Run with:  python -m unittest discover -s tests
Uses an isolated in-memory SQLite database; the real DB is never touched.
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  (registers all tables on Base.metadata)
from app.database import Base
from app.models.cliente import Cliente
from app.services.clienti import chiave_similarita, normalizza_nome


def _make_engine():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return engine


class TestNormalizzazione(unittest.TestCase):

    def test_spazi_ripuliti_ma_maiuscole_intatte(self):
        # "IBM" must not become "Ibm": only spacing is normalised.
        self.assertEqual(normalizza_nome("  IBM   Italia \n"), "IBM Italia")

    def test_forma_giuridica_ignorata_nella_similarita(self):
        self.assertEqual(chiave_similarita("Acme S.r.l."), chiave_similarita("ACME srl"))

    def test_ordine_delle_parole_ignorato(self):
        self.assertEqual(chiave_similarita("Rossi Mario"), chiave_similarita("Mario Rossi"))

    def test_nomi_diversi_restano_diversi(self):
        self.assertNotEqual(chiave_similarita("Acme"), chiave_similarita("Acmi"))


class TestRotteClienti(unittest.TestCase):

    def setUp(self):
        from fastapi.testclient import TestClient

        from app.database import get_db
        from app.dependencies import require_login
        from app.main import app

        self.engine = _make_engine()
        self.SessionLocal = sessionmaker(bind=self.engine)

        db = self.SessionLocal()
        db.add(Cliente(nome="Acme Srl", attivo=True))
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

    def _nomi(self) -> list[str]:
        db = self.SessionLocal()
        try:
            return list(db.scalars(select(Cliente.nome).order_by(Cliente.nome)))
        finally:
            db.close()

    def _crea(self, nome: str, **extra):
        dati = {"nome": nome, "note": "", "attivo": "on", **extra}
        return self.client.post("/clienti", data=dati, follow_redirects=False)

    # --- hard duplicate --------------------------------------------------

    def test_duplicato_solo_maiuscole_e_spazi_rifiutato(self):
        r = self._crea("  acme   SRL ")
        self.assertEqual(r.status_code, 422)
        self.assertIn("Esiste già un cliente", r.text)
        self.assertEqual(self._nomi(), ["Acme Srl"])

    def test_conferma_non_aggira_il_duplicato(self):
        # "Salva comunque" confirms a similarity warning, never an exact duplicate.
        r = self._crea("ACME SRL", conferma_simili="1")
        self.assertEqual(r.status_code, 422)
        self.assertEqual(self._nomi(), ["Acme Srl"])

    def test_modifica_senza_toccare_il_nome_non_e_duplicato_di_se_stesso(self):
        db = self.SessionLocal()
        cliente_id = db.scalar(select(Cliente.id))
        db.close()
        r = self.client.post(
            f"/clienti/{cliente_id}/modifica",
            data={"nome": "Acme Srl", "note": "nota", "attivo": "on"},
            follow_redirects=False,
        )
        self.assertEqual(r.status_code, 303)

    # --- similarity warning ----------------------------------------------

    def test_nome_simile_avvisa_e_non_salva(self):
        r = self._crea("Acme S.r.l.")
        self.assertEqual(r.status_code, 422)
        self.assertIn("nome molto simile", r.text)
        self.assertEqual(self._nomi(), ["Acme Srl"])

    def test_nome_simile_salvato_dopo_conferma(self):
        r = self._crea("Acme S.r.l.", conferma_simili="1")
        self.assertEqual(r.status_code, 303)
        self.assertEqual(self._nomi(), ["Acme S.r.l.", "Acme Srl"])

    def test_nome_diverso_salvato_senza_avvisi(self):
        r = self._crea("Beta Spa")
        self.assertEqual(r.status_code, 303)
        self.assertIn("Beta Spa", self._nomi())

    def test_nome_salvato_con_spazi_normalizzati(self):
        self._crea("  Gamma    Group  ", conferma_simili="1")
        self.assertIn("Gamma Group", self._nomi())

    # --- search -----------------------------------------------------------

    def test_ricerca_per_nome(self):
        self._crea("Beta Impianti", conferma_simili="1")
        r = self.client.get("/clienti", params={"q": "beta"})
        self.assertIn("Beta Impianti", r.text)
        self.assertNotIn("Acme Srl", r.text)

    def test_ricerca_nelle_note(self):
        self.client.post(
            "/clienti",
            data={"nome": "Gamma", "note": "sede di Cherasco", "attivo": "on",
                  "conferma_simili": "1"},
            follow_redirects=False,
        )
        r = self.client.get("/clienti", params={"q": "cherasco"})
        self.assertIn("Gamma", r.text)
        self.assertNotIn("Acme Srl", r.text)

    def test_ricerca_senza_risultati_lo_dice(self):
        r = self.client.get("/clienti", params={"q": "inesistente"})
        self.assertIn("Nessun cliente corrisponde", r.text)

    def test_ricerca_vuota_mostra_tutti(self):
        self._crea("Beta Impianti", conferma_simili="1")
        r = self.client.get("/clienti", params={"q": "  "})
        self.assertIn("Acme Srl", r.text)
        self.assertIn("Beta Impianti", r.text)

    def test_richiesta_htmx_torna_solo_i_risultati(self):
        r = self.client.get("/clienti", headers={"HX-Request": "true"})
        self.assertNotIn("<html", r.text)
        self.assertIn("Acme Srl", r.text)

    # --- quick create from the service form (POST /clienti/rapido) -------

    def _rapido(self, nome, **extra):
        return self.client.post("/clienti/rapido", data={"nome": nome, "note": "", **extra})

    def test_rapido_crea_e_segnala_il_nuovo_cliente(self):
        import json
        r = self._rapido("Beta Impianti")
        self.assertEqual(r.status_code, 200)
        evento = json.loads(r.headers["HX-Trigger"])["clienteCreato"]
        self.assertEqual(evento["nome"], "Beta Impianti")
        self.assertIn("Beta Impianti", self._nomi())
        # The dialog comes back empty, ready for the next customer.
        self.assertIn('value=""', r.text)

    def test_rapido_duplicato_rifiutato_senza_evento(self):
        r = self._rapido("ACME srl")
        # 200, not 422: HTMX would not swap an error response, and the user
        # must see why the save did not happen.
        self.assertEqual(r.status_code, 200)
        self.assertIn("Esiste già un cliente", r.text)
        self.assertNotIn("HX-Trigger", r.headers)
        self.assertEqual(self._nomi(), ["Acme Srl"])

    def test_rapido_simile_chiede_conferma(self):
        r = self._rapido("Acme S.r.l.")
        self.assertIn("nome molto simile", r.text)
        self.assertIn("Salva comunque", r.text)
        self.assertNotIn("HX-Trigger", r.headers)

    def test_rapido_simile_confermato_viene_creato(self):
        r = self._rapido("Acme S.r.l.", conferma_simili="1")
        self.assertIn("HX-Trigger", r.headers)
        self.assertIn("Acme S.r.l.", self._nomi())

    def test_rapido_nome_accentato_sopravvive_nell_header(self):
        import json
        r = self._rapido("Società Cooperativa Àlfa")
        evento = json.loads(r.headers["HX-Trigger"])["clienteCreato"]
        self.assertEqual(evento["nome"], "Società Cooperativa Àlfa")

    def test_rapido_nome_vuoto(self):
        r = self._rapido("   ")
        self.assertIn("Il nome è obbligatorio", r.text)
        self.assertNotIn("HX-Trigger", r.headers)

    def test_form_servizio_ha_il_pulsante_e_la_finestra(self):
        h = self.client.get("/servizi/nuovo").text
        self.assertIn('id="nuovo-cliente-dialog"', h)
        self.assertIn('hx-post="/clienti/rapido"', h)
        # The dialog form must not be nested inside the service form.
        self.assertLess(h.index("</form>"), h.index('id="nuovo-cliente-dialog"'))

    def test_la_finestra_nuovo_cliente_non_e_una_finestra_filtri(self):
        """app.js closes every dialog.filtri-dialog on the first change event.
        On a dialog with a field to fill in, that fires when the field loses
        focus — i.e. when the user clicks "Crea cliente" — and swallowed the
        click: the request never left. Found only by driving a real browser."""
        import re
        h = self.client.get("/servizi/nuovo").text
        tag = re.search(r'<dialog id="nuovo-cliente-dialog"[^>]*>', h).group(0)
        self.assertNotIn("filtri-dialog", tag)

    # --- database safety net ---------------------------------------------

    def test_indice_unico_blocca_il_duplicato_scritto_a_mano(self):
        db = self.SessionLocal()
        try:
            db.add(Cliente(nome="ACME SRL", attivo=True))
            with self.assertRaises(IntegrityError):
                db.commit()
        finally:
            db.rollback()
            db.close()


if __name__ == "__main__":
    unittest.main()
