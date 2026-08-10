"""
Tests for the occurrence-calculation engine (app/services/occorrenze.py).

This is the heart of the app, so the tests cover the tricky cases: cadence
stepping, the "target day" anti-drift rule, leap years, commitments billed in
instalments, the pending renewal that stops generation, per-occurrence
overrides and interval clipping.

The Servizio and OverrideImporto objects are built in memory (no DB needed):
the engine only reads attributes and the override_importi collection.

Run with:  python -m unittest discover -s tests
"""
from __future__ import annotations

import unittest
from datetime import date, timedelta
from decimal import Decimal

import app.models  # noqa: F401  (registers mappers / relationships)
from app.models.enums import TipoServizio
from app.models.override_importo import OverrideImporto
from app.models.servizio import Servizio
from app.services import occorrenze

# A very wide interval to mean "no clipping" in tests that don't test clipping.
TUTTO = (date(2000, 1, 1), date(2100, 12, 31))


def _servizio(data_scadenza, cadenza_mesi, *, importo="10.00", quantita=1,
              durata_impegno_mesi=None, rinnovo_automatico=True, disdetto=False,
              preavviso_giorni=30):
    """A service in the new model: one anchor date plus a cadence.

    ``rinnovo_automatico`` defaults to True so tests that only care about the
    date arithmetic aren't cut short by the pending-renewal stop rule.
    """
    return Servizio(
        cliente_id=1,
        descrizione="Test",
        tipo=TipoServizio.abbonamento,
        data_scadenza=data_scadenza,
        cadenza_mesi=cadenza_mesi,
        durata_impegno_mesi=durata_impegno_mesi,
        rinnovo_automatico=rinnovo_automatico,
        importo=Decimal(importo),
        quantita=quantita,
        valuta="EUR",
        preavviso_giorni=preavviso_giorni,
        disdetto=disdetto,
    )


def _date(occorrenze_list):
    return [o.data_occorrenza for o in occorrenze_list]


def _fattura(servizio, *date_occ):
    """Mark occurrences as billed, the way the app's state rows do."""
    for d in date_occ:
        servizio.override_importi.append(OverrideImporto(data_occorrenza=d, fatturato=True))


class TestCadenza(unittest.TestCase):
    """Occurrences step cadenza_mesi months from data_scadenza."""

    def test_mensile(self):
        s = _servizio(date(2025, 1, 10), cadenza_mesi=1)
        occ = _date(occorrenze.occorrenze_nel_periodo(s, date(2025, 1, 1), date(2025, 5, 31)))
        self.assertEqual(occ, [date(2025, 1, 10), date(2025, 2, 10), date(2025, 3, 10),
                               date(2025, 4, 10), date(2025, 5, 10)])

    def test_trimestrale(self):
        s = _servizio(date(2025, 1, 15), cadenza_mesi=3)
        occ = _date(occorrenze.occorrenze_nel_periodo(s, date(2025, 1, 1), date(2025, 12, 31)))
        self.assertEqual(occ, [date(2025, 1, 15), date(2025, 4, 15),
                               date(2025, 7, 15), date(2025, 10, 15)])

    def test_annuale(self):
        s = _servizio(date(2025, 6, 1), cadenza_mesi=12)
        occ = _date(occorrenze.occorrenze_nel_periodo(s, date(2025, 1, 1), date(2028, 12, 31)))
        self.assertEqual(occ, [date(2025, 6, 1), date(2026, 6, 1),
                               date(2027, 6, 1), date(2028, 6, 1)])

    def test_cadenza_non_valida(self):
        s = _servizio(date(2025, 1, 1), cadenza_mesi=0)
        with self.assertRaises(ValueError):
            occorrenze.occorrenze_nel_periodo(s, *TUTTO)


