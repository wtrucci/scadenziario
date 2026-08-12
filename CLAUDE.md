# Scadenziario — Gestione servizi a scadenza clienti

## Scopo del progetto

Applicazione web self-hosted per tracciare i servizi a scadenza dei clienti
(licenze, contratti di assistenza, abbonamenti). Fornisce notifiche prima della
scadenza e una vista mensile dei servizi in scadenza per produrre un riepilogo
da fatturare.

Il progetto sarà rilasciato come open source.

## Utenti

- Uso interno: l'amministratore e alcuni colleghi (multi-utente con login).
- I clienti NON accedono all'applicazione.

## Principi guida (in ordine di priorità)

1. **Codice pulito e mantenibile** — leggibilità prima di tutto. Separazione
   netta delle responsabilità, nomi chiari, niente scorciatoie.
2. **MVP funzionante in fretta** — funzionalità essenziali prima, fronzoli dopo.
3. **Pronto per open source** — README, licenza, niente segreti nel codice.

L'autore è un system integrator, NON un programmatore professionista. Il codice
deve essere comprensibile e ben commentato dove la logica non è ovvia. Spiega
le scelte tecniche quando non sono banali.

## Stack tecnologico

- **Linguaggio:** Python 3.12+
- **Backend:** FastAPI
- **ORM:** SQLAlchemy 2.x
- **Database:** SQLite (file singolo, volume montato). Mantenere la
  compatibilità con una futura migrazione a PostgreSQL.
- **Frontend:** template server-side Jinja2 + HTMX (NIENTE SPA, niente build
  step JavaScript, niente npm per il frontend). Librerie JS puntuali (es.
  flatpickr per i date picker) si caricano da CDN come HTMX, mai via npm.
- **Date nei form:** i campi data usano flatpickr (via CDN) invece del widget
  nativo `<input type="date">`, che segue la lingua del browser/OS e può
  mostrare mm/dd/yyyy. Mostra sempre dd/mm/yyyy; il valore inviato al server
  resta ISO yyyy-mm-dd.
- **Tema chiaro/scuro:** icona toggle in alto a destra su ogni pagina. Scelta
  salvata in `localStorage`, applicata prima del render per evitare flash; al
  primo accesso segue la preferenza del sistema operativo. Tutti i colori sono
  variabili CSS in `style.css` (incluse le sfumature di stato: righe/badge di
  scadenza, fatturazione, ecc.), ridefinite sotto `:root[data-theme="dark"]`.
- **Scheduler notifiche:** APScheduler nel processo applicativo.
- **Autenticazione:** login utente/password con hashing sicuro (bcrypt/argon2),
  sessioni. Il modello Utente ha un campo `ruolo` (admin / utente) ma NON è
  usato per autorizzare: chi entra vede tutto. Predisporre per futura
  integrazione OIDC, ma NON implementarla ora.

## Canali di notifica

- **Telegram** (chiamata HTTP a Bot API).
- NIENTE email/SMTP in questa fase.
- NIENTE webhook in questa fase (verranno aggiunti in un secondo momento;
  progettare il codice delle notifiche in modo estendibile a nuovi canali).
- Soglia di preavviso configurabile per servizio (giorni prima della scadenza).
- Registrare ogni notifica inviata (log con esito) per evitare invii doppi.

## Modello dati

> NOTA STORICA: il modello del Servizio è stato rivisto. In origine un servizio
> aveva una singola `data_scadenza` e una `ricorrenza` come etichetta. Ora un
> servizio è un CONTRATTO con un periodo e una cadenza di fatturazione, che
> genera più "occorrenze" fatturabili nel tempo. Vedi sotto.

