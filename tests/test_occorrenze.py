"""
Tests for the occurrence-calculation engine (app/services/occorrenze.py).

This is the heart of the app, so the tests are written first and cover the
tricky cases: month cadence, the "target day" anti-drift rule, leap years,
per-occurrence overrides, interval clipping and the empty/edge cases.

The Servizio and OverrideImporto objects are built in memory (no DB needed):
the engine only reads attributes and the override_importi collection.

Run with:  python -m unittest discover -s tests
"""
from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

import app.models  # noqa: F401  (registers mappers / relationships)
from app.models.enums import StatoServizio, TipoServizio
from app.models.override_importo import OverrideImporto
from app.models.servizio import Servizio
from app.services import occorrenze

# A very wide interval to mean "no clipping" in tests that don't test clipping.
TUTTO = (date(2000, 1, 1), date(2100, 12, 31))


def _servizio(data_inizio, data_fine, cadenza_mesi, *, importo="10.00", quantita=1):
    return Servizio(
        cliente_id=1,
        descrizione="Test",
        tipo=TipoServizio.abbonamento,
        data_inizio=data_inizio,
        data_fine=data_fine,
        cadenza_mesi=cadenza_mesi,
        importo=Decimal(importo),
        quantita=quantita,
        valuta="EUR",
        preavviso_giorni=30,
        stato=StatoServizio.attivo,
    )


def _date(occorrenze_list):
    return [o.data_occorrenza for o in occorrenze_list]


