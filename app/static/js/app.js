/*
 * Interface behaviour shared by every page: date pickers, filter dialogs,
 * light/dark theme, and the data tables (click-to-sort, column visibility,
 * pagination, "hide expired").
 *
 * It lived inline in base.html until the file reached 500 lines, almost all
 * of it JavaScript. As a static file the browser caches it across pages and
 * releases (the URL carries the app version, so a new release fetches it
 * again), and the template goes back to being markup.
 *
 * No build step, no modules: it is loaded as a plain script, exactly as it
 * was inline. Everything that reacts to HTMX swaps stays delegated on
 * document, so newly swapped-in markup keeps working with no re-init.
 */

// Init flatpickr on every .date-picker input, in the current page and
// in anything HTMX swaps in later (e.g. a form loaded into a fragment).
function initDatePickers(root) {
    root.querySelectorAll(".date-picker").forEach(function (el) {
        flatpickr(el, { dateFormat: "Y-m-d", altInput: true, altFormat: "d/m/Y", allowInput: true });
    });
}
document.addEventListener("DOMContentLoaded", function () { initDatePickers(document); });
document.addEventListener("htmx:afterSwap", function (e) { initDatePickers(e.target); });

// Light/dark theme toggle. The initial data-theme attribute is set
// synchronously in <head> (see above) to avoid a flash on load. The
// sun/moon icon swap is pure CSS (see .icon-sun/.icon-moon); this only
// keeps the title in sync and handles the click.
function syncThemeToggleTitle() {
    var btn = document.getElementById("theme-toggle");
    var isDark = document.documentElement.getAttribute("data-theme") === "dark";
    btn.title = isDark ? "Passa al tema chiaro" : "Passa al tema scuro";
}
document.addEventListener("DOMContentLoaded", function () {
    syncThemeToggleTitle();
    document.getElementById("theme-toggle").addEventListener("click", function () {
        var next = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
        document.documentElement.setAttribute("data-theme", next);
        localStorage.setItem("theme", next);
        syncThemeToggleTitle();
    });
});

// Click-to-sort table columns (th.sortable): alphabetical, toggles
// ascending/descending on repeated clicks. Delegated on document (not
// bound per-th) so it keeps working after HTMX swaps new table rows
// in, with no re-init step needed. Sorts only the clicked table's own
// tbody rows, so multiple tables on one page (e.g. one per customer
// in the riepilogo) sort independently.
var ORDINAMENTO_KEY_PREFIX = "scadenziario:ordinamento:";

// Table preferences (hidden columns, sort column) are saved by column NAME,
// the label shown in the header. Until 0.9.1 they were saved by POSITION,
// and adding a column anywhere but the end shifted every choice after it: a
// user who had hidden "Cadenza" would find the neighbouring column hidden
// instead. Names survive new columns; renaming a header only makes that one
// column visible again, which is a harmless way to fail.
function nomeColonna(th, i) {
    // The label is normally the th's own text nodes (skipping the gear
    // button and any other markup), but a header that wraps its label in
    // .th-label (to control where the sort arrow lands) has no bare text
    // node — read that span instead.
    var etichetta = th.querySelector(".th-label");
    var testo = etichetta
        ? etichetta.textContent.trim()
        : th.childNodes.length
            ? Array.prototype.map.call(th.childNodes, function (n) {
                  return n.nodeType === Node.TEXT_NODE ? n.textContent : "";
              }).join("").trim()
            : th.textContent.trim();
    return testo || "Colonna " + (i + 1);
}

function nomiColonne(tabella) {
    return Array.prototype.map.call(tabella.tHead.rows[0].children, nomeColonna);
}

// Translates a position saved by 0.9.1 or earlier into a column name. Those
// positions were counted on the table as it was then, so columns added since
// (marked data-col-nuova in the template) are left out of the count. Once
// every browser has loaded the page after the update the old data is gone
// and the marker can be dropped.
function nomeDaPosizioneVecchia(tabella, indice) {
    var vecchie = Array.prototype.filter.call(tabella.tHead.rows[0].children, function (th) {
        return !th.hasAttribute("data-col-nuova");
    });
    var th = vecchie[indice];
    return th ? nomeColonna(th, indice) : null;
}