- **Cliente**: id, nome, note, attivo (bool).
- **Servizio**: id, cliente_id (FK), descrizione, tipo
  (licenza/contratto/abbonamento/altro), **data_scadenza**, cadenza_mesi,
  durata_impegno_mesi (opz.), rinnovo_automatico (bool), data_inizio (opz.,
  informativa), importo, quantita, valuta, preavviso_giorni, disdetto (bool),
  referente, note.
  - **Tutto si regge su `data_scadenza`**: è il giorno da cui parte il ciclo
    di fatturazione corrente, e le occorrenze si generano avanzando di
    `cadenza_mesi` da lì. Non c'è nessun'altra data che governi calcoli.
  - **cadenza_mesi**: ogni quanti mesi si fattura (1 = mensile, 12 = annuale…).
  - **durata_impegno_mesi** (opzionale): per quanti mesi il cliente è
    impegnato. **Vuota = l'impegno è una singola fattura**, quindi ogni
    occorrenza è già un rinnovo — è il caso normale (37 contratti su 38).
    Si compila solo quando un impegno si fattura a rate: un abbonamento
    annuale fatturato ogni mese ha `cadenza_mesi=1, durata_impegno_mesi=12`.
  - **rinnovo_automatico**: il ciclo si ripete senza chiedere nulla, quindi le
    occorrenze proseguono all'infinito. Senza, la generazione si ferma al primo
    inizio-ciclo NON fatturato: quella è la **proposta di rinnovo**, la
    decisione che spetta al cliente, e oltre non si sa nulla.
  - **Fatturare la proposta È la conferma.** Non scrive niente sul contratto:
    la generazione riparte da sola perché quella riga di stato ora dice
    "fatturato". Smarcarla rimette il rinnovo in sospeso, senza contabilità da
    disfare. `data_scadenza` non si muove mai: storico e righe di stato restano
    agganciati.
  - **data_inizio** è solo un promemoria di quando il servizio è partito e non
    entra in nessun calcolo.
  - **Stato mostrato in UI** (Attivo/In scadenza/Scaduto/Disdetto): NON è una
    colonna, è calcolato da `stato_contratto` sulla `fine_copertura`:
    1. `disdetto=True` → sempre "Disdetto".
    2. altrimenti si guarda fin dove il cliente è coperto. Un contratto a
       rinnovo automatico non è MAI "Scaduto": si rinnova che tu abbia
       fatturato o no, quindi una fattura in ritardo appare come
       "da fatturare", non come contratto scaduto.
  - **disdetto=True sopprime gli alert delle occorrenze** e la proposta di
    rinnovo: l'operatore non deve essere sollecitato a fatturare qualcosa che
    il cliente ha annullato.
  - **importo** è il prezzo unitario di OGNI occorrenza; il totale è
    `quantita * importo` salvo override.
  - **referente**: persona a cui va fatturato, quando diverso dal cliente.

> NOTA STORICA: il modello derivava tutto da `data_inizio` + `durata_mesi` +
> `cadenza_mesi` + `durata_rinnovo_mesi`. Quattro manopole, di cui due dicevano
> la stessa cosa quando coincidevano e diventavano ambigue quando divergevano:
> una licenza triennale pagata in anticipo e una fatturata annualmente si
> inserivano allo stesso modo, e l'app sceglieva la seconda lettura,
> programmando fatture dentro un periodo già pagato. Da qui il modello a data
> unica.

- **Occorrenza** (concetto CALCOLATO, NON una tabella): una singola scadenza
  fatturabile. Si generano avanzando di `cadenza_mesi` da `data_scadenza`.
  Le date che cadono su un confine di impegno aprono un nuovo ciclo; quelle in
  mezzo sono le sue rate.
  - Ogni occorrenza cade nel **giorno del mese di data_scadenza**; se un mese
    non ha quel giorno (il 31 a febbraio) si usa l'ultimo giorno valido, e il
    giorno si recupera appena un mese è di nuovo abbastanza lungo (niente
    deriva).
  - Il totale usa importo/quantita del servizio, salvo OverrideImporto per
    quella data.

