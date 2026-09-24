"""
CRUD routes for services (servizi).

A service is a recurring contract hanging off data_scadenza, billed every
cadenza_mesi months. The actual billable dates ("occorrenze") are
computed elsewhere (app/services/occorrenze.py); these routes only manage the
contract record.

All routes require an authenticated user (require_login).
Delete uses HTMX hx-delete; all other writes use standard HTML form POST.
"""
from datetime import date
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, Form, Request
from fastapi.exceptions import HTTPException
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session, contains_eager, joinedload

from app.database import get_db, utcnow
from app.dependencies import require_login
from app.models.cliente import Cliente
from app.models.enums import TipoServizio
from app.models.override_importo import OverrideImporto
from app.models.servizio import Servizio
from app.models.utente import Utente
from app.routes.dashboard import _contesto_riepilogo, _contesto_risultati
from app.services import filtri, periodi, riepilogo
from app.services.filtri import condizioni_ricerca
from app.services.pdf import GruppoServiziPdf, RigaServizioPdf, genera_pdf_servizi
from app.services.servizi import trova_servizi_simili, valore_annuo
from app.services.storico import storico_servizio
from app.services.occorrenze import (
    ETICHETTE_STATO_CONTRATTO,
    STATI_CONTRATTO,
    Occorrenza,
    aggiungi_mesi,
    fine_impegno,
    mesi_impegno,
    prossima_scadenza,
    scadenza_congelata,
    occorrenze_nel_periodo,
    stato_contratto,
)
from app.services.periodi import etichetta_cadenza
from app.templating import templates

router = APIRouter(prefix="/servizi")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_or_404(db: Session, servizio_id: int) -> Servizio:
    s = db.get(Servizio, servizio_id)
    if s is None:
        raise HTTPException(status_code=404, detail="Servizio non trovato")
    return s


def _imposta_fatturato(
    db: Session, servizio: Servizio, data_occorrenza: date, occ: Occorrenza, fatturato: bool,
) -> None:
    """Get-or-create the per-occurrence state row and set its fatturato flag.

    Billing (fatturato=True) freezes the price: it snapshots the current
    effective price/quantity (override-aware) into the row, so a later change
    to the service price does not alter what was already billed. Un-billing
    clears that snapshot ONLY if it was not a deliberate manual override.
    Shared by the single-occurrence toggle and the per-customer bulk action.

    Nothing on the contract is written: billing an unbilled cycle opening IS
    the customer's confirmation, and the engine resumes generating from the
    next cycle on its own (see occorrenze_nel_periodo). Un-billing puts the
    pending renewal straight back — no bookkeeping to unwind.
    """
    stato = db.scalars(
        select(OverrideImporto)
        .where(OverrideImporto.servizio_id == servizio.id)
        .where(OverrideImporto.data_occorrenza == data_occorrenza)
    ).first()
    if stato is None:
        stato = OverrideImporto(servizio_id=servizio.id, data_occorrenza=data_occorrenza)
        db.add(stato)

    stato.fatturato = fatturato
    if fatturato:
        stato.importo = occ.importo
        stato.quantita = occ.quantita
        stato.fatturato_il = utcnow()
    else:
        stato.fatturato_il = None
        if not stato.override_manuale:
            stato.importo = None
            stato.quantita = None