function leggiOrdinamento(tabella) {
    var tableId = tabella.getAttribute("data-table-id");
    try {
        var raw = localStorage.getItem(ORDINAMENTO_KEY_PREFIX + tableId);
        if (!raw) return null;
        var salvato = JSON.parse(raw);
        if (typeof salvato.indice === "number") {        // saved by 0.9.1 or earlier
            var nome = nomeDaPosizioneVecchia(tabella, salvato.indice);
            if (!nome) return null;
            salvato = { colonna: nome, crescente: salvato.crescente };
            localStorage.setItem(ORDINAMENTO_KEY_PREFIX + tableId, JSON.stringify(salvato));
        }
        return salvato;
    } catch (e) {
        return null;
    }
}

function salvaOrdinamento(tabella, indice, crescente) {
    var nome = nomiColonne(tabella)[indice];
    localStorage.setItem(ORDINAMENTO_KEY_PREFIX + tabella.getAttribute("data-table-id"),
                         JSON.stringify({ colonna: nome, crescente: crescente }));
}

// A cell can carry data-sort (e.g. an ISO date) to sort by a value
// different from what's displayed — plain text sorting on
// "24/08/2026 – 23/08/2027" would order by day-of-month first, not
// chronologically.
function chiaveOrdinamento(cella) {
    var attr = cella.getAttribute("data-sort");
    if (attr !== null) return attr.trim().toLowerCase();
    return (cella.textContent || "").trim().toLowerCase();
}

// Empty-ish cells ("—" is how a missing referente/sede is drawn) always
// sort last, in both directions: they are absent values, not values
// that happen to come first or last in the alphabet.
function eVuota(chiave) {
    return chiave === "" || chiave === "—" || chiave === "-";
}

// A column of amounts sorted as text puts "100.00" before "20.00".
// Returns the number when the whole column is numeric (empties aside),
// null when it is not, so text columns keep sorting alphabetically.
function numero(chiave) {
    var n = Number(chiave.replace(/\s/g, "").replace(",", "."));
    return isFinite(n) && chiave !== "" ? n : null;
}

function colonnaNumerica(chiavi) {
    var almenoUna = false;
    for (var i = 0; i < chiavi.length; i++) {
        if (eVuota(chiavi[i])) continue;
        if (numero(chiavi[i]) === null) return false;
        almenoUna = true;
    }
    return almenoUna;
}

// Sorts the clicked table's own tbody rows by the given header/column,
// used both for the click-to-sort handler and to restore a previously
// saved order (see leggiOrdinamento) after a reload or an HTMX swap.
function ordinaTabella(tabella, th, crescente) {
    var corpo = tabella.tBodies[0];
    if (!corpo) return;
    var indice = Array.prototype.indexOf.call(th.parentNode.children, th);

    tabella.querySelectorAll("th.sortable").forEach(function (altro) {
        if (altro !== th) altro.removeAttribute("data-dir");
    });
    th.setAttribute("data-dir", crescente ? "asc" : "desc");

    // Row and key are paired up front: reading the key back out of a
    // parallel array while sort() is reordering would read the wrong one.
    var voci = Array.prototype.slice.call(corpo.querySelectorAll("tr")).map(
        function (riga) {
            var cella = riga.children[indice];
            return { riga: riga, chiave: cella ? chiaveOrdinamento(cella) : "" };
        }
    );
    var numerica = colonnaNumerica(voci.map(function (v) { return v.chiave; }));
    voci.sort(function (a, b) {
        if (eVuota(a.chiave) || eVuota(b.chiave)) {
            if (eVuota(a.chiave) && eVuota(b.chiave)) return 0;
            return eVuota(a.chiave) ? 1 : -1;   // missing values last, always
        }
        if (numerica) {
            return crescente
                ? numero(a.chiave) - numero(b.chiave)
                : numero(b.chiave) - numero(a.chiave);
        }
        if (a.chiave < b.chiave) return crescente ? -1 : 1;
        if (a.chiave > b.chiave) return crescente ? 1 : -1;
        return 0;
    });
    voci.forEach(function (v) { corpo.appendChild(v.riga); });
}