- **OverrideImporto** (tabella di STATO per-occorrenza): id, servizio_id (FK),
  data_occorrenza, importo (nullable), quantita (nullable), fatturato (bool,
  default False), fatturato_il (nullable). Rappresenta "ciò che rende speciale
  una specifica occorrenza":
  - importo/quantita: se valorizzati, fissano il prezzo/quantità di quella
    occorrenza (se NULL si usa il default del servizio). Possono essere
    valorizzati per due motivi DIVERSI, distinti dal flag override_manuale:
    - **override_manuale=True**: l'utente ha fatto una correzione deliberata
      del prezzo/quantità di quella occorrenza (es. sconto). In dashboard
      mostra il badge "Override".
    - **override_manuale=False** ma importo valorizzato: è uno SNAPSHOT
      automatico salvato al momento della fatturazione (vedi sotto). NON mostra
      il badge "Override": è il prezzo normale, semplicemente congelato.
  - fatturato: True quando l'utente marca quell'occorrenza come fatturata;
    fatturato_il registra quando. Al passaggio a fatturato=True, l'app salva
    uno SNAPSHOT del prezzo/quantità effettivi del momento (override-aware)
    nella riga, così un futuro cambio di prezzo del servizio NON altera ciò
    che è già stato fatturato. Allo "smarco" (fatturato=False), lo snapshot
    viene rimosso (importo/quantita tornano NULL) SOLO se override_manuale è
    False; se è una correzione manuale, NON va cancellata.
  - È uno stato PERSISTENTE per-occorrenza, e poiché le occorrenze non sono
    righe, questa tabella è il posto dove salvarlo. Marcare/smarcare o
    correggere un'occorrenza crea o aggiorna la riga per
    (servizio_id, data_occorrenza).
  - **note** (testo, opzionale): nota sulla riga di stato, per annotare il
    perché di una correzione manuale (es. "sconto fedeltà").
  - cascade delete con il servizio. Vincolo di unicità su
    (servizio_id, data_occorrenza): al massimo una riga di stato per
    occorrenza.