def _fattura_occorrenze_passate(db: Session, servizio: Servizio, oggi: date) -> None:
    """Mark every occurrence already in the past as billed, at creation time.

    A service is often entered into the system well after it actually
    started (importing pre-existing licenses/contracts, catching up on
    equipment installed months ago, ...). Without this, every occurrence
    between the real start date and today would surface as an overdue
    "da fatturare" alert forever, which is noise, not a real unpaid
    invoice — the system only starts "watching" a service from today
    forward. Only touches occurrences with no existing state row, so it
    can never override a deliberate fatturato/un-fatturato decision.

    Deliberately NOT called on edits (only on creation, see crea_servizio):
    re-running this on every save could silently hide a genuinely overdue,
    already-tracked invoice the user is editing for an unrelated reason.
    """
    esistenti = {
        o.data_occorrenza
        for o in db.scalars(
            select(OverrideImporto).where(OverrideImporto.servizio_id == servizio.id)
        )
    }
    # The renewal proposal (the one occurrence past the effective end date of
    # a non-auto-renewing contract) is exempt even when it is already in the
    # past: it is precisely the pending decision the user must act on, not
    # history to be silenced.
    if not servizio.rinnovo_automatico:
        # Its only occurrence is the renewal still waiting on the customer —
        # precisely the decision to act on, not history to silence.
        return
    for occ in occorrenze_nel_periodo(servizio, servizio.data_scadenza, oggi, oggi=oggi):
        if occ.data_occorrenza <= oggi and occ.data_occorrenza not in esistenti:
            _imposta_fatturato(db, servizio, occ.data_occorrenza, occ, fatturato=True)


def _destinazione(ritorno: str) -> str:
    """Where to send the user after saving (or cancelling) the form.

    The edit form can be opened from the dashboard, which wants the user back on
    the month and filters they came from; opened from anywhere else it falls
    back to the services list. The value travels in the URL, so it is only
    accepted when it is a path INSIDE this application: anything else (an
    absolute URL, a protocol-relative "//evil.example" or a backslash, which
    some browsers normalise to "/") would turn the redirect into an open
    redirect towards a site we do not control.
    """
    if ritorno.startswith("/") and not ritorno.startswith(("//", "/\\")):
        return ritorno
    return "/servizi"


def _clienti_attivi(db: Session) -> list[Cliente]:
    return db.scalars(select(Cliente).where(Cliente.attivo.is_(True)).order_by(Cliente.nome)).all()


def _form_choices(db: Session) -> dict:
    """Returns the dropdown data needed to render the service form."""
    return {
        "clienti_attivi": _clienti_attivi(db),
        "tipi_servizio": list(TipoServizio),
        "referenti_esistenti": filtri.referenti_disponibili(db),
        "descrizioni_esistenti": filtri.descrizioni_disponibili(db),
    }


def _valori_da_servizio(s: Servizio) -> dict:
    return {
        "cliente_id": str(s.cliente_id),
        "descrizione": s.descrizione,
        "tipo": s.tipo.value,
        "data_scadenza": s.data_scadenza.isoformat(),
        "data_inizio": s.data_inizio.isoformat() if s.data_inizio else "",
        "durata_impegno_mesi": str(s.durata_impegno_mesi) if s.durata_impegno_mesi is not None else "",
        "rinnovo_automatico": s.rinnovo_automatico,
        "cadenza_mesi": str(s.cadenza_mesi),
        "importo": str(s.importo),
        "quantita": str(s.quantita),
        "valuta": s.valuta,
        "preavviso_giorni": str(s.preavviso_giorni),
        "disdetto": s.disdetto,
        "referente": s.referente or "",
        "numero_seriale": s.numero_seriale or "",
        "luogo_installazione": s.luogo_installazione or "",
        "note": s.note or "",
    }


def _valori_da_form(**kwargs) -> dict:
    """Build the valori dict from raw form strings (for error repopulation)."""
    return {k: (v or "") for k, v in kwargs.items()}


