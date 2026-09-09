"""
Tests for the interface script served as a static file.

The behaviour of every data table (sorting, columns, pagination) lives in
app/static/js/app.js. It used to be inline in base.html; as a file it can be
cached by the browser, which is only safe because the URL carries the app
version — otherwise a release could leave users running last week's script
against this week's markup.

Run with:  python -m unittest discover -s tests
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

import app as pacchetto
from app.dependencies import require_login
from app.main import app

APP_JS = Path(__file__).resolve().parent.parent / "app" / "static" / "js" / "app.js"


class TestAssetJs(unittest.TestCase):

    def setUp(self):
        app.dependency_overrides[require_login] = lambda: SimpleNamespace(username="tester")
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()

    def test_la_pagina_carica_lo_script(self):
        h = self.client.get("/login").text
        self.assertRegex(h, r'<script src="[^"]*static/js/app\.js\?v=[^"]*" defer>')

    def test_url_versionata_con_la_versione_corrente(self):
        h = self.client.get("/login").text
        self.assertIn(f"app.js?v={pacchetto.__version__}", h)

    def test_lo_script_viene_servito(self):
        r = self.client.get("/static/js/app.js")
        self.assertEqual(r.status_code, 200)
        self.assertIn("initTabelleDati", r.text)

    def test_niente_javascript_rimasto_inline_a_parte_il_tema(self):
        """base.html keeps exactly one inline script: the theme, which must run
        before the page paints or it would flash the wrong colours."""
        sorgente = (Path(__file__).resolve().parent.parent
                    / "app" / "templates" / "base.html").read_text()
        inline = re.findall(r"<script>(.*?)</script>", sorgente, re.S)
        self.assertEqual(len(inline), 1)
        self.assertIn("data-theme", inline[0])

    def test_lo_script_non_contiene_sintassi_jinja(self):
        """A static file is never rendered: a leftover {{ ... }} would ship to
        the browser verbatim."""
        testo = APP_JS.read_text()
        self.assertNotIn("{{", testo)
        self.assertNotIn("{%", testo)


if __name__ == "__main__":
    unittest.main()
