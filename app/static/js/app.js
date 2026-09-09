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

// Filter dialogs (funnel icon next to the page title): picking a
// filter already triggers the HTMX request via hx-trigger="change" on
// the form: this just closes the dialog afterwards so the (already
// updating) results are immediately visible.
document.addEventListener("change", function (e) {
    var dialog = e.target.closest("dialog.filtri-dialog");
    if (dialog) dialog.close();
});

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

function leggiOrdinamento(tableId) {
    try {
        var raw = localStorage.getItem(ORDINAMENTO_KEY_PREFIX + tableId);
        return raw ? JSON.parse(raw) : null;
    } catch (e) {
        return null;
    }
}

function salvaOrdinamento(tableId, indice, crescente) {
    localStorage.setItem(ORDINAMENTO_KEY_PREFIX + tableId, JSON.stringify({ indice: indice, crescente: crescente }));
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
        salvaOrdinamento(tabella.getAttribute("data-table-id"), indice, crescente);
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
    var ordinamento = leggiOrdinamento(tableId);
    if (!ordinamento) return;
    var th = tabella.tHead.rows[0].children[ordinamento.indice];
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

function colonneNascoste(tableId) {
    try {
        var raw = localStorage.getItem(COLONNE_KEY_PREFIX + tableId);
        return raw ? JSON.parse(raw) : [];
    } catch (e) {
        return [];
    }
}

function salvaColonneNascoste(tableId, indici) {
    localStorage.setItem(COLONNE_KEY_PREFIX + tableId, JSON.stringify(indici));
}

function righePerPaginaCorrente() {
    var v = parseInt(localStorage.getItem(PAGE_SIZE_KEY), 10);
    return PAGE_SIZES.indexOf(v) !== -1 ? v : 50;
}

function applicaColonne(tabella) {
    var tableId = tabella.getAttribute("data-table-id");
    if (!tableId || !tabella.tHead) return;
    var nascoste = colonneNascoste(tableId);
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
    var nascoste = colonneNascoste(tableId);

    var dialog = document.createElement("dialog");
    dialog.className = "colonne-dialog";

    var titolo = document.createElement("h3");
    titolo.textContent = "Colonne visibili";
    dialog.appendChild(titolo);

    Array.prototype.forEach.call(headerRow.children, function (th, i) {
        // Skip the column-picker button's own (usually label-less) cell.
        if (th.classList.contains("col-actions-header") && !th.firstChild.textContent.trim() && th.children.length <= 1) return;
        // The label is normally the th's own text nodes (skipping the
        // gear button and any other markup), but a header that wraps
        // its label in .th-label (to control where the sort arrow
        // lands) has no bare text node — read that span instead.
        var etichetta = th.querySelector(".th-label");
        var testo = etichetta
            ? etichetta.textContent.trim()
            : th.childNodes.length
                ? Array.prototype.map.call(th.childNodes, function (n) {
                      return n.nodeType === Node.TEXT_NODE ? n.textContent : "";
                  }).join("").trim()
                : th.textContent.trim();
        if (!testo) testo = "Colonna " + (i + 1);

        var label = document.createElement("label");
        label.className = "colonna-opzione";
        var checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.checked = nascoste.indexOf(i) === -1;
        checkbox.addEventListener("change", function () {
            var attuali = colonneNascoste(tableId);
            if (checkbox.checked) {
                attuali = attuali.filter(function (n) { return n !== i; });
            } else if (attuali.indexOf(i) === -1) {
                attuali.push(i);
            }
            salvaColonneNascoste(tableId, attuali);
            document.querySelectorAll('table[data-table-id="' + tableId + '"]').forEach(applicaColonne);
        });
        label.appendChild(checkbox);
        label.appendChild(document.createTextNode(testo));
        dialog.appendChild(label);
    });

    var azioni = document.createElement("div");
    azioni.className = "colonne-dialog-actions";
    var chiudi = document.createElement("button");
    chiudi.type = "button";
    chiudi.className = "btn btn-secondary";
    chiudi.textContent = "Chiudi";
    chiudi.addEventListener("click", function () { dialog.close(); });
    azioni.appendChild(chiudi);
    dialog.appendChild(azioni);

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

// Shows a dot on the funnel icon while the preference is on: rows are
// missing from the table and nothing else on the page would say why.
function aggiornaIndicatorePreferenza() {
    var badge = document.getElementById("pref-badge");
    if (!badge) return;
    var attivo = nascondiScadutiAttivo();
    badge.innerHTML = attivo ? '<span class="icon-btn-badge-pref"></span>' : "";
    var btn = badge.closest(".icon-btn");
    if (btn) {
        btn.title = attivo ? "Filtri — scaduti e disdetti nascosti" : "Filtri";
    }
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
    aggiornaIndicatorePreferenza();
    document.querySelectorAll('table[data-table-id="servizi"]').forEach(function (t) {
        applicaNascondiScaduti(t);
        // Row count changed, so page 1 is the only safe page to land on.
        t._paginaCorrente = 1;
        applicaPaginazione(t);
    });
});

function initTabelleDati(root) {
    root.querySelectorAll("table[data-table-id]").forEach(function (tabella) {
        applicaColonne(tabella);
        applicaOrdinamento(tabella);
        applicaNascondiScaduti(tabella);
        tabella._paginaCorrente = 1;
        applicaPaginazione(tabella);
    });
}
document.addEventListener("DOMContentLoaded", function () {
    // The checkbox lives in the filter dialog, outside any HTMX target,
    // so it only needs syncing on a full page load.
    var chk = document.getElementById("nascondi-scaduti");
    if (chk) chk.checked = nascondiScadutiAttivo();
    aggiornaIndicatorePreferenza();
    initTabelleDati(document);
});
document.addEventListener("htmx:afterSwap", function (e) { initTabelleDati(e.target); });