def _valida(
    *,
    cliente_id_raw: str,
    descrizione: str,
    tipo_raw: str,
    data_scadenza_raw: str,
    data_inizio_raw: str,
    durata_impegno_mesi_raw: str,
    cadenza_mesi_raw: str,
    importo_raw: str,
    quantita_raw: str,
    valuta: str,
    preavviso_giorni_raw: str,
    db: Session,
) -> tuple[list[str], dict]:
    """
    Validate form fields. Returns (errori, parsed_values).
    parsed_values contains typed objects only for fields that passed validation.
    errori is empty when all fields are valid.
    """
    errori: list[str] = []
    parsed: dict = {}

    # cliente_id
    try:
        cid = int(cliente_id_raw)
        cliente = db.get(Cliente, cid)
        if cliente is None or not cliente.attivo:
            errori.append("Seleziona un cliente valido e attivo.")
        else:
            parsed["cliente_id"] = cid
    except (ValueError, TypeError):
        errori.append("Seleziona un cliente.")

    # descrizione
    desc = descrizione.strip()
    if not desc:
        errori.append("La descrizione è obbligatoria.")
    elif len(desc) > 300:
        errori.append("La descrizione non può superare i 300 caratteri.")
    else:
        parsed["descrizione"] = desc

    # tipo
    try:
        parsed["tipo"] = TipoServizio(tipo_raw)
    except ValueError:
        errori.append("Seleziona un tipo valido.")

    # data_scadenza — the one date everything is computed from.
    try:
        parsed["data_scadenza"] = date.fromisoformat(data_scadenza_raw)
    except (ValueError, TypeError):
        errori.append("Inserisci una data di scadenza valida.")

    # data_inizio — optional and purely informational (see Servizio docstring).
    inizio_raw = data_inizio_raw.strip()
    if not inizio_raw:
        parsed["data_inizio"] = None
    else:
        try:
            parsed["data_inizio"] = date.fromisoformat(inizio_raw)
        except (ValueError, TypeError):
            errori.append("Data di inizio non valida.")

    # durata_impegno_mesi — optional: empty means the commitment is a single
    # billing, so every occurrence is a renewal (see mesi_impegno).
    impegno_raw = durata_impegno_mesi_raw.strip()
    if not impegno_raw:
        parsed["durata_impegno_mesi"] = None
    else:
        try:
            impegno = int(impegno_raw)
            if impegno < 1:
                errori.append("La durata dell'impegno deve essere di almeno 1 mese.")
            else:
                parsed["durata_impegno_mesi"] = impegno
        except (ValueError, TypeError):
            errori.append("Durata impegno non valida: inserisci un numero intero di mesi (es. 12).")

    # cadenza_mesi
    try:
        cadenza = int(cadenza_mesi_raw)
        if cadenza < 1:
            errori.append("La cadenza deve essere di almeno 1 mese.")
        else:
            parsed["cadenza_mesi"] = cadenza
    except (ValueError, TypeError):
        errori.append("Cadenza non valida: inserisci un numero intero di mesi (es. 12).")

    # importo
    try:
        importo = Decimal(importo_raw.replace(",", "."))
        if importo < 0:
            errori.append("L'importo non può essere negativo.")
        else:
            parsed["importo"] = importo
    except (InvalidOperation, AttributeError):
        errori.append("Importo non valido: usa un numero decimale (es. 99.90).")

    # quantita
    try:
        q = int(quantita_raw)
        if q < 1:
            errori.append("La quantità deve essere almeno 1.")
        else:
            parsed["quantita"] = q
    except (ValueError, TypeError):
        errori.append("Quantità non valida: inserisci un numero intero.")

    # valuta
    v = valuta.strip().upper()
    if not v:
        errori.append("La valuta è obbligatoria.")
    else:
        parsed["valuta"] = v

    # preavviso_giorni
    try:
        pg = int(preavviso_giorni_raw)
        if pg < 0:
            errori.append("Il preavviso non può essere negativo.")
        else:
            parsed["preavviso_giorni"] = pg
    except (ValueError, TypeError):
        errori.append("Preavviso non valido: inserisci un numero intero.")

    return errori, parsed


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

def _normalizza_filtri(
    cliente: str | None, referente: str | None, stato: str | None, q: str | None
) -> tuple[int | None, str | None, str | None, str]:
    """Turn the raw query params into typed, validated filter values.

    Anything unrecognised is dropped rather than rejected: these arrive from
    links and bookmarks, and a stale value should mean "no filter", not an
    error page.
    """
    cliente_id = int(cliente) if (cliente and cliente.isdigit()) else None
    referente_val = referente.strip() if referente and referente.strip() else None
    stato_val = stato if stato in STATI_CONTRATTO else None
    return cliente_id, referente_val, stato_val, (q or "").strip()


