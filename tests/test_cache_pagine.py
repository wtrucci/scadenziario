"""
Tests for the caching headers on pages.

Every page is a live view of the database: billing an occurrence changes what
the riepilogo must show. A browser that reuses a stored copy (most visibly the
back button, which restores a page from memory without asking the server)
would show work that has already been done as still pending — so HTML must be
served as "no-store", while /static keeps its normal caching.

Run with:  python -m unittest discover -s tests
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.dependencies import require_login
from app.main import app


class TestCacheControl(unittest.TestCase):

    def setUp(self):
        app.dependency_overrides[require_login] = lambda: SimpleNamespace(username="tester")
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()

    def test_pagina_html_non_va_conservata(self):
        r = self.client.get("/login")
        self.assertEqual(r.status_code, 200)
        self.assertIn("no-store", r.headers.get("cache-control", ""))

    def test_static_resta_memorizzabile(self):
        r = self.client.get("/static/css/style.css")
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("no-store", r.headers.get("cache-control", ""))


if __name__ == "__main__":
    unittest.main()