// Click-to-sort table columns (th.sortable): alphabetical, toggles
// ascending/descending on repeated clicks. Delegated on document (not
// bound per-th) so it keeps working after HTMX swaps new table rows
// in, with no re-init step needed. Sorts only the clicked table's own
// tbody rows, so multiple tables on one page (e.g. one per customer
// in the riepilogo) sort independently.
document.addEventListener("click", function (e) {
    if (e.target.closest(".col-toggle-btn")) return;
    var th = e.target.closest("th.sortable");
    if (!th) return;
    var tabella = th.closest("table");
    var indice = Array.prototype.indexOf.call(th.parentNode.children, th);
    var crescente = th.getAttribute("data-dir") !== "asc";

    ordinaTabella(tabella, th, crescente);

    if (tabella.hasAttribute("data-table-id")) {
        salvaOrdinamento(tabella, indice, crescente);
        // Re-sync pagination: row order changed, so which rows fall on
        // which page changed too. Back to page 1 on a fresh sort.
        tabella._paginaCorrente = 1;
        applicaPaginazione(tabella);
    }
});

// Restores a previously saved sort order for a data table (see
// salvaOrdinamento), so it survives reloads and navigating away and
// back (e.g. opening "Modifica" on a row and returning).
function applicaOrdinamento(tabella) {
    var tableId = tabella.getAttribute("data-table-id");
    if (!tableId || !tabella.tHead) return;
    var ordinamento = leggiOrdinamento(tabella);
    if (!ordinamento) return;
    var th = tabella.tHead.rows[0].children[nomiColonne(tabella).indexOf(ordinamento.colonna)];
    if (!th || !th.classList.contains("sortable")) return;
    ordinaTabella(tabella, th, ordinamento.crescente);
}

// ---------------------------------------------------------------
// Column visibility + pagination for data tables (servizi, dashboard,
// riepilogo). Both are pure client-side (the tables are small) and
// persisted in localStorage so the choice survives reloads/future
// visits. A table opts in via data-table-id="servizi"/"dashboard"/
// "riepilogo" (see the relevant _risultati.html); riepilogo has one
// <table> per customer, all sharing the same id, so a column choice
// or page-size change applies to all of them at once.

var COLONNE_KEY_PREFIX = "scadenziario:colonneNascoste:";
var PAGE_SIZE_KEY = "scadenziario:righePerPagina";
var PAGE_SIZES = [10, 50, 100];

// Positions (0-based) of the hidden columns of this table. Stored as names
// (see nomeColonna), handed out as positions because that is what the code
// that shows and hides cells works with.
function colonneNascoste(tabella) {
    var tableId = tabella.getAttribute("data-table-id");
    try {
        var raw = localStorage.getItem(COLONNE_KEY_PREFIX + tableId);
        var salvate = raw ? JSON.parse(raw) : [];
        if (salvate.some(function (v) { return typeof v === "number"; })) {
            // Saved by 0.9.1 or earlier: convert once, keeping the user's choice.
            salvate = salvate
                .map(function (i) { return nomeDaPosizioneVecchia(tabella, i); })
                .filter(function (n) { return n; });
            localStorage.setItem(COLONNE_KEY_PREFIX + tableId, JSON.stringify(salvate));
        }
        var nomi = nomiColonne(tabella);
        return salvate
            .map(function (n) { return nomi.indexOf(n); })
            .filter(function (i) { return i !== -1; });
    } catch (e) {
        return [];
    }
}

function salvaColonneNascoste(tabella, indici) {
    var nomi = nomiColonne(tabella);
    localStorage.setItem(COLONNE_KEY_PREFIX + tabella.getAttribute("data-table-id"),
                         JSON.stringify(indici.map(function (i) { return nomi[i]; })));
}

function righePerPaginaCorrente() {
    var v = parseInt(localStorage.getItem(PAGE_SIZE_KEY), 10);
    return PAGE_SIZES.indexOf(v) !== -1 ? v : 50;
}

