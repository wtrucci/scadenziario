"""
PDF generation.

- ``genera_pdf_cliente``: the billing summary, one document per customer,
  meant to be handed to that customer (or their referente) as a billing recap
  for the month.
- ``genera_pdf_servizi``: the services list as filtered on screen, grouped by
  customer, with each contract's value over a year and the totals.

Built with fpdf2 (pure Python, no OS-level dependencies), consistent with the
project's "no build step" philosophy. Core PDF fonts (Helvetica) only support
Latin-1/CP1252, so amounts are printed with the ISO currency code ("10.00
EUR"), not a currency symbol — matching the CSV export.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from fpdf import FPDF

from app.models.cliente import Cliente
from app.models.servizio import Servizio
from app.services.riepilogo import GruppoCliente

# Core PDF fonts (Helvetica) can only encode Latin-1, and fpdf2 refuses the
# whole document when one character falls outside it. Text typed on a phone
# or a Mac, or pasted from Word, carries typographic characters Latin-1 does
# not have — a referente "Atelier dell’auto" (curly apostrophe) was enough to
# turn a customer's PDF into an error page. They get their plain equivalents;
# anything else unrepresentable becomes "?", so one odd character costs one
# odd character, never the document.
_SOSTITUZIONI = str.maketrans({
    "\u2018": "'", "\u2019": "'",      # ‘ ’
    "\u201c": '"', "\u201d": '"',      # “ ”
    "\u2013": "-", "\u2014": "-",      # – —
    "\u2026": "...",                   # …
    "\u20ac": "EUR",                   # €
    "\u00a0": " ",                     # non-breaking space
})


def _latin1(testo: str) -> str:
    return testo.translate(_SOSTITUZIONI).encode("latin-1", "replace").decode("latin-1")


class _PDF(FPDF):
    """FPDF whose text always fits the core fonts.

    normalize_text is the one place fpdf2 routes every string through —
    cells, titles, and get_string_width used by _tronca to measure — so
    cleaning it here covers every call, including ones added later.
    """

    def normalize_text(self, text: str) -> str:
        return super().normalize_text(_latin1(text))


# Column widths (mm) for the occurrence table, summing to the usable page
# width (A4 minus the default 10mm margins on each side = 190mm).
# Referente gets the widest text column: on a reseller's summary the customer
# is the same on every row and the referente is what tells the rows apart
# ("Boccardo Amministrazioni", "Studio Legale Palermiti"), so it is the column
# that must not be cramped.
# "R" marks the columns that read as numbers and are right-aligned.
_COLONNE = [
    ("Descrizione", 44, "L"),
    ("Scadenza", 24, "L"),
    ("Referente", 52, "L"),
    ("Qtà", 12, "R"),
    ("Importo unit.", 28, "R"),
    ("Totale", 30, "R"),
]

# Breathing room (mm) left inside a cell so text never touches the borders.
_PADDING = 2


def _tronca(pdf: FPDF, testo: str, larghezza: float) -> str:
    """Shorten ``testo`` with an ellipsis until it fits ``larghezza``.

    fpdf2 does not clip: an over-long value simply runs on over the next
    column, which is how a long referente ended up sitting on top of the
    quantity. Measuring with the current font is the only reliable way to fit
    it, since character count says nothing about rendered width.
    """
    utile = larghezza - _PADDING
    if pdf.get_string_width(testo) <= utile:
        return testo
    while testo and pdf.get_string_width(testo + "...") > utile:
        testo = testo[:-1]
    return testo + "..."


def genera_pdf_cliente(gruppo: GruppoCliente, etichetta_mese: str) -> bytes:
    """Render one customer's occurrences for the month as a PDF, bytes-ready
    for a Response body."""
    pdf = _PDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "Riepilogo da fatturare", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 11)
    pdf.cell(0, 7, etichetta_mese, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 8, gruppo.cliente.nome, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    pdf.set_font("Helvetica", "B", 9)
    pdf.set_fill_color(230, 230, 230)
    for intestazione, larghezza, allineamento in _COLONNE:
        pdf.cell(larghezza, 8, intestazione, border=1, fill=True, align=allineamento)
    pdf.ln()

    # Alphabetical by referente: the customer reading this recognises their own
    # sites by name, not by the order the occurrences happened to fall in.
    # Rows without a referente come last, then by date so a referente billed
    # twice in the month stays in chronological order.
    righe = sorted(
        gruppo.righe,
        key=lambda r: ((r.servizio.referente or "").lower() == "",
                       (r.servizio.referente or "").lower(),
                       r.occorrenza.data_occorrenza),
    )

    pdf.set_font("Helvetica", "", 9)
    for riga in righe:
        s = riga.servizio
        occ = riga.occorrenza
        valori = [
            s.descrizione,
            occ.data_occorrenza.strftime("%d/%m/%Y"),
            s.referente or "-",
            str(occ.quantita),
            f"{occ.importo:.2f} {s.valuta}",
            f"{occ.totale:.2f} {s.valuta}",
        ]
        for valore, (_, larghezza, allineamento) in zip(valori, _COLONNE):
            pdf.cell(larghezza, 7, _tronca(pdf, valore, larghezza),
                     border=1, align=allineamento)
        pdf.ln()

    pdf.set_font("Helvetica", "B", 10)
    larghezza_label = sum(l for _, l, _a in _COLONNE[:-1])
    pdf.cell(larghezza_label, 8, "Subtotale", border=1, align="R")
    pdf.cell(_COLONNE[-1][1], 8, f"{gruppo.subtotale:.2f}", border=1, align="R")

    return bytes(pdf.output())


# ---------------------------------------------------------------------------
# Services list
# ---------------------------------------------------------------------------

# Same 190mm budget as above. The per-occurrence total is left out on purpose:
# importo and quantità are both on the row, and the column that can be summed
# across contracts with different cadences is the annual value.
_COLONNE_SERVIZI = [
    ("Descrizione", 42, "L"),
    ("Referente", 40, "L"),
    ("Scadenza", 20, "L"),
    ("Cadenza", 22, "L"),
    ("Qtà", 10, "R"),
    ("Importo unit.", 26, "R"),
    ("Valore annuo", 30, "R"),
]


@dataclass
class RigaServizioPdf:
    servizio: Servizio
    cadenza: str
    prossima: date
    valore_annuo: Decimal | None   # None for a cancelled contract


@dataclass
class GruppoServiziPdf:
    cliente: Cliente
    righe: list[RigaServizioPdf] = field(default_factory=list)

    def subtotali(self) -> dict[str, Decimal]:
        """Annual value per currency: summing EUR and USD into one figure
        would be a number in no currency at all."""
        totali: dict[str, Decimal] = {}
        for r in self.righe:
            if r.valore_annuo is not None:
                valuta = r.servizio.valuta
                totali[valuta] = totali.get(valuta, Decimal("0")) + r.valore_annuo
        return totali


def _importi(totali: dict[str, Decimal]) -> str:
    if not totali:
        return "-"
    return "  ".join(f"{v:.2f} {k}" for k, v in sorted(totali.items()))


def genera_pdf_servizi(
    gruppi: list[GruppoServiziPdf], filtri: list[str], generato_il: date
) -> bytes:
    """Render the filtered services list, grouped by customer, as a PDF.

    ``filtri`` are human-readable descriptions of what was applied ("Cliente:
    Beta Memory", "Ricerca: sentinel"...): a list printed without saying how
    it was narrowed reads as the complete portfolio.
    """
    pdf = _PDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "Servizi", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 6, f"Generato il {generato_il.strftime('%d/%m/%Y')}", new_x="LMARGIN", new_y="NEXT")
    pdf.multi_cell(
        0, 6,
        "Filtri: " + ("; ".join(filtri) if filtri else "nessuno (tutti i servizi)"),
        new_x="LMARGIN", new_y="NEXT",
    )
    pdf.ln(3)

    if not gruppi:
        pdf.set_font("Helvetica", "I", 11)
        pdf.cell(0, 8, "Nessun servizio corrisponde ai filtri.", new_x="LMARGIN", new_y="NEXT")
        return bytes(pdf.output())

    larghezza_label = sum(l for _, l, _a in _COLONNE_SERVIZI[:-1])
    larghezza_valore = _COLONNE_SERVIZI[-1][1]
    totale: dict[str, Decimal] = {}

    for gruppo in gruppi:
        pdf.set_font("Helvetica", "B", 12)
        pdf.cell(0, 8, gruppo.cliente.nome, new_x="LMARGIN", new_y="NEXT")

        pdf.set_font("Helvetica", "B", 9)
        pdf.set_fill_color(230, 230, 230)
        for intestazione, larghezza, allineamento in _COLONNE_SERVIZI:
            pdf.cell(larghezza, 7, intestazione, border=1, fill=True, align=allineamento)
        pdf.ln()

        pdf.set_font("Helvetica", "", 9)
        for riga in gruppo.righe:
            s = riga.servizio
            descrizione = s.descrizione + (" (disdetto)" if s.disdetto else "")
            valori = [
                descrizione,
                s.referente or "-",
                riga.prossima.strftime("%d/%m/%Y"),
                riga.cadenza,
                str(s.quantita),
                f"{s.importo:.2f} {s.valuta}",
                f"{riga.valore_annuo:.2f} {s.valuta}" if riga.valore_annuo is not None else "-",
            ]
            for valore, (_, larghezza, allineamento) in zip(valori, _COLONNE_SERVIZI):
                pdf.cell(larghezza, 7, _tronca(pdf, valore, larghezza),
                         border=1, align=allineamento)
            pdf.ln()

        subtotali = gruppo.subtotali()
        for valuta, importo in subtotali.items():
            totale[valuta] = totale.get(valuta, Decimal("0")) + importo
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(larghezza_label, 7, f"Subtotale annuo {gruppo.cliente.nome}", border=1, align="R")
        pdf.cell(larghezza_valore, 7, _tronca(pdf, _importi(subtotali), larghezza_valore),
                 border=1, align="R")
        pdf.ln(10)

    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(larghezza_label, 9, "Totale annuo", border=1, align="R")
    pdf.cell(larghezza_valore, 9, _tronca(pdf, _importi(totale), larghezza_valore),
             border=1, align="R")
    pdf.ln(12)

    pdf.set_font("Helvetica", "I", 8)
    pdf.multi_cell(
        0, 4,
        "Valore annuo: importo unitario x quantità riportati a 12 mesi "
        "(un servizio mensile conta 12 volte, un trimestrale 4, un triennale un terzo). "
        "I contratti disdetti sono elencati ma non contribuiscono ai totali.",
    )
    return bytes(pdf.output())