def _righe_filtrate(
    db: Session,
    cliente_id: int | None,
    referente_val: str | None,
    stato_val: str | None,
    ricerca: str,
) -> list[tuple]:
    """The services matching the filters, as the rows the list shows.

    Shared by the page and by the PDF export, so the export is always exactly
    what the page lists — two copies of this query would drift apart the first
    time a filter is added to one and not the other.

    cliente/referente/search are SQL conditions on service columns; the
    contract state is NOT a column (only disdetto is — see stato_contratto),
    so it is filtered in Python after computing it per row.
    """
    query = (
        select(Servizio)
        .join(Servizio.cliente)
        .options(contains_eager(Servizio.cliente))
        .order_by(Servizio.data_inizio)
    )
    if cliente_id is not None:
        query = query.where(Servizio.cliente_id == cliente_id)
    if referente_val:
        query = query.where(Servizio.referente == referente_val)
    for condizione in condizioni_ricerca(ricerca):
        query = query.where(condizione)

    oggi = date.today()
    # Each row carries a human-readable cadence label (mensile/trimestrale/...),
    # the date the contract next comes due (NOT the stored data_scadenza, which
    # is a fixed anchor and drifts into the past on an auto-renewing contract)
    # and the computed contract state.
    righe = [
        (s, etichetta_cadenza(s.cadenza_mesi), prossima_scadenza(s, oggi),
         stato_contratto(s, oggi=oggi))
        for s in db.scalars(query).all()
    ]
    if stato_val is not None:
        righe = [r for r in righe if r[3] == stato_val]
    return righe


@router.get("")
def lista_servizi(
    request: Request,
    cliente: str | None = None,
    referente: str | None = None,
    stato: str | None = None,
    q: str | None = None,
    db: Session = Depends(get_db),
    user: Utente = Depends(require_login),
):
    cliente_id, referente_val, stato_val, ricerca = _normalizza_filtri(cliente, referente, stato, q)
    righe = _righe_filtrate(db, cliente_id, referente_val, stato_val, ricerca)

    contesto = {
        "user": user,
        "righe": righe,
        # Current filter values, to pre-populate the form (e.g. on a bookmarked URL).
        "filtri": {
            "cliente": cliente_id,
            "referente": referente_val or "",
            "stato": stato_val or "",
            "q": ricerca,
        },
        "clienti": filtri.clienti_disponibili(db),
        "referenti": filtri.referenti_disponibili(db),
        "stati": STATI_CONTRATTO,
        "etichette_stato": ETICHETTE_STATO_CONTRATTO,
    }

    # HTMX request (filter change): swap only the results region. A normal page
    # load or a bookmarked URL gets the whole page, with filters already applied
    # and the form pre-populated from the querystring.
    template = (
        "servizi/_risultati.html"
        if request.headers.get("HX-Request")
        else "servizi/lista.html"
    )
    return templates.TemplateResponse(request, template, contesto)