function applicaColonne(tabella) {
    var tableId = tabella.getAttribute("data-table-id");
    if (!tableId || !tabella.tHead) return;
    var nascoste = colonneNascoste(tabella);
    var righeIntestazione = Array.prototype.slice.call(tabella.tHead.rows);
    righeIntestazione.forEach(function (tr) {
        Array.prototype.forEach.call(tr.children, function (th, i) {
            th.classList.toggle("col-hidden", nascoste.indexOf(i) !== -1);
        });
    });
    if (tabella.tBodies[0]) {
        Array.prototype.forEach.call(tabella.tBodies[0].rows, function (tr) {
            Array.prototype.forEach.call(tr.children, function (td, i) {
                td.classList.toggle("col-hidden", nascoste.indexOf(i) !== -1);
            });
        });
    }
}

function apriDialogoColonne(tabella) {
    var tableId = tabella.getAttribute("data-table-id");
    var headerRow = tabella.tHead.rows[0];

    // Laid out as a settings sheet: title with "Fine" on the right, then a
    // grouped list with one switch per column, then a footnote. The
    // padding sits on the inner box, so a click on the <dialog> itself is
    // a click on the backdrop and closes it.
    var dialog = document.createElement("dialog");
    dialog.className = "colonne-dialog";
    dialog.setAttribute("aria-labelledby", "colonne-titolo");
    var corpo = document.createElement("div");
    corpo.className = "colonne-corpo";
    dialog.appendChild(corpo);

    var testa = document.createElement("div");
    testa.className = "colonne-testa";
    var titolo = document.createElement("h3");
    titolo.id = "colonne-titolo";
    titolo.textContent = "Colonne visibili";
    var fine = document.createElement("button");
    fine.type = "button";
    fine.className = "btn-testo";
    fine.textContent = "Fine";
    fine.addEventListener("click", function () { dialog.close(); });
    testa.appendChild(titolo);
    testa.appendChild(fine);
    corpo.appendChild(testa);

    var gruppo = document.createElement("div");
    gruppo.className = "scheda-gruppo";
    var interruttori = [];

    function aggiorna() {
        var nascoste = interruttori
            .filter(function (v) { return !v.input.checked; })
            .map(function (v) { return v.indice; });
        salvaColonneNascoste(tabella, nascoste);
        document.querySelectorAll('table[data-table-id="' + tableId + '"]').forEach(applicaColonne);
        mostraTutte.disabled = nascoste.length === 0;
    }

    var nascoste = colonneNascoste(tabella);
    Array.prototype.forEach.call(headerRow.children, function (th, i) {
        // Skip the column-picker button's own (usually label-less) cell.
        if (th.classList.contains("col-actions-header") && !th.firstChild.textContent.trim() && th.children.length <= 1) return;

        var riga = document.createElement("label");
        riga.className = "scheda-riga colonna-opzione";
        var nome = document.createElement("span");
        nome.textContent = nomeColonna(th, i);
        var input = document.createElement("input");
        input.type = "checkbox";
        input.className = "interruttore";
        input.checked = nascoste.indexOf(i) === -1;
        input.addEventListener("change", aggiorna);
        riga.appendChild(nome);
        riga.appendChild(input);
        gruppo.appendChild(riga);
        interruttori.push({ indice: i, input: input });
    });
    corpo.appendChild(gruppo);

    var piede = document.createElement("div");
    piede.className = "colonne-piede";
    var nota = document.createElement("span");
    nota.className = "muted";
    nota.textContent = "La scelta resta salvata in questo browser.";
    var mostraTutte = document.createElement("button");
    mostraTutte.type = "button";
    mostraTutte.className = "btn-testo";
    mostraTutte.textContent = "Mostra tutte";
    mostraTutte.disabled = nascoste.length === 0;
    mostraTutte.addEventListener("click", function () {
        interruttori.forEach(function (v) { v.input.checked = true; });
        aggiorna();
    });
    piede.appendChild(nota);
    piede.appendChild(mostraTutte);
    corpo.appendChild(piede);

    dialog.addEventListener("click", function (e) {
        if (e.target === dialog) dialog.close();
    });
    dialog.addEventListener("close", function () { dialog.remove(); });
    document.body.appendChild(dialog);
    dialog.showModal();
}

document.addEventListener("click", function (e) {
    var btn = e.target.closest(".col-toggle-btn");
    if (!btn) return;
    var tabella = btn.closest("table");
    if (tabella) apriDialogoColonne(tabella);
});