class TestGiornoTarget(unittest.TestCase):
    """The day-of-month is clamped, never drifted: it must come back as soon as
    a month is long enough again."""

    def test_il_31_non_deriva(self):
        s = _servizio(date(2025, 1, 31), cadenza_mesi=1)
        occ = _date(occorrenze.occorrenze_nel_periodo(s, date(2025, 1, 1), date(2025, 5, 31)))
        self.assertEqual(occ, [date(2025, 1, 31), date(2025, 2, 28), date(2025, 3, 31),
                               date(2025, 4, 30), date(2025, 5, 31)])

    def test_29_febbraio_bisestile(self):
        s = _servizio(date(2024, 2, 29), cadenza_mesi=12)
        occ = _date(occorrenze.occorrenze_nel_periodo(s, date(2024, 1, 1), date(2028, 12, 31)))
        # Non-leap years clamp to the 28th, 2028 gets the 29th back.
        self.assertEqual(occ, [date(2024, 2, 29), date(2025, 2, 28), date(2026, 2, 28),
                               date(2027, 2, 28), date(2028, 2, 29)])


class TestIntervallo(unittest.TestCase):
    """Only occurrences inside [data_da, data_a] are returned."""

    def test_ritaglio(self):
        s = _servizio(date(2025, 1, 10), cadenza_mesi=1)
        occ = _date(occorrenze.occorrenze_nel_periodo(s, date(2025, 3, 1), date(2025, 4, 30)))
        self.assertEqual(occ, [date(2025, 3, 10), date(2025, 4, 10)])

    def test_intervallo_prima_della_scadenza(self):
        """Occurrences only ever run forward: nothing exists before the anchor."""
        s = _servizio(date(2025, 6, 1), cadenza_mesi=1)
        self.assertEqual(occorrenze.occorrenze_nel_periodo(s, date(2024, 1, 1), date(2025, 5, 31)), [])


class TestImpegno(unittest.TestCase):
    """durata_impegno_mesi: a commitment billed in instalments.

    The real case: an 8-seat subscription committed for a year but invoiced
    every month.
    """

    def test_rate_dentro_l_impegno(self):
        s = _servizio(date(2026, 7, 1), cadenza_mesi=1, durata_impegno_mesi=12)
        occ = _date(occorrenze.occorrenze_nel_periodo(s, date(2026, 7, 1), date(2027, 6, 30)))
        self.assertEqual(len(occ), 12)
        self.assertEqual(occ[0], date(2026, 7, 1))
        self.assertEqual(occ[-1], date(2027, 6, 1))

    def test_impegno_vuoto_significa_una_fattura_per_scadenza(self):
        """The common case: every occurrence is itself a renewal."""
        s = _servizio(date(2026, 7, 1), cadenza_mesi=12, durata_impegno_mesi=None)
        occ = _date(occorrenze.occorrenze_nel_periodo(s, date(2026, 1, 1), date(2029, 12, 31)))
        self.assertEqual(occ, [date(2026, 7, 1), date(2027, 7, 1),
                               date(2028, 7, 1), date(2029, 7, 1)])

    def test_un_periodo_pagato_in_anticipo_non_fattura_al_suo_interno(self):
        """A three-year licence paid up front, renewing yearly: nothing may be
        invoiced inside the years already paid for."""
        s = _servizio(date(2028, 10, 14), cadenza_mesi=12)
        occ = _date(occorrenze.occorrenze_nel_periodo(s, date(2026, 1, 1), date(2028, 10, 13)))
        self.assertEqual(occ, [])