@router.get("/export/pdf")
def export_pdf(
    cliente: str | None = None,
    referente: str | None = None,
    stato: str | None = None,
    q: str | None = None,
    nascondi: str | None = None,
    db: Session = Depends(get_db),
    user: Utente = Depends(require_login),
):
    """The services list as it is on screen, as a PDF grouped by customer.

    Same filters as the page, through the same _righe_filtrate. ``nascondi``
    carries the "nascondi scaduti e disdetti" display preference: it lives in
    the browser (localStorage), so the server only knows about it because
    app.js adds it to the link — without it the PDF would contain rows the
    user was not looking at.
    """
    cliente_id, referente_val, stato_val, ricerca = _normalizza_filtri(cliente, referente, stato, q)
    righe = _righe_filtrate(db, cliente_id, referente_val, stato_val, ricerca)
    if nascondi == "1":
        righe = [r for r in righe if r[3] not in ("scaduto", "disdetto")]

    # Grouped by customer like the riepilogo, customers alphabetical; inside a
    # group by description then referente, so identical contracts (the
    # fifteen "Sentinel One") sit together and read in the referente's order.
    gruppi: dict[int, GruppoServiziPdf] = {}
    for s, cadenza, prossima, _stato in righe:
        gruppo = gruppi.setdefault(s.cliente_id, GruppoServiziPdf(cliente=s.cliente))
        gruppo.righe.append(RigaServizioPdf(s, cadenza, prossima, valore_annuo(s)))
    ordinati = sorted(gruppi.values(), key=lambda g: g.cliente.nome.casefold())
    for g in ordinati:
        g.righe.sort(key=lambda r: (r.servizio.descrizione.casefold(),
                                    (r.servizio.referente or "").casefold()))

    # What was applied, spelled out on the document.
    filtri_letti = []
    if cliente_id is not None:
        c = db.get(Cliente, cliente_id)
        filtri_letti.append(f"Cliente: {c.nome if c else cliente_id}")
    if referente_val:
        filtri_letti.append(f"Referente: {referente_val}")
    if stato_val:
        filtri_letti.append(f"Stato: {ETICHETTE_STATO_CONTRATTO[stato_val]}")
    if ricerca:
        filtri_letti.append(f"Ricerca: {ricerca}")
    if nascondi == "1":
        filtri_letti.append("esclusi scaduti e disdetti")

    oggi = date.today()
    contenuto = genera_pdf_servizi(ordinati, filtri_letti, oggi)
    return Response(
        content=contenuto,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="servizi_{oggi.isoformat()}.pdf"'},
    )


@router.get("/nuovo")
def nuovo_form(
    request: Request,
    db: Session = Depends(get_db),
    user: Utente = Depends(require_login),
):
    return templates.TemplateResponse(
        request,
        "servizi/form.html",
        {
            "user": user,
            "titolo": "Nuovo servizio",
            "action": "/servizi",
            "valori": {
                "cliente_id": "",
                "descrizione": "",
                "tipo": TipoServizio.abbonamento.value,
                "data_scadenza": "",
                "data_inizio": "",
                "durata_impegno_mesi": "",
                "rinnovo_automatico": False,
                "cadenza_mesi": "12",
                "importo": "",
                "quantita": "1",
                "valuta": "EUR",
                "preavviso_giorni": "30",
                "disdetto": False,
                "referente": "",
                "note": "",
            },
            "errori": [],
            **_form_choices(db),
        },
    )


