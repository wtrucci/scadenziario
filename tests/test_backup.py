"""
Tests for the nightly database backup (app/services/backup.py).

The point of these is not that a file appears: it is that a copy which cannot
be trusted is never left behind looking like a good one, and that rotation
cannot be talked into deleting everything.

Run with:  python -m pytest tests/test_backup.py
Everything happens in a temporary directory; the real database is untouched.
"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.services import backup


def _crea_db(percorso: Path, righe: int = 3) -> None:
    con = sqlite3.connect(percorso)
    con.execute("create table t (id integer primary key, v text)")
    con.executemany("insert into t (v) values (?)", [(f"riga {i}",) for i in range(righe)])
    con.commit()
    con.close()


class TestPercorsoDb(unittest.TestCase):

    def test_url_relativo(self):
        self.assertEqual(backup.percorso_db("sqlite:///./data/x.db"), Path("data/x.db"))

    def test_url_assoluto(self):
        self.assertEqual(backup.percorso_db("sqlite:////var/lib/x.db"), Path("/var/lib/x.db"))

    def test_altro_database_e_rifiutato(self):
        """On PostgreSQL this job has no business running: say so instead of
        producing something meaningless."""
        with self.assertRaises(backup.BackupFallito):
            backup.percorso_db("postgresql://localhost/scadenziario")


class TestBackup(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.db = self.base / "scadenziario.db"
        self.dest = self.base / "backup"
        _crea_db(self.db)
        self.url = f"sqlite:///{self.db}"

    def tearDown(self):
        self._tmp.cleanup()

    def test_la_copia_contiene_gli_stessi_dati(self):
        copia = backup.esegui_backup(self.url, self.dest, 30)
        con = sqlite3.connect(copia)
        self.assertEqual(con.execute("select count(*) from t").fetchone()[0], 3)
        con.close()

    def test_la_cartella_viene_creata(self):
        self.assertFalse(self.dest.exists())
        backup.esegui_backup(self.url, self.dest, 30)
        self.assertTrue(self.dest.is_dir())

    def test_database_mancante(self):
        self.db.unlink()
        with self.assertRaises(backup.BackupFallito):
            backup.esegui_backup(self.url, self.dest, 30)

    def test_una_copia_corrotta_viene_scartata(self):
        """The integrity check is the whole point: if the snapshot comes out
        unreadable it must be deleted, never left in the folder where it would
        later be mistaken for a restore point."""
        originale = backup._verifica
        backup._verifica = lambda percorso: "malformed database schema"
        try:
            with self.assertRaises(backup.BackupFallito):
                backup.esegui_backup(self.url, self.dest, 30)
        finally:
            backup._verifica = originale
        self.assertEqual(backup.elenco_backup(self.dest), [])

    def test_due_backup_ravvicinati_non_si_sovrascrivono(self):
        """The timestamp has one-second resolution, so two runs in the same
        second must still get separate files — otherwise the second one owns
        (and on failure deletes) the first one's copy."""
        primo = backup.esegui_backup(self.url, self.dest, 30)
        secondo = backup.esegui_backup(self.url, self.dest, 30)
        self.assertNotEqual(primo, secondo)
        self.assertEqual(len(backup.elenco_backup(self.dest)), 2)

    def test_una_copia_corrotta_non_travolge_quelle_buone(self):
        buona = backup.esegui_backup(self.url, self.dest, 30)
        originale = backup._verifica
        backup._verifica = lambda percorso: "database disk image is malformed"
        try:
            with self.assertRaises(backup.BackupFallito):
                backup.esegui_backup(self.url, self.dest, 30)
        finally:
            backup._verifica = originale
        self.assertEqual(backup.elenco_backup(self.dest), [buona])


class TestRotazione(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dest = Path(self._tmp.name)
        # Names carry the timestamp, so they sort chronologically as strings.
        self.tutti = []
        for giorno in range(1, 6):
            p = self.dest / f"scadenziario-2026080{giorno}-030000.db"
            p.write_bytes(b"x")
            self.tutti.append(p)

    def tearDown(self):
        self._tmp.cleanup()

    def test_tiene_le_piu_recenti(self):
        eliminati = backup.ruota(self.dest, 2)
        self.assertEqual(eliminati, self.tutti[:3])
        self.assertEqual(backup.elenco_backup(self.dest), self.tutti[3:])

    def test_sotto_la_soglia_non_elimina_nulla(self):
        self.assertEqual(backup.ruota(self.dest, 10), [])
        self.assertEqual(len(backup.elenco_backup(self.dest)), 5)

    def test_valore_non_valido_non_cancella_tutto(self):
        """A misconfigured BACKUP_KEEP must never read as "delete everything"."""
        for valore in (0, -1):
            self.assertEqual(backup.ruota(self.dest, valore), [])
            self.assertEqual(len(backup.elenco_backup(self.dest)), 5)

    def test_ignora_i_file_estranei(self):
        (self.dest / "appunti.txt").write_text("non toccarmi")
        backup.ruota(self.dest, 1)
        self.assertTrue((self.dest / "appunti.txt").exists())

    def test_cartella_inesistente(self):
        self.assertEqual(backup.elenco_backup(self.dest / "manca"), [])


if __name__ == "__main__":
    unittest.main()