> DECISIONE sul prezzo che cambia nel tempo (aumenti): NON si usa un prezzo
> storicizzato. Per aumentare il prezzo si modifica direttamente
> servizio.importo: il nuovo prezzo vale per le occorrenze future E per quelle
> passate non ancora fatturate (accettato dall'utente). Le occorrenze già
> fatturate restano congelate grazie allo snapshot salvato nella riga di stato
> al momento della fatturazione. Una vera storicizzazione del prezzo (listini
> con data di decorrenza) è un possibile miglioramento futuro.

- **Utente**: id, username, password_hash, ruolo, attivo.
- **NotificaLog**: id, servizio_id (FK), canale, inviata_il, esito, dettaglio.

## Funzionalità chiave

- CRUD completo clienti e servizi.
- La pagina servizi mostra anche la colonna **Referente**.
- **La "Scadenza" mostrata NON è `data_scadenza`**: è `prossima_scadenza`, cioè
  la data del prossimo RINNOVO, calcolata. Due ragioni:
  - `data_scadenza` è un'ancora che non si muove mai, quindi su un contratto a
    rinnovo automatico scivola nel passato mentre il contratto è vivissimo;
  - le **rate non sono scadenze**. Un abbonamento annuale fatturato ogni mese
    scade una volta l'anno, non dodici: le rate sono fatture da emettere e
    vivono in dashboard e nel riepilogo. Si escludono con `apre_ciclo`.
  Se non c'è nessun rinnovo futuro si mostra l'ultimo (un rinnovo mai
  confermato è proprio ciò che va guardato); se non c'è alcuna occorrenza
  (contratto disdetto) si ripiega sull'ancora.
  Nel form la data modificabile resta l'ancora, etichettata **"Decorrenza del
  ciclo corrente"**, e accanto compare la scadenza calcolata in sola lettura:
  aprire un contratto deve sempre rispondere a "quando scade?".
- Dashboard: mostra le **occorrenze** che cadono nel mese selezionato (NON i
  servizi una volta sola). Un servizio mensile valido per 12 mesi compare in
  12 mesi diversi, uno trimestrale ogni 3 mesi, ecc.
  - Le occorrenze riportano un flag **fatturato** (dalla tabella di stato).
  - Stato visivo di un'occorrenza (sostituisce il vecchio "passata"):
    - fatturato=True → mostrata come "Fatturato".
    - non fatturata e data passata/scaduta → ALERT "Da fatturare" ben
      evidenziato (è ciò che l'utente non deve dimenticare): NON va sbiadita.
    - non fatturata e in avvicinamento entro preavviso_giorni → "In scadenza".
    - non fatturata e futura → stato normale.
  - L'utente può marcare/smarcare "fatturato" su una singola occorrenza
    (toggle, via HTMX), che crea/aggiorna la relativa riga di stato.
- Filtri su pagina servizi e dashboard: per cliente, per referente e per stato
  (es. fatturato / da fatturare). I filtri sono combinabili. Vivono nella query
  string, quindi sono condivisibili con un link ma NON sopravvivono alla
  navigazione: sono una domanda del momento, non una preferenza.
- **Preferenze di visualizzazione delle tabelle** (colonne visibili, righe per
  pagina, ordinamento, "nascondi contratti scaduti e disdetti"): al contrario
  dei filtri sono scelte durature, quindi si salvano in `localStorage` e si
  riapplicano ad ogni caricamento — chi torna dalla modifica di un servizio
  ritrova la tabella come l'aveva lasciata. Sono applicate lato client
  (`base.html`), senza round-trip al server.
  - "Nascondi scaduti e disdetti" esiste SOLO nella pagina servizi: la
    dashboard mostra occorrenze di un mese, dove "scaduto" non è un criterio
    sensato. Si appoggia a `data-stato` sulla riga, e la paginazione deve
    contare solo le righe non nascoste.
  - Quando una preferenza sta nascondendo dei dati, l'icona che apre la
    finestra mostra un indicatore visivo: una tabella filtrata non deve mai
    sembrare una tabella vuota.
- Vista "riepilogo da fatturare" per un mese selezionato:
  - Esclude SOLO le occorrenze di servizi disdetti (`Servizio.disdetto`);
    "scaduto"/"in scadenza"/"attivo" non influenzano questo filtro (sono stato
    calcolato, non un criterio di esclusione — vedi sopra).
  - Raggruppa per cliente, con subtotale per cliente e totale complessivo del
    mese. Il totale di ogni occorrenza rispetta eventuali correzioni di
    importo presenti nella tabella di stato per-occorrenza.
  - Esportabile in CSV (tutti i clienti del mese, un unico file).
  - Esportabile anche in PDF, ma solo **per singolo cliente** (un pulsante
    "PDF" accanto a ciascun gruppo cliente in `riepilogo/index.html`,
    `GET /riepilogo/export/pdf?cliente_id=...`): pensato per essere
    inoltrato al cliente stesso, a differenza del CSV che resta un export
    interno per l'intero mese. Generato con `fpdf2` (Python puro, nessuna
    dipendenza di sistema come Pango/Cairo) in `app/services/pdf.py`. Non è
    fatturazione vera (niente numerazione fiscale, PDF/A, firma, ecc.): resta
    un riepilogo, solo in un formato diverso dal CSV.
- Calcolo occorrenze: implementare in un modulo di servizio dedicato e ben
  testato (è il cuore dell'app). Mantenere SEMPRE Decimal per i valori
  monetari, mai float.
- Invio notifiche automatiche secondo la soglia di preavviso (sulle occorrenze
  in avvicinamento).
  - Si notificano SOLO le occorrenze che aprono un ciclo (`apre_ciclo`), cioè i
    rinnovi, mai le rate interne a un impegno: un abbonamento annuale fatturato
    ogni mese è UNA scadenza da presidiare, non dodici, e avvisare ad ogni rata
    abitua l'operatore a ignorare il canale. Le rate restano visibili in
    dashboard e nel riepilogo — sono fatture da emettere, non scadenze da
    inseguire. Con `durata_impegno_mesi` vuota ogni occorrenza è un rinnovo,
    quindi per il contratto normale non cambia nulla.

> NOTA: con il modello a occorrenze calcolate, lo "scheduler di avanzamento
> automatico delle scadenze" non serve: sia le occorrenze sia (quando
> rinnovo_automatico è attivo) la data di fine effettiva del contratto si
> ricalcolano al volo ad ogni lettura, non tramite un job periodico che scrive
> sul DB. Il modello RinnovoLog non esiste: il rinnovo non produce una riga di
> log, è puro calcolo. Resta valido lo scheduler per le NOTIFICHE.

## Backup

- Job APScheduler notturno (accanto a quello delle notifiche) che copia il DB
  in `data/backup/`, configurato da `BACKUP_*`. Il codice sta in
  `app/services/backup.py`.
- La copia usa **`sqlite3.backup()`**, mai `cp`: l'app può scrivere mentre il
  job gira e una copia grezza può risultare inservibile.
- Ogni copia viene **riaperta e verificata** con `PRAGMA integrity_check`; se
  fallisce viene cancellata e parte un avviso Telegram. Un backup corrotto
  lasciato lì è peggio di nessun backup, perché sembra un punto di ripristino.
- Il nome porta un timestamp al secondo, quindi due esecuzioni ravvicinate
  devono prendere nomi diversi: un fallimento cancella il PROPRIO file, e senza
  il suffisso distruggerebbe la copia buona appena fatta.
- `BACKUP_KEEP` < 1 significa "tieni tutto", MAI "cancella tutto".
- Solo copie locali: portarle fuori sede è compito dell'host (rsync, client
  cloud), per non mettere credenziali di terze parti nell'app.

