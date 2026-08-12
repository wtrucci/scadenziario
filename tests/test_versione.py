"""
Tests for the version shown in the interface.

The version is only useful if it is the SAME everywhere: what the page shows,
what the API docs report, and what the release is tagged. These pin the wiring
so a future refactor cannot quietly leave one of them behind.

Run with:  python -m pytest tests/test_versione.py
"""
from __future__ import annotations

import re
import unittest
from types import SimpleNamespace

from fastapi.testclient import TestClient

import app as pacchetto
from app.config import settings
from app.dependencies import require_login
from app.main import app as fastapi_app


class TestVersione(unittest.TestCase):

    def test_formato_semver(self):
        self.assertRegex(pacchetto.__version__, r"^\d+\.\d+\.\d+([-+].+)?$")

    def test_settings_riflette_il_pacchetto(self):
        """Without an APP_VERSION override, the interface shows the version
        baked into the package."""
        self.assertEqual(settings.APP_VERSION, pacchetto.__version__)

    def test_app_fastapi_espone_la_stessa_versione(self):
        self.assertEqual(fastapi_app.version, settings.APP_VERSION)

    def test_compare_nella_pagina(self):
        fastapi_app.dependency_overrides[require_login] = lambda: SimpleNamespace(
            username="tester"
        )
        try:
            client = TestClient(fastapi_app)
            r = client.get("/login")
            self.assertEqual(r.status_code, 200)
            self.assertIn(f"v{settings.APP_VERSION}", r.text)
        finally:
            fastapi_app.dependency_overrides.clear()


if __name__ == "__main__":
    unittest.main()