@router.post("")
def crea_servizio(
    request: Request,
    cliente_id: str = Form(""),
    descrizione: str = Form(""),
    tipo: str = Form(""),
    data_inizio: str = Form(""),
    data_scadenza: str = Form(""),
    durata_impegno_mesi: str = Form(""),
    rinnovo_automatico: str | None = Form(None),  # checkbox: present when checked
    cadenza_mesi: str = Form(""),
    importo: str = Form(""),
    quantita: str = Form("1"),
    valuta: str = Form("EUR"),
    preavviso_giorni: str = Form("30"),
    disdetto: str | None = Form(None),  # checkbox: present when checked
    referente: str = Form(""),
    numero_seriale: str = Form(""),
    luogo_installazione: str = Form(""),
    note: str = Form(""),
    conferma_simili: str | None = Form(None),  # set when the user confirmed the warning
    db: Session = Depends(get_db),
    user: Utente = Depends(require_login),
):
    is_rinnovo = rinnovo_automatico is not None
    is_disdetto = disdetto is not None
    valori = _valori_da_form(
        cliente_id=cliente_id, descrizione=descrizione, tipo=tipo,
        data_scadenza=data_scadenza, data_inizio=data_inizio,
        durata_impegno_mesi=durata_impegno_mesi, cadenza_mesi=cadenza_mesi,
        importo=importo, quantita=quantita, valuta=valuta,
        preavviso_giorni=preavviso_giorni,
        referente=referente, numero_seriale=numero_seriale,
        luogo_installazione=luogo_installazione, note=note,
    )
    valori["rinnovo_automatico"] = is_rinnovo
    valori["disdetto"] = is_disdetto
    errori, parsed = _valida(
        cliente_id_raw=cliente_id, descrizione=descrizione, tipo_raw=tipo,
        data_scadenza_raw=data_scadenza, data_inizio_raw=data_inizio,
        durata_impegno_mesi_raw=durata_impegno_mesi, cadenza_mesi_raw=cadenza_mesi,
        importo_raw=importo, quantita_raw=quantita, valuta=valuta,
        preavviso_giorni_raw=preavviso_giorni, db=db,
    )
    # Look-alike services are a warning, never an error: one customer here has
    # fifteen identical "Sentinel One" contracts, one per end customer. It is
    # only asked once — confirming means "yes, another one" (see
    # services/servizi.py).
    simili = (
        []
        if errori or conferma_simili is not None
        else trova_servizi_simili(db, parsed["cliente_id"], descrizione, referente)
    )
    if errori or simili:
        return templates.TemplateResponse(
            request, "servizi/form.html",
            {"user": user, "titolo": "Nuovo servizio", "action": "/servizi",
             "valori": valori, "errori": errori, "simili": simili,
             **_form_choices(db)},
            status_code=422,
        )
    nuovo = Servizio(
        referente=referente.strip() or None,
        numero_seriale=numero_seriale.strip() or None,
        luogo_installazione=luogo_installazione.strip() or None,
        note=note.strip() or None,
        rinnovo_automatico=is_rinnovo,
        disdetto=is_disdetto,
        **parsed,
    )
    db.add(nuovo)
    db.flush()  # assigns nuovo.id, needed by _fattura_occorrenze_passate
    _fattura_occorrenze_passate(db, nuovo, date.today())
    db.commit()
    return RedirectResponse(url="/servizi", status_code=303)


@router.get("/{servizio_id}/storico")
def storico(
    request: Request,
    servizio_id: int,
    db: Session = Depends(get_db),
    user: Utente = Depends(require_login),
):
    """One service's whole timeline (see services/storico.py).

    Read-only: marking an occurrence billed stays where the month's work
    happens — the dashboard and the riepilogo — so this page cannot change
    what it is there to report.
    """
    s = _get_or_404(db, servizio_id)
    oggi = date.today()
    return templates.TemplateResponse(
        request,
        "servizi/storico.html",
        {
            "user": user,
            "servizio": s,
            "voci": storico_servizio(db, s, oggi=oggi),
            "cadenza": etichetta_cadenza(s.cadenza_mesi),
            "stato_s": stato_contratto(s, oggi=oggi),
            "etichette_stato": ETICHETTE_STATO_CONTRATTO,
            "prossima": prossima_scadenza(s, oggi),
        },
    )


@router.get("/{servizio_id}/modifica")
def modifica_form(
    request: Request,
    servizio_id: int,
    ritorno: str = "",
    db: Session = Depends(get_db),
    user: Utente = Depends(require_login),
):
    s = _get_or_404(db, servizio_id)
    # If the linked customer is inactive, still include it so the saved value
    # is visible, and mark it as such in the dropdown.
    choices = _form_choices(db)
    if s.cliente not in choices["clienti_attivi"]:
        choices["clienti_attivi"] = [s.cliente] + list(choices["clienti_attivi"])
    return templates.TemplateResponse(
        request,
        "servizi/form.html",
        {
            "user": user,
            "titolo": f"Modifica — {s.descrizione}",
            "action": f"/servizi/{servizio_id}/modifica",
            "servizio": s,
            "valori": _valori_da_servizio(s),
            "etichetta_stato_attuale": ETICHETTE_STATO_CONTRATTO[stato_contratto(s)],
            # The editable field holds the anchor, which on a contract billed in
            # instalments is NOT the expiry the operator has in mind. Show the
            # computed expiry alongside it so opening a contract always answers
            # "when does this expire?".
            "prossima_scadenza": prossima_scadenza(s, date.today()),
            "errori": [],
            "ritorno": _destinazione(ritorno),
            **choices,
        },
    )


