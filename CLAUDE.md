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
  sessioni. Ruoli base (admin / utente). Predisporre per futura integrazione
  OIDC, ma NON implementarla ora.

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
  (licenza/contratto/abbonamento/altro), data_inizio, durata_mesi, data_fine,
  rinnovo_automatico (bool), cadenza_mesi (int), importo (prezzo unitario di
  default), quantita (default 1), valuta, preavviso_giorni, disdetto (bool),
  referente, note.
  - **Stato mostrato in UI** (Attivo/In scadenza/Scaduto/Disdetto): NON è una
    colonna. È CALCOLATO da `stato_contratto(servizio, oggi)` in
    app/services/occorrenze.py, con questa precedenza:
    1. `disdetto=True` (unico flag manuale) → sempre "Disdetto", qualunque sia
       la data.
    2. altrimenti, confronta `data_fine_effettiva` con oggi (+ preavviso_giorni)
       → "Scaduto" / "In scadenza" / "Attivo". Un contratto a rinnovo
       automatico non risulta MAI "Scaduto" (la sua fine effettiva non è mai
       nel passato).
    Il vecchio Enum `StatoServizio` (attivo/scaduto/rinnovato/disdetto,
    scelto a mano nel form) è stato rimosso: "rinnovato" non esiste più
    (superato da durata_mesi/rinnovo_automatico), "attivo/in_scadenza/scaduto"
    non sono più decisioni manuali. `disdetto` è l'unica decisione commerciale
    che le date da sole non possono dedurre (il cliente ha annullato il
    contratto), quindi resta un campo persistito.
  - **disdetto=True sopprime gli alert delle occorrenze**: un'occorrenza di un
    contratto disdetto NON risulta mai "Da fatturare"/"In scadenza" (vedi
    `_stato_visivo` in occorrenze.py) — l'operatore non deve essere sollecitato
    a fatturare qualcosa che il cliente ha annullato. Se l'occorrenza era già
    stata fatturata prima della disdetta, resta "Fatturato" (la disdetta non
    tocca lo storico di fatturazione).
  - **durata_mesi**: per quanti mesi il contratto viene fatturato (durata),
    concetto DISTINTO da `cadenza_mesi` (ogni quanto viene fatturato). Es. un
    contratto annuale fatturato mensilmente ha cadenza_mesi=1, durata_mesi=12
    (12 occorrenze).
  - **data_fine** si CALCOLA da data_inizio + durata_mesi (vedi
    `calcola_data_fine`: giorno prima dell'anniversario a durata_mesi di
    distanza, così una durata di 12 mesi da gennaio copre gennaio–dicembre
    inclusi) e NON si inserisce mai a mano nel form: il campo "Data fine" del
    form storico è stato sostituito da "Durata contratto (mesi)". Resta una
    colonna sul DB (serve al filtro SQL "quali contratti ricadono nel mese").
  - **rinnovo_automatico**: significato COMMERCIALE: "fatturo senza chiedere
    conferma al cliente". Se attivo, il contratto si rinnova da solo di un
    altro blocco (durata_rinnovo_mesi se impostata, altrimenti durata_mesi)
    ogni volta che altrimenti sarebbe scaduto. Il rinnovo NON scrive nulla
    nel DB: la data di fine EFFETTIVA (che tiene conto del rinnovo) si
    calcola al volo ad ogni lettura con
    `data_fine_effettiva(servizio, riferimento)` in
    app/services/occorrenze.py, sullo stesso principio delle occorrenze
    stesse ("calcolato, non salvato" — niente scheduler di avanzamento). Le
    query SQL che filtrano i servizi per mese (vedi
    app/services/riepilogo.py) devono includere i servizi con
    rinnovo_automatico=True indipendentemente dalla data_fine STORICA salvata,
    altrimenti sparirebbero dalla dashboard una volta superata la prima
    scadenza.
  - **SENZA rinnovo_automatico — proposta di rinnovo e conferma tramite
    fatturazione** (deciso con l'utente, vedi anche "Occorrenza" sotto): il
    rinnovo va confermato dal cliente. Il sistema genera comunque UNA
    occorrenza oltre la data_fine — la "proposta di rinnovo", che cade
    sull'anniversario successivo (= data_fine + 1 giorno) — che compare in
    dashboard/riepilogo e genera le notifiche man mano che si avvicina.
    - Marcarla **fatturata** = il cliente ha confermato: il contratto si
      ESTENDE di un blocco di rinnovo (durata_mesi += blocco, data_fine
      ricalcolata; data_inizio NON si muove, così lo storico resta visibile).
      Al primo rinnovo confermato durata_rinnovo_mesi viene valorizzata con
      il blocco usato (altrimenti la conferma successiva userebbe come passo
      il totale accumulato). Vedi `_estendi_se_rinnovo_confermato` in
      app/routes/servizi.py; smarcarla ritira l'estensione
      (`_ritira_estensione_se_smarcato`).
    - Se il cliente non conferma: dopo la data_fine lo stato è "Scaduto",
      la proposta resta "Da fatturare"; si risolve fatturando (estende) o
      spuntando disdetto (sopprime avvisi e proposta).
    - La proposta è UNA sola (nessuna proiezione oltre): dopo, non si sa
      nulla finché il cliente non conferma.
    - Il filtro SQL del mese ha un margine di 1 giorno su data_fine perché
      la proposta (data_fine+1) può cadere nel mese successivo alla
      data_fine salvata.
    - Il backfill alla creazione ("occorrenze passate = fatturate per
      definizione", vedi sotto) NON tocca la proposta: è proprio la
      decisione pendente.
  - **durata_rinnovo_mesi** (opzionale): lunghezza del blocco di rinnovo se
    diversa dal periodo iniziale (es. 36 mesi iniziali, poi rinnovi annuali
    di 12). Vuota = i rinnovi durano quanto durata_mesi.
  - **cadenza_mesi**: ogni quanti mesi il servizio va fatturato (1 = mensile,
    3 = trimestrale, 6 = semestrale, 12 = annuale, ecc.). Sostituisce il
    vecchio Enum `ricorrenza`. NON esistono più servizi "una tantum": tutto è
    ricorrente (per un pagamento singolo si imposta durata_mesi <= cadenza_mesi,
    così solo la prima occorrenza ricade nel periodo del contratto).
  - **importo** è il prezzo unitario di default di OGNI occorrenza; il totale
    di un'occorrenza è `quantita * importo` salvo override (vedi sotto). La
    quantità serve per servizi a postazione/licenza (es. antivirus).
  - **referente**: persona/collega a cui va fatturato il servizio, quando
    diverso dal cliente finale. Campo testuale opzionale.

- **Occorrenza** (concetto CALCOLATO, NON una tabella): una singola scadenza
  fatturabile. Le occorrenze si generano al volo partendo da data_inizio e
  aggiungendo cadenza_mesi ripetutamente, finché la data <= data_fine EFFETTIVA
  (`data_fine_effettiva`: con rinnovo_automatico può superare la data_fine
  storicizzata sul DB). ECCEZIONE: un contratto SENZA rinnovo_automatico e non
  disdetto genera UNA occorrenza in più oltre la data_fine, la "proposta di
  rinnovo" (vedi rinnovo_automatico sopra).
  - Ogni occorrenza cade nel **giorno del mese di data_inizio** (es. inizio il
    15 → ogni occorrenza il giorno 15 del suo mese). Se un mese non ha quel
    giorno (es. il 31 a febbraio), usare l'ultimo giorno valido del mese.
  - Il totale di un'occorrenza usa l'importo/quantita del servizio, a meno che
    esista un OverrideImporto per quella specifica data.

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
  (es. fatturato / da fatturare). I filtri sono combinabili.
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
- Gestione utenti (solo admin).

> NOTA: con il modello a occorrenze calcolate, lo "scheduler di avanzamento
> automatico delle scadenze" non serve: sia le occorrenze sia (quando
> rinnovo_automatico è attivo) la data di fine effettiva del contratto si
> ricalcolano al volo ad ogni lettura, non tramite un job periodico che scrive
> sul DB. Il modello RinnovoLog non esiste: il rinnovo non produce una riga di
> log, è puro calcolo. Resta valido lo scheduler per le NOTIFICHE.

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
- Niente PostgreSQL come default (ma non precludere la migrazione).
- Niente fatturazione vera in questa fase (niente numerazione fiscale,
  PDF/A, firma digitale, invio automatico al cliente): solo riepilogo,
  esportabile in CSV (tutti i clienti) o PDF (per singolo cliente — vedi
  sopra).

## Modalità di lavoro con l'autore

- Procedere per piccoli incrementi verificabili, un blocco alla volta.
- Prima di modifiche estese, spiegare il piano e attendere conferma.
  - Dopo ogni blocco significativo, proporre un commit Git con messaggio chiaro.
