"""
PDF generation for the billing summary — one document per customer, meant to
be handed to that customer (or their referente) as a billing recap for the
month.

Built with fpdf2 (pure Python, no OS-level dependencies), consistent with the
project's "no build step" philosophy. Core PDF fonts (Helvetica) only support
Latin-1/CP1252, so amounts are printed with the ISO currency code ("10.00
EUR"), not a currency symbol — matching the CSV export.
"""
from __future__ import annotations

from fpdf import FPDF

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