class TestRinnovoDaConfermare(unittest.TestCase):
    """Without rinnovo_automatico, generation stops at the first unbilled cycle
    opening: that is the renewal the customer still has to confirm, and nothing
    past it is known."""

    def test_si_ferma_al_rinnovo_pendente(self):
        s = _servizio(date(2026, 8, 24), cadenza_mesi=12, rinnovo_automatico=False)
        occ = _date(occorrenze.occorrenze_nel_periodo(s, *TUTTO))
        self.assertEqual(occ, [date(2026, 8, 24)])

    def test_fatturare_il_rinnovo_fa_ripartire_la_generazione(self):
        """Billing the pending renewal IS the confirmation — no contract field
        is touched, the engine simply carries on to the next cycle."""
        s = _servizio(date(2026, 8, 24), cadenza_mesi=12, rinnovo_automatico=False)
        _fattura(s, date(2026, 8, 24))
        occ = _date(occorrenze.occorrenze_nel_periodo(s, *TUTTO))
        self.assertEqual(occ, [date(2026, 8, 24), date(2027, 8, 24)])

    def test_le_rate_dell_impegno_confermato_restano_certe(self):
        """Once a commitment's opening is billed, its instalments are certain;
        generation stops only at the NEXT cycle."""
        s = _servizio(date(2026, 7, 1), cadenza_mesi=1, durata_impegno_mesi=12,
                      rinnovo_automatico=False)
        _fattura(s, date(2026, 7, 1))
        occ = _date(occorrenze.occorrenze_nel_periodo(s, *TUTTO))
        self.assertEqual(len(occ), 13)          # 12 instalments + next renewal
        self.assertEqual(occ[-1], date(2027, 7, 1))

    def test_disdetto_non_propone_nulla(self):
        s = _servizio(date(2026, 8, 24), cadenza_mesi=12,
                      rinnovo_automatico=False, disdetto=True)
        self.assertEqual(occorrenze.occorrenze_nel_periodo(s, *TUTTO), [])

    def test_rinnovo_automatico_prosegue_senza_chiedere(self):
        s = _servizio(date(2026, 8, 24), cadenza_mesi=12, rinnovo_automatico=True)
        occ = _date(occorrenze.occorrenze_nel_periodo(s, date(2026, 1, 1), date(2030, 12, 31)))
        self.assertEqual(len(occ), 5)


class TestStatoContratto(unittest.TestCase):
    OGGI = date(2026, 8, 10)

    def test_attivo(self):
        s = _servizio(date(2027, 1, 1), cadenza_mesi=12)
        self.assertEqual(occorrenze.stato_contratto(s, oggi=self.OGGI), "attivo")

    def test_in_scadenza_entro_il_preavviso(self):
        """The cycle opens in 14 days and has not been paid: cover ends the day
        before, inside the warning window."""
        s = _servizio(date(2026, 8, 24), cadenza_mesi=12, rinnovo_automatico=False)
        self.assertEqual(occorrenze.stato_contratto(s, oggi=self.OGGI), "in_scadenza")

    def test_scaduto(self):
        s = _servizio(date(2023, 9, 24), cadenza_mesi=12, rinnovo_automatico=False)
        self.assertEqual(occorrenze.stato_contratto(s, oggi=self.OGGI), "scaduto")

    def test_rinnovo_automatico_non_scade_mai(self):
        """It renews whether or not you got round to invoicing, so a late
        invoice shows as "da fatturare", never as an expired contract."""
        s = _servizio(date(2020, 1, 1), cadenza_mesi=12, rinnovo_automatico=True)
        self.assertEqual(occorrenze.stato_contratto(s, oggi=self.OGGI), "attivo")

    def test_impegno_pagato_copre_fino_alla_sua_fine(self):
        s = _servizio(date(2026, 7, 1), cadenza_mesi=1, durata_impegno_mesi=12,
                      rinnovo_automatico=False)
        _fattura(s, date(2026, 7, 1))
        self.assertEqual(occorrenze.stato_contratto(s, oggi=self.OGGI), "attivo")

    def test_disdetto_vince_su_tutto(self):
        s = _servizio(date(2027, 1, 1), cadenza_mesi=12, disdetto=True)
        self.assertEqual(occorrenze.stato_contratto(s, oggi=self.OGGI), "disdetto")


class TestStatoVisivo(unittest.TestCase):
    OGGI = date(2026, 8, 10)

    def test_passata_non_fatturata_e_da_fatturare(self):
        s = _servizio(date(2026, 6, 1), cadenza_mesi=1)
        occ = occorrenze.occorrenze_nel_periodo(s, date(2026, 6, 1), date(2026, 6, 30),
                                                oggi=self.OGGI)
        self.assertEqual(occ[0].stato_visivo, "da_fatturare")

    def test_fatturata(self):
        s = _servizio(date(2026, 6, 1), cadenza_mesi=1)
        _fattura(s, date(2026, 6, 1))
        occ = occorrenze.occorrenze_nel_periodo(s, date(2026, 6, 1), date(2026, 6, 30),
                                                oggi=self.OGGI)
        self.assertEqual(occ[0].stato_visivo, "fatturato")

    def test_disdetto_non_genera_da_fatturare(self):
        """Never nag to invoice something the customer cancelled."""
        s = _servizio(date(2026, 6, 1), cadenza_mesi=1, disdetto=True)
        occ = occorrenze.occorrenze_nel_periodo(s, date(2026, 6, 1), date(2026, 6, 30),
                                                oggi=self.OGGI)
        self.assertEqual(occ[0].stato_visivo, "normale")


