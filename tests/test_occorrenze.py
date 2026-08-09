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
from app.models.enums import TipoServizio
from app.models.override_importo import OverrideImporto
from app.models.servizio import Servizio
from app.services import occorrenze

# A very wide interval to mean "no clipping" in tests that don't test clipping.
TUTTO = (date(2000, 1, 1), date(2100, 12, 31))


def _servizio(data_inizio, data_fine, cadenza_mesi, *, importo="10.00", quantita=1,
              durata_mesi=None, rinnovo_automatico=False, disdetto=False,
              preavviso_giorni=30, durata_rinnovo_mesi=None):
    return Servizio(
        cliente_id=1,
        descrizione="Test",
        tipo=TipoServizio.abbonamento,
        data_inizio=data_inizio,
        data_fine=data_fine,
        durata_mesi=durata_mesi,
        rinnovo_automatico=rinnovo_automatico,
        durata_rinnovo_mesi=durata_rinnovo_mesi,
        cadenza_mesi=cadenza_mesi,
        importo=Decimal(importo),
        quantita=quantita,
        valuta="EUR",
        preavviso_giorni=preavviso_giorni,
        disdetto=disdetto,
    )


def _date(occorrenze_list):
    return [o.data_occorrenza for o in occorrenze_list]


class TestOccorrenze(unittest.TestCase):

    # NOTE on the trailing extra date in the tests below: a contract WITHOUT
    # auto-renewal also yields ONE occurrence past its end date — the "renewal
    # proposal" to be confirmed (billed) by the client. See
    # occorrenze_nel_periodo.

    # 1. Simple monthly cadence: 12 occurrences in a year, all on the same
    #    day, plus the renewal proposal.
    def test_mensile_un_anno(self):
        s = _servizio(date(2025, 1, 15), date(2025, 12, 15), cadenza_mesi=1)
        occ = occorrenze.occorrenze_nel_periodo(s, *TUTTO)
        self.assertEqual(len(occ), 13)
        self.assertTrue(all(o.data_occorrenza.day == 15 for o in occ))
        self.assertEqual(occ[0].data_occorrenza, date(2025, 1, 15))
        self.assertEqual(occ[-2].data_occorrenza, date(2025, 12, 15))
        self.assertEqual(occ[-1].data_occorrenza, date(2026, 1, 15))  # renewal proposal

    # 2a. Quarterly cadence.
    def test_trimestrale(self):
        s = _servizio(date(2025, 1, 15), date(2025, 12, 31), cadenza_mesi=3)
        self.assertEqual(
            _date(occorrenze.occorrenze_nel_periodo(s, *TUTTO)),
            [date(2025, 1, 15), date(2025, 4, 15), date(2025, 7, 15), date(2025, 10, 15),
             date(2026, 1, 15)],  # renewal proposal
        )

    # 2b. Yearly cadence across several years.
    def test_annuale(self):
        s = _servizio(date(2025, 1, 15), date(2027, 12, 31), cadenza_mesi=12)
        self.assertEqual(
            _date(occorrenze.occorrenze_nel_periodo(s, *TUTTO)),
            [date(2025, 1, 15), date(2026, 1, 15), date(2027, 1, 15),
             date(2028, 1, 15)],  # renewal proposal
        )

    # 3. data_fine == data_inizio: one occurrence (single payment), plus the
    #    renewal proposal one cadence later.
    def test_pagamento_singolo(self):
        s = _servizio(date(2025, 5, 20), date(2025, 5, 20), cadenza_mesi=1)
        occ = occorrenze.occorrenze_nel_periodo(s, *TUTTO)
        self.assertEqual(_date(occ), [date(2025, 5, 20), date(2025, 6, 20)])

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
                date(2025, 6, 30),  # renewal proposal (clamped)
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
                date(2029, 2, 28),  # renewal proposal (clamped)
            ],
        )

    # 6. Override on one specific occurrence: that one uses override values
    #    (flagged), the others use the service defaults.
    def test_override_su_una_occorrenza(self):
        s = _servizio(date(2025, 1, 10), date(2025, 3, 10), cadenza_mesi=1,
                      importo="10.00", quantita=1)
        s.override_importi.append(
            OverrideImporto(data_occorrenza=date(2025, 2, 10),
                            importo=Decimal("99.00"), quantita=5,
                            override_manuale=True)
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


class TestStatoOccorrenza(unittest.TestCase):
    """Per-occurrence state rows: NULL price fallback, fatturato, visual state."""

    # A state row that exists ONLY to carry the fatturato flag (importo and
    # quantita left NULL) must fall back to the service defaults and must NOT be
    # reported as an override.
    def test_riga_stato_senza_override_usa_default(self):
        s = _servizio(date(2025, 1, 10), date(2025, 1, 10), cadenza_mesi=1,
                      importo="10.00", quantita=2)
        s.override_importi.append(
            OverrideImporto(data_occorrenza=date(2025, 1, 10), fatturato=True)
        )
        o = occorrenze.occorrenze_nel_periodo(s, *TUTTO, oggi=date(2025, 1, 1))[0]
        self.assertFalse(o.da_override)
        self.assertEqual(o.importo, Decimal("10.00"))
        self.assertEqual(o.quantita, 2)
        self.assertEqual(o.totale, Decimal("20.00"))
        self.assertTrue(o.fatturato)

    # A billed occurrence: fatturato True and stato_visivo "fatturato",
    # regardless of the date.
    def test_occorrenza_fatturata(self):
        s = _servizio(date(2025, 1, 10), date(2025, 1, 10), cadenza_mesi=1)
        s.override_importi.append(
            OverrideImporto(data_occorrenza=date(2025, 1, 10), fatturato=True)
        )
        # Even with a past date, "fatturato" wins over "da_fatturare".
        o = occorrenze.occorrenze_nel_periodo(s, *TUTTO, oggi=date(2025, 6, 1))[0]
        self.assertTrue(o.fatturato)
        self.assertEqual(o.stato_visivo, "fatturato")

    # A past, NOT billed occurrence must be flagged "da_fatturare".
    def test_passata_non_fatturata_e_da_fatturare(self):
        s = _servizio(date(2025, 1, 10), date(2025, 1, 10), cadenza_mesi=1)
        o = occorrenze.occorrenze_nel_periodo(s, *TUTTO, oggi=date(2025, 6, 1))[0]
        self.assertFalse(o.fatturato)
        self.assertEqual(o.stato_visivo, "da_fatturare")

    # The "Override" badge (da_override) must reflect ONLY a deliberate manual
    # override, not a plain billing snapshot (override_manuale=False with a
    # frozen importo).
    def test_badge_override_solo_se_manuale(self):
        s = _servizio(date(2025, 1, 10), date(2025, 2, 10), cadenza_mesi=1,
                      importo="10.00", quantita=1)
        # January: a billing snapshot (importo set, override_manuale False).
        s.override_importi.append(
            OverrideImporto(data_occorrenza=date(2025, 1, 10),
                            importo=Decimal("10.00"), quantita=1,
                            override_manuale=False, fatturato=True)
        )
        # February: a deliberate manual override.
        s.override_importi.append(
            OverrideImporto(data_occorrenza=date(2025, 2, 10),
                            importo=Decimal("8.00"), quantita=1,
                            override_manuale=True)
        )
        per_data = {
            o.data_occorrenza: o
            for o in occorrenze.occorrenze_nel_periodo(s, *TUTTO, oggi=date(2025, 1, 1))
        }
        self.assertFalse(per_data[date(2025, 1, 10)].da_override)  # snapshot -> no badge
        self.assertTrue(per_data[date(2025, 2, 10)].da_override)   # manual -> badge

    # Full visual-state precedence on a single monthly service (preavviso 30d).
    def test_stato_visivo_precedenza(self):
        s = _servizio(date(2025, 1, 5), date(2025, 12, 5), cadenza_mesi=1)
        oggi = date(2025, 6, 1)
        per_data = {
            o.data_occorrenza: o
            for o in occorrenze.occorrenze_nel_periodo(s, *TUTTO, oggi=oggi)
        }
        # Past, not billed -> da_fatturare.
        self.assertEqual(per_data[date(2025, 5, 5)].stato_visivo, "da_fatturare")
        # Within 30 days ahead (05/06 is 4 days away) -> in_scadenza.
        self.assertEqual(per_data[date(2025, 6, 5)].stato_visivo, "in_scadenza")
        # Far in the future -> normale.
        self.assertEqual(per_data[date(2025, 12, 5)].stato_visivo, "normale")


class TestDurataERinnovo(unittest.TestCase):
    """Contract duration (calcola_data_fine) and auto-renewal (data_fine_effettiva)."""

    # A 12-month contract starting Jan 1 covers through Dec 31 (inclusive),
    # not Jan 1 of the following year.
    def test_calcola_data_fine_12_mesi(self):
        self.assertEqual(
            occorrenze.calcola_data_fine(date(2026, 1, 1), 12),
            date(2026, 12, 31),
        )

    # Day-clamping applies here too: starting on the 31st, a 1-month contract
    # ends the day before "Feb 31st" clamped to Feb 28 (2026 is not a leap year).
    def test_calcola_data_fine_clamp(self):
        self.assertEqual(
            occorrenze.calcola_data_fine(date(2026, 1, 31), 1),
            date(2026, 2, 27),
        )

    # Without rinnovo_automatico, the effective end date is just data_fine,
    # even if it is long past the reference date.
    def test_senza_rinnovo_resta_invariata(self):
        s = _servizio(date(2025, 1, 1), date(2025, 12, 31), cadenza_mesi=1,
                      durata_mesi=12, rinnovo_automatico=False)
        self.assertEqual(
            occorrenze.data_fine_effettiva(s, riferimento=date(2027, 6, 1)),
            date(2025, 12, 31),
        )

    # With rinnovo_automatico, an expired contract rolls forward by whole
    # durata_mesi blocks until it covers the reference date.
    def test_rinnovo_singolo_blocco(self):
        s = _servizio(date(2025, 1, 1), date(2025, 12, 31), cadenza_mesi=1,
                      durata_mesi=12, rinnovo_automatico=True)
        # Reference date falls in the second yearly block (2026).
        self.assertEqual(
            occorrenze.data_fine_effettiva(s, riferimento=date(2026, 6, 1)),
            date(2026, 12, 31),
        )

    # Several renewal blocks are chained correctly, not just one.
    def test_rinnovo_piu_blocchi(self):
        s = _servizio(date(2025, 1, 1), date(2025, 12, 31), cadenza_mesi=1,
                      durata_mesi=12, rinnovo_automatico=True)
        self.assertEqual(
            occorrenze.data_fine_effettiva(s, riferimento=date(2028, 3, 1)),
            date(2028, 12, 31),
        )

    # Integration: occurrence generation for an auto-renewing service keeps
    # producing occurrences past the originally stored data_fine.
    def test_occorrenze_oltre_data_fine_con_rinnovo(self):
        s = _servizio(date(2025, 1, 15), date(2025, 12, 15), cadenza_mesi=1,
                      durata_mesi=12, rinnovo_automatico=True)
        occ = occorrenze.occorrenze_nel_periodo(s, date(2026, 3, 1), date(2026, 3, 31))
        self.assertEqual(_date(occ), [date(2026, 3, 15)])

    # Without rinnovo_automatico, the contract yields exactly ONE occurrence
    # past its end date — the renewal proposal at the next anniversary — and
    # nothing further: past it, nothing is known until the client confirms.
    def test_solo_la_proposta_di_rinnovo_oltre_data_fine_senza_rinnovo(self):
        s = _servizio(date(2025, 1, 15), date(2025, 12, 15), cadenza_mesi=1,
                      durata_mesi=12, rinnovo_automatico=False)
        occ = occorrenze.occorrenze_nel_periodo(s, date(2026, 1, 1), date(2026, 12, 31))
        self.assertEqual(_date(occ), [date(2026, 1, 15)])  # the proposal only
        occ = occorrenze.occorrenze_nel_periodo(s, date(2026, 3, 1), date(2026, 3, 31))
        self.assertEqual(occ, [])

    # A different renewal-block length than the initial term (e.g. 36 months
    # up front, then 12-month yearly renewals) must be honoured by every
    # renewal step after the first, not just the first one.
    def test_rinnovo_con_durata_diversa_dal_periodo_iniziale(self):
        s = _servizio(date(2022, 3, 1), date(2025, 2, 28), cadenza_mesi=12,
                      durata_mesi=36, rinnovo_automatico=True, durata_rinnovo_mesi=12)
        # First renewal block after the 36-month initial term: 12 months, not 36.
        self.assertEqual(
            occorrenze.data_fine_effettiva(s, riferimento=date(2025, 6, 1)),
            date(2026, 2, 28),
        )
        # Second renewal block, same 12-month step.
        self.assertEqual(
            occorrenze.data_fine_effettiva(s, riferimento=date(2026, 6, 1)),
            date(2027, 2, 28),
        )

    # durata_rinnovo_mesi unset falls back to durata_mesi (today's behaviour,
    # unaffected by the new field).
    def test_rinnovo_senza_durata_dedicata_usa_durata_mesi(self):
        s = _servizio(date(2025, 1, 1), date(2025, 12, 31), cadenza_mesi=1,
                      durata_mesi=12, rinnovo_automatico=True, durata_rinnovo_mesi=None)
        self.assertEqual(
            occorrenze.data_fine_effettiva(s, riferimento=date(2026, 6, 1)),
            date(2026, 12, 31),
        )


class TestPropostaRinnovo(unittest.TestCase):
    """The "renewal proposal": a contract WITHOUT auto-renewal yields one
    occurrence past its end date (the next anniversary, data_fine + 1 day) —
    the renewal the client still has to confirm. Auto-renewing and cancelled
    contracts must not propose anything."""

    def test_proposta_alla_scadenza(self):
        s = _servizio(date(2025, 8, 24), date(2026, 8, 23), cadenza_mesi=12,
                      durata_mesi=12, rinnovo_automatico=False)
        occ = occorrenze.occorrenze_nel_periodo(s, *TUTTO, oggi=date(2026, 8, 9))
        self.assertEqual(_date(occ), [date(2025, 8, 24), date(2026, 8, 24)])
        # 15 days out, within the default 30-day warning window.
        self.assertEqual(occ[-1].stato_visivo, "in_scadenza")

    def test_nessuna_proposta_con_rinnovo_automatico(self):
        s = _servizio(date(2025, 8, 24), date(2026, 8, 23), cadenza_mesi=12,
                      durata_mesi=12, rinnovo_automatico=True)
        # Clip to the stored contract period: the auto-renewing contract keeps
        # generating occurrences by rolling forward, but never an extra
        # "proposal" beyond what the window asks for.
        occ = occorrenze.occorrenze_nel_periodo(s, date(2025, 1, 1), date(2026, 8, 23))
        self.assertEqual(_date(occ), [date(2025, 8, 24)])

    def test_nessuna_proposta_se_disdetto(self):
        s = _servizio(date(2025, 8, 24), date(2026, 8, 23), cadenza_mesi=12,
                      durata_mesi=12, rinnovo_automatico=False, disdetto=True)
        occ = occorrenze.occorrenze_nel_periodo(s, *TUTTO)
        self.assertEqual(_date(occ), [date(2025, 8, 24)])

    def test_contratto_degenere_senza_occorrenze(self):
        # End before start: no occurrences, and in particular no proposal.
        s = _servizio(date(2025, 6, 1), date(2025, 1, 1), cadenza_mesi=1)
        self.assertEqual(occorrenze.occorrenze_nel_periodo(s, *TUTTO), [])


class TestDurataMesiCongelata(unittest.TestCase):
    """durata_mesi_congelata: the value that freezes the CURRENT effective
    end date in place of the original term, when auto-renewal is turned off
    (see aggiorna_servizio in app/routes/servizi.py)."""

    # No renewal elapsed yet: freezing changes nothing.
    def test_nessun_rinnovo_trascorso(self):
        s = _servizio(date(2025, 1, 1), date(2025, 12, 31), cadenza_mesi=1,
                      durata_mesi=12, rinnovo_automatico=True)
        congelata = occorrenze.durata_mesi_congelata(s, riferimento=date(2025, 6, 1))
        self.assertEqual(congelata, 12)
        self.assertEqual(occorrenze.calcola_data_fine(s.data_inizio, congelata), s.data_fine)

    # Several elapsed renewal blocks must all be folded into a single
    # durata_mesi that reproduces the exact same effective end date.
    def test_diversi_rinnovi_trascorsi(self):
        s = _servizio(date(2022, 3, 1), date(2023, 2, 28), cadenza_mesi=12,
                      durata_mesi=12, rinnovo_automatico=True)
        riferimento = date(2026, 8, 9)
        atteso = occorrenze.data_fine_effettiva(s, riferimento)
        congelata = occorrenze.durata_mesi_congelata(s, riferimento)
        self.assertEqual(occorrenze.calcola_data_fine(s.data_inizio, congelata), atteso)
        # This is the bug being fixed: freezing must NOT collapse back to the
        # original single-year term.
        self.assertNotEqual(atteso, s.data_fine)

    # A renewal step different from the initial term (36 then 12-month
    # blocks) must still fold into one consistent frozen duration.
    def test_con_durata_rinnovo_diversa(self):
        s = _servizio(date(2022, 3, 1), date(2025, 2, 28), cadenza_mesi=12,
                      durata_mesi=36, rinnovo_automatico=True, durata_rinnovo_mesi=12)
        riferimento = date(2027, 6, 1)
        atteso = occorrenze.data_fine_effettiva(s, riferimento)
        congelata = occorrenze.durata_mesi_congelata(s, riferimento)
        self.assertEqual(occorrenze.calcola_data_fine(s.data_inizio, congelata), atteso)

    # Without rinnovo_automatico there is nothing to freeze: durata_mesi is
    # returned unchanged.
    def test_senza_rinnovo_automatico_invariata(self):
        s = _servizio(date(2025, 1, 1), date(2025, 12, 31), cadenza_mesi=1,
                      durata_mesi=12, rinnovo_automatico=False)
        self.assertEqual(occorrenze.durata_mesi_congelata(s, riferimento=date(2027, 1, 1)), 12)


class TestStatoContratto(unittest.TestCase):
    """Contract-level state (stato_contratto): attivo/in_scadenza/scaduto/disdetto."""

    def test_attivo(self):
        s = _servizio(date(2025, 1, 1), date(2025, 12, 31), cadenza_mesi=1, preavviso_giorni=30)
        self.assertEqual(occorrenze.stato_contratto(s, oggi=date(2025, 6, 1)), "attivo")

    def test_in_scadenza_entro_il_preavviso(self):
        s = _servizio(date(2025, 1, 1), date(2025, 12, 31), cadenza_mesi=1, preavviso_giorni=30)
        self.assertEqual(occorrenze.stato_contratto(s, oggi=date(2025, 12, 15)), "in_scadenza")

    def test_scaduto(self):
        s = _servizio(date(2025, 1, 1), date(2025, 12, 31), cadenza_mesi=1, preavviso_giorni=30)
        self.assertEqual(occorrenze.stato_contratto(s, oggi=date(2026, 1, 15)), "scaduto")

    # disdetto always wins, even over dates that would otherwise say "attivo".
    def test_disdetto_vince_su_tutto(self):
        s = _servizio(date(2025, 1, 1), date(2027, 12, 31), cadenza_mesi=1, disdetto=True)
        self.assertEqual(occorrenze.stato_contratto(s, oggi=date(2025, 6, 1)), "disdetto")

    # An auto-renewing contract's effective end date never falls behind
    # "oggi", so it can never be "scaduto" — at most "in_scadenza" right
    # before it rolls into the next block.
    def test_rinnovo_automatico_non_scade_mai(self):
        s = _servizio(date(2025, 1, 1), date(2025, 12, 31), cadenza_mesi=1,
                      durata_mesi=12, rinnovo_automatico=True, preavviso_giorni=30)
        self.assertEqual(occorrenze.stato_contratto(s, oggi=date(2028, 3, 1)), "attivo")

    # A disdetto contract's unbilled occurrences must never show the urgent
    # "da_fatturare"/"in_scadenza" alerts (see _stato_visivo), so the operator
    # is never nagged to invoice something the customer has cancelled.
    def test_disdetto_non_genera_da_fatturare(self):
        s = _servizio(date(2025, 1, 10), date(2025, 1, 10), cadenza_mesi=1, disdetto=True)
        o = occorrenze.occorrenze_nel_periodo(s, *TUTTO, oggi=date(2025, 6, 1))[0]
        self.assertEqual(o.stato_visivo, "normale")

    # ...but a disdetto occurrence that WAS already billed still shows
    # "fatturato" — disdetto only suppresses the alert states, not billing.
    def test_disdetto_gia_fatturato_resta_fatturato(self):
        s = _servizio(date(2025, 1, 10), date(2025, 1, 10), cadenza_mesi=1, disdetto=True)
        s.override_importi.append(
            OverrideImporto(data_occorrenza=date(2025, 1, 10), fatturato=True)
        )
        o = occorrenze.occorrenze_nel_periodo(s, *TUTTO, oggi=date(2025, 6, 1))[0]
        self.assertEqual(o.stato_visivo, "fatturato")


if __name__ == "__main__":
    unittest.main()