## Rilascio

- La versione vive in **`app/__init__.py`** (`__version__`) ed è l'unica fonte:
  da lì la leggono l'interfaccia (piè di pagina della sidebar, via
  `templates.env.globals`) e le API docs. `APP_VERSION` può sovrascriverla per
  una build particolare, senza toccare il sorgente.
- **Aggiornare `__version__` nello stesso commit che si tagga**: altrimenti
  l'interfaccia dichiara una versione che non esiste. Un test in
  `tests/test_versione.py` verifica che i punti restino allineati fra loro.
- Su GitHub **tag e release sono due cose diverse**: il push del tag fa partire
  il workflow Docker ma non basta a creare la release. Dalla 0.8.2 se ne occupa
  il job `release` in `.github/workflows/docker-publish.yml`, che gira dopo la
  pubblicazione dell'immagine e usa il **messaggio del tag annotato** come note.
  Quindi: tag annotato sempre, prima riga = titolo. Le versioni precedenti alla
  0.8.1 restano volutamente senza release.
- Il testo del tag finisce nelle note passando da un **file** e da variabili
  d'ambiente, mai interpolato nel comando shell: è testo arbitrario, e
  incollarlo in un comando è il modo in cui virgolette e backtick diventano
  codice eseguito.

## Convenzioni di progetto

- Configurazione SOLO via variabili d'ambiente (token Telegram, URL webhook,
  chiave segreta sessioni, percorso DB). Mai segreti nel codice o nel repo.
- Fornire un file `.env.example` documentato. Il vero `.env` va in `.gitignore`.
- Struttura del progetto chiara: separare modelli, rotte/viste, logica di
  business (servizi), template, configurazione.
- Migrazioni del database con Alembic.
- Commenti, docstring, nomi di variabili/funzioni e contenuto di `.env` /
  `.env.example`: tutto in **inglese**.
- **README in italiano** per questa fase (verrà tradotto in inglese più avanti).

## Packaging e deploy

- **Dockerfile** basato su immagine Python slim (Debian).
- **docker-compose.yml** con un solo servizio applicativo e un volume per il DB.
- Timezone Europe/Rome.
- README con istruzioni di avvio chiare per chi usa Docker.

## Cosa NON fare (per ora)

- Niente SPA / framework JS frontend.
- Niente Celery/Redis (lo scheduler interno basta).
- Niente email/SMTP.
- Niente webhook (rimandati a fase successiva; canale notifiche estendibile).
- Niente integrazione OIDC/AD (solo predisporre).
- Niente gestione utenti da interfaccia (CRUD utenti, cambio password, ruoli
  applicati): il primo admin si crea da `.env` al primo avvio e per ora basta.
  Il campo `ruolo` esiste sul modello ma non autorizza nulla.
- Niente PostgreSQL come default (ma non precludere la migrazione).
- Niente fatturazione vera in questa fase (niente numerazione fiscale,
  PDF/A, firma digitale, invio automatico al cliente): solo riepilogo,
  esportabile in CSV (tutti i clienti) o PDF (per singolo cliente — vedi
  sopra).

## Modalità di lavoro con l'autore

- Procedere per piccoli incrementi verificabili, un blocco alla volta.
- Prima di modifiche estese, spiegare il piano e attendere conferma.
  - Dopo ogni blocco significativo, proporre un commit Git con messaggio chiaro.