@router.post("/{servizio_id}/modifica")
def aggiorna_servizio(
    request: Request,
    servizio_id: int,
    cliente_id: str = Form(""),
    descrizione: str = Form(""),
    tipo: str = Form(""),
    data_inizio: str = Form(""),
    data_scadenza: str = Form(""),
    durata_impegno_mesi: str = Form(""),
    rinnovo_automatico: str | None = Form(None),  # checkbox: present when checked
    cadenza_mesi: str = Form(""),
    importo: str = Form(""),
    quantita: str = Form("1"),
    valuta: str = Form("EUR"),
    preavviso_giorni: str = Form("30"),
    disdetto: str | None = Form(None),  # checkbox: present when checked
    referente: str = Form(""),
    numero_seriale: str = Form(""),
    luogo_installazione: str = Form(""),
    note: str = Form(""),
    ritorno: str = Form(""),  # page to go back to (see _destinazione)
    conferma_simili: str | None = Form(None),
    db: Session = Depends(get_db),
    user: Utente = Depends(require_login),
):
    s = _get_or_404(db, servizio_id)
    destinazione = _destinazione(ritorno)
    is_rinnovo = rinnovo_automatico is not None
    is_disdetto = disdetto is not None

    # Turning auto-renewal off must pin the cycle where the automatic renewals
    # have actually carried it, not snap it back to the stored one — computed
    # from the service as it stood BEFORE today's edits. Overrides whatever was
    # typed in "Data di scadenza" below.
    disattiva_rinnovo = s.rinnovo_automatico and not is_rinnovo
    scadenza_pinnata = scadenza_congelata(s, date.today()) if disattiva_rinnovo else None

    valori = _valori_da_form(
        cliente_id=cliente_id, descrizione=descrizione, tipo=tipo,
        data_scadenza=data_scadenza, data_inizio=data_inizio,
        durata_impegno_mesi=durata_impegno_mesi, cadenza_mesi=cadenza_mesi,
        importo=importo, quantita=quantita, valuta=valuta,
        preavviso_giorni=preavviso_giorni,
        referente=referente, numero_seriale=numero_seriale,
        luogo_installazione=luogo_installazione, note=note,
    )
    valori["rinnovo_automatico"] = is_rinnovo
    valori["disdetto"] = is_disdetto
    errori, parsed = _valida(
        cliente_id_raw=cliente_id, descrizione=descrizione, tipo_raw=tipo,
        data_scadenza_raw=data_scadenza, data_inizio_raw=data_inizio,
        durata_impegno_mesi_raw=durata_impegno_mesi, cadenza_mesi_raw=cadenza_mesi,
        importo_raw=importo, quantita_raw=quantita, valuta=valuta,
        preavviso_giorni_raw=preavviso_giorni, db=db,
    )
    simili = (
        []
        if errori or conferma_simili is not None
        else trova_servizi_simili(
            db, parsed["cliente_id"], descrizione, referente, escludi_id=servizio_id
        )
    )
    if errori or simili:
        choices = _form_choices(db)
        if s.cliente not in choices["clienti_attivi"]:
            choices["clienti_attivi"] = [s.cliente] + list(choices["clienti_attivi"])
        return templates.TemplateResponse(
            request, "servizi/form.html",
            {"user": user, "titolo": f"Modifica — {s.descrizione}",
             "action": f"/servizi/{servizio_id}/modifica",
             "servizio": s, "valori": valori, "errori": errori, "simili": simili,
             "etichetta_stato_attuale": ETICHETTE_STATO_CONTRATTO[stato_contratto(s)],
             "prossima_scadenza": prossima_scadenza(s, date.today()),
             "ritorno": destinazione,
             **choices},
            status_code=422,
        )
    if scadenza_pinnata is not None:
        parsed["data_scadenza"] = scadenza_pinnata
    for field, value in parsed.items():
        setattr(s, field, value)
    s.referente = referente.strip() or None
    s.numero_seriale = numero_seriale.strip() or None
    s.luogo_installazione = luogo_installazione.strip() or None
    s.note = note.strip() or None
    s.rinnovo_automatico = is_rinnovo
    s.disdetto = is_disdetto
    db.commit()
    return RedirectResponse(url=destinazione, status_code=303)


