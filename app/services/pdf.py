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

# Column widths (mm) for the occurrence table, summing to the usable page
# width (A4 minus the default 10mm margins on each side = 190mm).
_COLONNE = [
    ("Descrizione", 60),
    ("Scadenza", 25),
    ("Referente", 35),
    ("Qtà", 15),
    ("Importo unit.", 25),
    ("Totale", 30),
]


def genera_pdf_cliente(gruppo: GruppoCliente, etichetta_mese: str) -> bytes:
    """Render one customer's occurrences for the month as a PDF, bytes-ready
    for a Response body."""
    pdf = FPDF(orientation="P", unit="mm", format="A4")
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
    for intestazione, larghezza in _COLONNE:
        pdf.cell(larghezza, 8, intestazione, border=1, fill=True)
    pdf.ln()

    pdf.set_font("Helvetica", "", 9)
    for riga in gruppo.righe:
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
        for valore, (_, larghezza) in zip(valori, _COLONNE):
            pdf.cell(larghezza, 7, valore, border=1)
        pdf.ln()

    pdf.set_font("Helvetica", "B", 10)
    larghezza_label = sum(l for _, l in _COLONNE[:-1])
    pdf.cell(larghezza_label, 8, "Subtotale", border=1)
    pdf.cell(_COLONNE[-1][1], 8, f"{gruppo.subtotale:.2f}", border=1)

    return bytes(pdf.output())