function applicaPaginazione(tabella) {
    var corpo = tabella.tBodies[0];
    if (!corpo) return;
    // Rows hidden by the display preference (see applicaNascondiScaduti)
    // are left out entirely: they must not fill page slots nor be
    // counted in "N risultati".
    var righe = Array.prototype.slice.call(corpo.rows).filter(function (tr) {
        return !tr.classList.contains("stato-nascosto");
    });
    var dimensione = righePerPaginaCorrente();
    var totale = righe.length;
    var totalePagine = Math.max(1, Math.ceil(totale / dimensione));
    var pagina = tabella._paginaCorrente || 1;
    if (pagina > totalePagine) pagina = totalePagine;
    tabella._paginaCorrente = pagina;

    righe.forEach(function (tr, i) {
        var paginaRiga = Math.floor(i / dimensione) + 1;
        tr.classList.toggle("pag-hidden", paginaRiga !== pagina);
    });

    renderControlliPaginazione(tabella, pagina, totalePagine, totale, dimensione);
}

function renderControlliPaginazione(tabella, pagina, totalePagine, totale, dimensione) {
    var footer = tabella._paginazioneFooter;
    if (!footer || !footer.isConnected) {
        footer = document.createElement("div");
        footer.className = "paginazione";
        tabella.insertAdjacentElement("afterend", footer);
        tabella._paginazioneFooter = footer;
    }
    footer.innerHTML =
        '<label class="paginazione-size">Righe per pagina:' +
        '<select class="pagina-size-select">' +
        PAGE_SIZES.map(function (n) {
            return '<option value="' + n + '"' + (n === dimensione ? " selected" : "") + '>' + n + "</option>";
        }).join("") +
        "</select></label>" +
        '<span class="paginazione-info">Pagina ' + pagina + " di " + totalePagine + " (" + totale + " risultati)</span>" +
        '<span class="paginazione-nav">' +
        '<button type="button" class="btn btn-secondary pagina-prec"' + (pagina <= 1 ? " disabled" : "") + ">&larr; Precedente</button>" +
        '<button type="button" class="btn btn-secondary pagina-succ"' + (pagina >= totalePagine ? " disabled" : "") + ">Successiva &rarr;</button>" +
        "</span>";
}

document.addEventListener("click", function (e) {
    var prec = e.target.closest(".pagina-prec");
    var succ = e.target.closest(".pagina-succ");
    if (!prec && !succ) return;
    var footer = (prec || succ).closest(".paginazione");
    var tabella = footer.previousElementSibling;
    if (!tabella || !tabella.hasAttribute("data-table-id")) return;
    tabella._paginaCorrente = (tabella._paginaCorrente || 1) + (prec ? -1 : 1);
    applicaPaginazione(tabella);
});

document.addEventListener("change", function (e) {
    var sel = e.target.closest(".pagina-size-select");
    if (!sel) return;
    localStorage.setItem(PAGE_SIZE_KEY, sel.value);
    document.querySelectorAll("table[data-table-id]").forEach(function (t) {
        t._paginaCorrente = 1;
        applicaPaginazione(t);
    });
});

// ---------------------------------------------------------------
// "Nascondi scaduti e disdetti" (servizi page only).
//
// This is a display PREFERENCE, not a filter: the filters live in the
// URL and are deliberately forgotten when you navigate away, whereas
// this one must survive opening a service and coming back, so it is
// saved in localStorage like the column/page-size choices. Rows carry
// their computed contract state in data-stato (see servizi/_riga.html),
// so hiding them needs no round-trip.

var NASCONDI_SCADUTI_KEY = "scadenziario:nascondiScaduti";
var STATI_NASCOSTI = ["scaduto", "disdetto"];

function nascondiScadutiAttivo() {
    return localStorage.getItem(NASCONDI_SCADUTI_KEY) === "1";
}

function applicaNascondiScaduti(tabella) {
    // Only the servizi table has a contract state per row.
    if (tabella.getAttribute("data-table-id") !== "servizi") return;
    var corpo = tabella.tBodies[0];
    if (!corpo) return;
    var attivo = nascondiScadutiAttivo();
    Array.prototype.forEach.call(corpo.rows, function (tr) {
        var stato = tr.getAttribute("data-stato");
        tr.classList.toggle(
            "stato-nascosto",
            attivo && STATI_NASCOSTI.indexOf(stato) !== -1
        );
    });
}