class TestImporti(unittest.TestCase):
    """Totals stay Decimal and respect per-occurrence state rows."""

    def test_totale_di_default(self):
        s = _servizio(date(2025, 1, 1), cadenza_mesi=1, importo="12.50", quantita=4)
        occ = occorrenze.occorrenze_nel_periodo(s, date(2025, 1, 1), date(2025, 1, 31))
        self.assertEqual(occ[0].totale, Decimal("50.00"))

    def test_override_manuale(self):
        s = _servizio(date(2025, 1, 1), cadenza_mesi=1, importo="10.00")
        s.override_importi.append(OverrideImporto(
            data_occorrenza=date(2025, 1, 1), importo=Decimal("7.00"), override_manuale=True))
        occ = occorrenze.occorrenze_nel_periodo(s, date(2025, 1, 1), date(2025, 1, 31))
        self.assertEqual(occ[0].totale, Decimal("7.00"))
        self.assertTrue(occ[0].da_override)

    def test_snapshot_di_fatturazione_non_e_un_override(self):
        """A price frozen at billing time is the normal price, just pinned: it
        must not show the "Override" badge."""
        s = _servizio(date(2025, 1, 1), cadenza_mesi=1, importo="10.00")
        s.override_importi.append(OverrideImporto(
            data_occorrenza=date(2025, 1, 1), importo=Decimal("9.00"),
            fatturato=True, override_manuale=False))
        occ = occorrenze.occorrenze_nel_periodo(s, date(2025, 1, 1), date(2025, 1, 31))
        self.assertEqual(occ[0].totale, Decimal("9.00"))
        self.assertFalse(occ[0].da_override)

    def test_quantita_override(self):
        s = _servizio(date(2025, 1, 1), cadenza_mesi=1, importo="10.00", quantita=2)
        s.override_importi.append(OverrideImporto(
            data_occorrenza=date(2025, 1, 1), quantita=5, override_manuale=True))
        occ = occorrenze.occorrenze_nel_periodo(s, date(2025, 1, 1), date(2025, 1, 31))
        self.assertEqual(occ[0].totale, Decimal("50.00"))


class TestDateHelper(unittest.TestCase):
    def test_aggiungi_mesi_clamp(self):
        self.assertEqual(occorrenze.aggiungi_mesi(date(2025, 1, 31), 1), date(2025, 2, 28))
        self.assertEqual(occorrenze.aggiungi_mesi(date(2024, 1, 31), 1), date(2024, 2, 29))

    def test_calcola_data_fine(self):
        """Inclusive end: 12 months from Jan 1 covers through Dec 31."""
        self.assertEqual(occorrenze.calcola_data_fine(date(2025, 1, 1), 12), date(2025, 12, 31))

    def test_fine_impegno(self):
        s = _servizio(date(2026, 7, 1), cadenza_mesi=1, durata_impegno_mesi=12)
        self.assertEqual(occorrenze.fine_impegno(s), date(2027, 6, 30))

    def test_scadenza_congelata_pinna_il_ciclo_raggiunto(self):
        """Turning auto-renewal off must pin the cycle where the automatic
        renewals actually carried it, not snap back years."""
        s = _servizio(date(2020, 3, 1), cadenza_mesi=12, rinnovo_automatico=True)
        self.assertEqual(
            occorrenze.scadenza_congelata(s, date(2026, 8, 10)), date(2026, 3, 1))


if __name__ == "__main__":
    unittest.main()