class TestOccorrenze(unittest.TestCase):

    # 1. Simple monthly cadence: 12 occurrences in a year, all on the same day.
    def test_mensile_un_anno(self):
        s = _servizio(date(2025, 1, 15), date(2025, 12, 15), cadenza_mesi=1)
        occ = occorrenze.occorrenze_nel_periodo(s, *TUTTO)
        self.assertEqual(len(occ), 12)
        self.assertTrue(all(o.data_occorrenza.day == 15 for o in occ))
        self.assertEqual(occ[0].data_occorrenza, date(2025, 1, 15))
        self.assertEqual(occ[-1].data_occorrenza, date(2025, 12, 15))

    # 2a. Quarterly cadence.
    def test_trimestrale(self):
        s = _servizio(date(2025, 1, 15), date(2025, 12, 31), cadenza_mesi=3)
        self.assertEqual(
            _date(occorrenze.occorrenze_nel_periodo(s, *TUTTO)),
            [date(2025, 1, 15), date(2025, 4, 15), date(2025, 7, 15), date(2025, 10, 15)],
        )

    # 2b. Yearly cadence across several years.
    def test_annuale(self):
        s = _servizio(date(2025, 1, 15), date(2027, 12, 31), cadenza_mesi=12)
        self.assertEqual(
            _date(occorrenze.occorrenze_nel_periodo(s, *TUTTO)),
            [date(2025, 1, 15), date(2026, 1, 15), date(2027, 1, 15)],
        )

    # 3. data_fine == data_inizio: exactly one occurrence (single payment).
    def test_pagamento_singolo(self):
        s = _servizio(date(2025, 5, 20), date(2025, 5, 20), cadenza_mesi=1)
        occ = occorrenze.occorrenze_nel_periodo(s, *TUTTO)
        self.assertEqual(_date(occ), [date(2025, 5, 20)])

    # 4. Day-31 case: Feb clamps to 28 (non-leap), but March returns to 31
    #    (the target day must NOT drift).
    def test_giorno_31_non_perde_il_target(self):
        s = _servizio(date(2025, 1, 31), date(2025, 5, 31), cadenza_mesi=1)
        self.assertEqual(
            _date(occorrenze.occorrenze_nel_periodo(s, *TUTTO)),
            [
                date(2025, 1, 31),
                date(2025, 2, 28),  # clamped (2025 is not a leap year)
                date(2025, 3, 31),  # back to 31, no drift
                date(2025, 4, 30),  # clamped (April has 30 days)
                date(2025, 5, 31),  # back to 31
            ],
        )

    # 5. Feb 29 start: leap year keeps 29, non-leap years clamp to 28,
    #    and the target day returns to 29 on the next leap year.
    def test_29_febbraio_bisestile_e_non(self):
        s = _servizio(date(2024, 2, 29), date(2028, 12, 31), cadenza_mesi=12)
        self.assertEqual(
            _date(occorrenze.occorrenze_nel_periodo(s, *TUTTO)),
            [
                date(2024, 2, 29),  # leap
                date(2025, 2, 28),  # clamped
                date(2026, 2, 28),  # clamped
                date(2027, 2, 28),  # clamped
                date(2028, 2, 29),  # leap again -> target day recovered
            ],
        )

    # 6. Override on one specific occurrence: that one uses override values
    #    (flagged), the others use the service defaults.
    def test_override_su_una_occorrenza(self):
        s = _servizio(date(2025, 1, 10), date(2025, 3, 10), cadenza_mesi=1,
                      importo="10.00", quantita=1)
        s.override_importi.append(
            OverrideImporto(data_occorrenza=date(2025, 2, 10),
                            importo=Decimal("99.00"), quantita=5)
        )
        occ = occorrenze.occorrenze_nel_periodo(s, *TUTTO)
        per_data = {o.data_occorrenza: o for o in occ}

        gennaio = per_data[date(2025, 1, 10)]
        self.assertFalse(gennaio.da_override)
        self.assertEqual(gennaio.importo, Decimal("10.00"))
        self.assertEqual(gennaio.quantita, 1)
        self.assertEqual(gennaio.totale, Decimal("10.00"))

        febbraio = per_data[date(2025, 2, 10)]
        self.assertTrue(febbraio.da_override)
        self.assertEqual(febbraio.importo, Decimal("99.00"))
        self.assertEqual(febbraio.quantita, 5)
        self.assertEqual(febbraio.totale, Decimal("495.00"))

        marzo = per_data[date(2025, 3, 10)]
        self.assertFalse(marzo.da_override)
        self.assertEqual(marzo.totale, Decimal("10.00"))

    # 7. Interval clipping: occurrences outside [data_da, data_a] are excluded.
    def test_intervallo_taglia(self):
        s = _servizio(date(2025, 1, 15), date(2025, 12, 15), cadenza_mesi=1)
        occ = occorrenze.occorrenze_nel_periodo(s, date(2025, 3, 1), date(2025, 5, 31))
        self.assertEqual(
            _date(occ),
            [date(2025, 3, 15), date(2025, 4, 15), date(2025, 5, 15)],
        )

    # 7b. Clamp vs interval filter: the filter must use the ACTUAL clamped date
    #     (28/02), never the theoretical target day (31). Service starts on the
    #     31st, so February's occurrence is clamped to 28/02/2025.
    def test_filtro_usa_data_clampata_non_target(self):
        s = _servizio(date(2025, 1, 31), date(2025, 3, 31), cadenza_mesi=1)

        # Interval covering only 28/02: the clamped occurrence must be INCLUDED.
        # (The theoretical "31 Feb" does not exist and would never match here, so
        # inclusion proves the filter compares the real 28/02 date.)
        solo_feb = occorrenze.occorrenze_nel_periodo(s, date(2025, 2, 28), date(2025, 2, 28))
        self.assertEqual(_date(solo_feb), [date(2025, 2, 28)])

        # Interval ending on 27/02: the 28/02 occurrence falls just outside and
        # must be EXCLUDED based on its real date, not the target day 31.
        fino_al_27 = occorrenze.occorrenze_nel_periodo(s, date(2025, 2, 1), date(2025, 2, 27))
        self.assertEqual(_date(fino_al_27), [])

        # Interval starting on 01/03: 28/02 is before it and excluded; the 31/03
        # occurrence (target day recovered) is included.
        da_marzo = occorrenze.occorrenze_nel_periodo(s, date(2025, 3, 1), date(2025, 3, 31))
        self.assertEqual(_date(da_marzo), [date(2025, 3, 31)])

    # 8. data_fine < data_inizio: no occurrences, no infinite loop.
    def test_fine_prima_di_inizio(self):
        s = _servizio(date(2025, 6, 1), date(2025, 1, 1), cadenza_mesi=1)
        self.assertEqual(occorrenze.occorrenze_nel_periodo(s, *TUTTO), [])

    # Defensive: a non-positive cadence would never advance -> reject it.
    def test_cadenza_non_valida(self):
        s = _servizio(date(2025, 1, 1), date(2025, 12, 31), cadenza_mesi=0)
        with self.assertRaises(ValueError):
            occorrenze.occorrenze_nel_periodo(s, *TUTTO)

    # Monetary values must stay Decimal, never float.
    def test_valori_sono_decimal(self):
        s = _servizio(date(2025, 1, 1), date(2025, 1, 1), cadenza_mesi=1,
                      importo="12.34", quantita=3)
        o = occorrenze.occorrenze_nel_periodo(s, *TUTTO)[0]
        self.assertIsInstance(o.importo, Decimal)
        self.assertIsInstance(o.totale, Decimal)
        self.assertEqual(o.totale, Decimal("37.02"))


if __name__ == "__main__":
    unittest.main()