@router.delete("/{servizio_id}")
def elimina_servizio(
    servizio_id: int,
    db: Session = Depends(get_db),
    user: Utente = Depends(require_login),
):
    s = _get_or_404(db, servizio_id)
    db.delete(s)
    db.commit()
    return Response(status_code=200)


@router.post("/{servizio_id}/occorrenze/{data_occorrenza}/fatturato")
def toggle_fatturato(
    request: Request,
    servizio_id: int,
    data_occorrenza: date,
    mese: str | None = Form(None),
    cliente: str | None = Form(None),
    referente: str | None = Form(None),
    stato_filtro: str | None = Form(None, alias="stato"),
    vista: str = Form("dashboard"),
    db: Session = Depends(get_db),
    user: Utente = Depends(require_login),
):
    """
    Toggle the "fatturato" flag for one occurrence (HTMX).

    Occurrences are not stored, so the flag lives on a per-occurrence state row
    (OverrideImporto); see _imposta_fatturato for the snapshot/undo rules.
    We accept the date only if it is a real occurrence of the service, to
    avoid creating state rows on bogus dates.

    Used from two places, told apart by ``vista``:
    - dashboard (servizi/_toggle_fatturato.html): sends the active month and
      filters, and we re-render the whole results region because billing can
      change the summary counts and drop the row out of an active stato
      filter.
    - riepilogo (riepilogo/_toggle_fatturato.html): billing there always
      means "remove from the list" (see _contesto_riepilogo), so the results
      region is re-rendered there too, for the same reason.
    """
    s = _get_or_404(db, servizio_id)

    # Guard: the date must be an actual occurrence of this service. Reuse the
    # computed occurrence as the source of the effective price/quantity.
    occorrenze = occorrenze_nel_periodo(s, data_occorrenza, data_occorrenza)
    if not occorrenze:
        raise HTTPException(status_code=404, detail="Occorrenza non trovata")
    occ = occorrenze[0]

    _imposta_fatturato(db, s, data_occorrenza, occ, fatturato=not occ.fatturato)
    db.commit()

    if vista == "riepilogo":
        contesto = _contesto_riepilogo(db, mese)
        return templates.TemplateResponse(request, "riepilogo/_risultati.html", contesto)

    contesto = _contesto_risultati(db, mese, cliente, referente, stato_filtro)
    return templates.TemplateResponse(request, "dashboard/_risultati.html", contesto)


@router.post("/fatturato-cliente")
def fatturato_cliente(
    request: Request,
    cliente_id: int = Form(...),
    mese: str | None = Form(None),
    db: Session = Depends(get_db),
    user: Utente = Depends(require_login),
):
    """
    Mark every not-yet-billed occurrence of one customer, for the selected
    month, as fatturato in one go (bulk version of toggle_fatturato) — the
    "Segna tutto fatturato" button next to a customer group on the riepilogo
    page, for when the whole month is invoiced together.
    """
    righe = riepilogo.occorrenze_del_mese(
        db, periodi.parse_mese(mese), escludi_disdetti=True, cliente_id=cliente_id
    )
    for riga in righe:
        if riga.occorrenza.stato_visivo == "fatturato":
            continue
        _imposta_fatturato(
            db, riga.servizio, riga.occorrenza.data_occorrenza, riga.occorrenza, fatturato=True
        )
    db.commit()

    contesto = _contesto_riepilogo(db, mese)
    return templates.TemplateResponse(request, "riepilogo/_risultati.html", contesto)