document.addEventListener("change", function (e) {
    if (e.target.id !== "nascondi-scaduti") return;
    localStorage.setItem(NASCONDI_SCADUTI_KEY, e.target.checked ? "1" : "0");
    document.querySelectorAll('table[data-table-id="servizi"]').forEach(function (t) {
        applicaNascondiScaduti(t);
        // Row count changed, so page 1 is the only safe page to land on.
        t._paginaCorrente = 1;
        applicaPaginazione(t);
    });
});

function initTabelleDati(root) {
    // The checkbox sits in the filter bar, which HTMX redraws with the
    // results: tick it again from the saved preference every time. Being
    // always in view, a ticked box is itself the sign that rows are hidden.
    var chk = root.querySelector("#nascondi-scaduti");
    if (chk) chk.checked = nascondiScadutiAttivo();
    root.querySelectorAll("table[data-table-id]").forEach(function (tabella) {
        applicaColonne(tabella);
        applicaOrdinamento(tabella);
        applicaNascondiScaduti(tabella);
        tabella._paginaCorrente = 1;
        applicaPaginazione(tabella);
    });
}
document.addEventListener("DOMContentLoaded", function () {
    initTabelleDati(document);
});
document.addEventListener("htmx:afterSwap", function (e) { initTabelleDati(e.target); });

// Dropdowns with a search box: any <select data-ricerca> becomes a Tom Select
// control (the library is loaded only by the pages that use it, hence the
// guard). The <select> stays in the page and keeps being the value that is
// posted; Tom Select only replaces what the user sees and types into.
function initSelectRicerca(root) {
    if (typeof TomSelect === "undefined") return;
    root.querySelectorAll("select[data-ricerca]").forEach(function (el) {
        if (el.tomselect) return;   // already enhanced (e.g. after an HTMX swap)
        new TomSelect(el, {
            create: false,
            // Tom Select shows 50 options by default and silently drops the
            // rest: with more customers than that, the ones past the 50th
            // would be unreachable unless you happened to type their name.
            maxOptions: null,
            // Alphabetical, including a customer added later with the "+".
            // This sorts on the option text exactly as written in the HTML,
            // which is why the template keeps the labels free of whitespace.
            sortField: [{ field: "text", direction: "asc" }],
            placeholder: "— seleziona o cerca —",
            // Accents do not matter when searching: "societa" finds "Società".
            diacritics: true,
        });
    });
}
// The dropdowns of a filter bar (<select data-filtro>): searchable too, but
// unlike the form's customer picker they keep the server's order ("Tutti"
// first, the states in their natural order rather than alphabetical) and
// show "Tutti" as a real choice. Tom Select fires "change" on the original
// <select>, so the bar's hx-trigger="change" works as before.
// A dropdown with a filter set is tinted (.filtro-attivo): the bar tells at a
// glance what is narrowing the table.
function initSelectFiltro(root) {
    if (typeof TomSelect === "undefined") return;
    root.querySelectorAll("select[data-filtro]").forEach(function (el) {
        if (el.tomselect) return;
        var ts = new TomSelect(el, {
            create: false,
            maxOptions: null,
            allowEmptyOption: true,     // "Tutti" has an empty value
            diacritics: true,
            placeholder: "Cerca…",
        });
        function segnaAttivo() {
            ts.wrapper.classList.toggle("filtro-attivo", el.value !== "");
        }
        segnaAttivo();
        el.addEventListener("change", segnaAttivo);
    });
}

document.addEventListener("DOMContentLoaded", function () {
    initSelectRicerca(document);
    initSelectFiltro(document);
});
// After the SETTLE, not the swap: while settling, HTMX briefly gives each new
// element the attributes of the old one with the same id. The old <select>
// carried Tom Select's "hidden" classes; enhancing the new one at that moment
// copied them onto its new control, which then stayed invisible.
document.addEventListener("htmx:afterSettle", function (e) {
    initSelectRicerca(e.target);
    initSelectFiltro(e.target);
});

// "Nuovo cliente" dialog in the service form. POST /clienti/rapido answers a
// successful save with an HX-Trigger header carrying the new customer's id
// and name; here it joins the dropdown and gets selected, and the dialog
// closes. Nothing else in the service form is touched, which is the whole
// point of creating the customer in place.
document.body.addEventListener("clienteCreato", function (e) {
    var select = document.getElementById("cliente_id");
    if (!select) return;
    var id = String(e.detail.id);
    if (select.tomselect) {
        // With the search box on, the visible list is Tom Select's: adding the
        // option to the hidden <select> alone would never show up.
        select.tomselect.addOption({ value: id, text: e.detail.nome });
        select.tomselect.setValue(id);
        select.tomselect.close();
    } else {
        // Plain <select> (the library failed to load, e.g. CDN unreachable):
        // insert in alphabetical position by hand, keep "— seleziona —" on top.
        var nuovo = new Option(e.detail.nome, id);
        var prima = null;
        for (var i = 0; i < select.options.length; i++) {
            var opt = select.options[i];
            if (opt.value === "") continue;
            if (opt.text.localeCompare(e.detail.nome, "it", { sensitivity: "base" }) > 0) {
                prima = opt;
                break;
            }
        }
        select.insertBefore(nuovo, prima);
        select.value = id;
        select.dispatchEvent(new Event("change", { bubbles: true }));
    }
    var dialog = document.getElementById("nuovo-cliente-dialog");
    if (dialog) dialog.close();
});

// Service sheet: clicking a table row marked data-anteprima opens a dialog
// with everything about that service, fetched from the server at that moment
// (routes/servizi.anteprima). The row keeps its own controls: a click on a
// link, button or field inside it does what it always did. Nor does it open
// while text is selected — dragging across a serial number to copy it is
// a click too, as far as the browser is concerned.
// The page the row was clicked on travels as "ritorno", so the sheet's
// "Modifica" brings the user back here, filters included.
document.addEventListener("click", function (e) {
    var riga = e.target.closest("tr[data-anteprima]");
    if (!riga) return;
    if (e.target.closest("a, button, input, select, textarea, label")) return;
    var selezione = window.getSelection();
    if (selezione && selezione.toString().trim() !== "") return;
    var dialog = document.getElementById("anteprima-dialog");
    if (!dialog || typeof htmx === "undefined") return;
    var base = riga.getAttribute("data-anteprima");
    var url = base + (base.indexOf("?") === -1 ? "?" : "&") +
        "ritorno=" + encodeURIComponent(location.pathname + location.search);
    // Opened once the content is in, so it never flashes up empty.
    htmx.ajax("GET", url, { target: "#anteprima-corpo", swap: "innerHTML" })
        .then(function () {
            if (!dialog.open) dialog.showModal();
            dialog.scrollTop = 0;   // a sheet opened before may have been scrolled
        });
});

// A click on the dimmed backdrop closes the sheet, as on a phone. The
// backdrop belongs to the <dialog> element itself, so a click whose target
// is the dialog (not something inside it) landed outside the content.
document.addEventListener("click", function (e) {
    if (e.target.id === "anteprima-dialog") e.target.close();
});

// Any button marked data-chiudi-dialog closes the <dialog> it sits in.
// Delegated, so it also works on dialog content swapped in by HTMX.
document.addEventListener("click", function (e) {
    var bottone = e.target.closest("[data-chiudi-dialog]");
    if (!bottone) return;
    var dialog = bottone.closest("dialog");
    if (dialog) dialog.close();
});

// "PDF" button on the services page: export exactly what is on screen. The
// filters live in the URL, kept current by hx-push-url as they change, so the
// link is built at click time rather than when the page loaded. The "nascondi
// scaduti e disdetti" preference lives only here in the browser, so it is
// added explicitly — the server has no other way to know about it.
document.addEventListener("click", function (e) {
    var link = e.target.closest("[data-export-pdf]");
    if (!link) return;
    var parametri = new URLSearchParams(window.location.search);
    if (nascondiScadutiAttivo()) parametri.set("nascondi", "1");
    var query = parametri.toString();
    link.href = "/servizi/export/pdf" + (query ? "?" + query : "");
});
