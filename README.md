# Scadenziario

Applicazione web self-hosted per tracciare i servizi a scadenza dei clienti
(licenze, contratti di assistenza, abbonamenti): genera automaticamente le
scadenze fatturabili, invia notifiche Telegram in avvicinamento alla
scadenza e produce un riepilogo mensile da fatturare, esportabile in CSV o
PDF.

Pensata per uso interno (system integrator, rivenditori, chi gestisce
rinnovi periodici per conto di più clienti), non per l'accesso dei clienti
finali.

## Funzionalità

- **Clienti e servizi**: anagrafica clienti, servizi come contratti
  ricorrenti (data di scadenza, cadenza di fatturazione, importo,
  quantità, rinnovo automatico).
- **Occorrenze calcolate**: le singole scadenze fatturabili non sono
  righe nel database, ma calcolate al volo avanzando di `cadenza_mesi`
  dalla data di scadenza del contratto (vedi
  [Come funziona un contratto](#come-funziona-un-contratto)).
- **Proposta di rinnovo**: per i contratti senza rinnovo automatico
  l'app si ferma al primo rinnovo non ancora fatturato — la decisione
  spetta al cliente, e oltre non si può sapere nulla. Fatturare quella
  occorrenza vale come conferma e fa proseguire il calcolo.
- **Dashboard mensile**: tutte le occorrenze del mese selezionato, con
  stato visivo (Da fatturare / In scadenza / Fatturato), filtri per
  cliente/referente/stato e toggle "fatturato" per occorrenza.
- **Riepilogo da fatturare**: le occorrenze ancora da fatturare nel mese,
  raggruppate per cliente con subtotali, esportabili in CSV (tutti i
  clienti) o PDF (per singolo cliente, da inoltrare al cliente stesso).
  Marcatura "fatturato" singola o massiva per cliente, sincronizzata in
  tempo reale con la dashboard.
- **Notifiche Telegram**: tre promemoria indipendenti — preavviso
  personalizzato per servizio, promemoria fisso 7 giorni prima, e avviso
  di contratto scaduto (mai per i contratti a rinnovo automatico).
  Supporta l'invio a un topic specifico nei supergruppi Telegram.
- **Autenticazione**: login con sessioni, password con hashing bcrypt.
- **Backup automatico**: copia notturna del database, verificata con
  `integrity_check` e ruotata (vedi [Backup e ripristino](#backup-e-ripristino)).

## Come funziona un contratto

Tutto si regge su **una sola data**: `data_scadenza`, il giorno da cui
parte il ciclo di fatturazione corrente. Le occorrenze si generano
avanzando di `cadenza_mesi` da lì (1 = mensile, 12 = annuale…), sempre
sullo stesso giorno del mese; se un mese è troppo corto (il 31 a
febbraio) si usa l'ultimo giorno valido e il giorno si recupera appena
un mese è di nuovo abbastanza lungo, senza deriva.

Gli altri campi servono solo nei casi particolari:

| Campo | A cosa serve |
|---|---|
| `durata_impegno_mesi` | Solo se un impegno si fattura **a rate**: un abbonamento annuale fatturato ogni mese è `cadenza_mesi=1, durata_impegno_mesi=12`. Lasciato vuoto, ogni occorrenza è già un rinnovo — è il caso normale. |
| `rinnovo_automatico` | Il ciclo si ripete senza chiedere nulla, quindi le occorrenze proseguono all'infinito. Senza, il calcolo si ferma alla proposta di rinnovo. |
| `data_inizio` | Solo un promemoria di quando il servizio è partito: non entra in nessun calcolo. |
| `disdetto` | Sopprime gli alert e la proposta di rinnovo, ed esclude il servizio dal riepilogo. |

Lo stato mostrato in elenco (Attivo / In scadenza / Scaduto / Disdetto)
non è una colonna del database: è calcolato da fin dove il cliente
risulta coperto. Un contratto a rinnovo automatico non è mai "Scaduto" —
si rinnova che tu abbia fatturato o no, quindi una fattura in ritardo
compare come "da fatturare", che è il problema reale.

**Fatturare non riscrive mai il contratto.** `data_scadenza` non si
muove: l'unica cosa che cambia è la riga di stato dell'occorrenza, per
cui smarcare una fatturazione rimette semplicemente in sospeso il
rinnovo, senza contabilità da disfare.

## Stack tecnologico

- **Backend**: Python 3.12+, FastAPI, SQLAlchemy 2.x
- **Database**: SQLite (compatibile con una futura migrazione a
  PostgreSQL), migrazioni con Alembic
- **Frontend**: template server-side Jinja2 + HTMX (nessuna SPA, nessun
  build step JavaScript)
- **Notifiche**: Telegram Bot API, scheduler interno APScheduler
- **PDF**: fpdf2 (Python puro, nessuna dipendenza di sistema)

## Avvio con Docker Compose (consigliato)

Richiede Docker e Docker Compose. `docker-compose.yml` è già configurato
per usare l'immagine pubblicata su GitHub Container Registry
(`ghcr.io/wtrucci/scadenziario`): non serve clonare il repository né
buildare nulla in locale.

**1. Scarica i due file necessari** (bastano questi, non serve il resto del
repository):

```bash
mkdir scadenziario && cd scadenziario
curl -O https://raw.githubusercontent.com/wtrucci/scadenziario/master/docker-compose.yml
curl -O https://raw.githubusercontent.com/wtrucci/scadenziario/master/.env.example
```

**2. Crea il file `.env`** a partire dall'esempio e compilalo con i tuoi
valori (almeno `SECRET_KEY`; `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` se
vuoi le notifiche; `FIRST_ADMIN_USERNAME`/`FIRST_ADMIN_PASSWORD` per il
primo accesso):

```bash
cp .env.example .env
nano .env   # o l'editor che preferisci
```

**3. Avvia**:

```bash
docker compose up -d
```

La prima volta scarica l'immagine da GitHub Container Registry, crea la
cartella `data/` per il database e crea l'utente admin definito in `.env`.
L'app è raggiungibile su `http://localhost:8000`.

**4. Comandi utili**:

```bash
docker compose logs -f       # segui i log
docker compose pull          # scarica l'ultima immagine pubblicata
docker compose up -d         # riavvia usando l'immagine appena scaricata
docker compose down          # ferma e rimuove il container (i dati restano in ./data)
```

### Dove viene salvato il database

`docker-compose.yml` monta la cartella **`./data`**, accanto al file
`docker-compose.yml` stesso, dentro il container (`/app/data`): è lì che
finisce il file SQLite (`data/scadenziario.db`), non in un volume Docker
"nascosto". Vantaggi pratici:

- il database resta a disposizione anche rimuovendo il container
  (`docker compose down`) o l'immagine;
- `data/` è già esclusa da Git (vedi `.gitignore`), quindi non c'è rischio
  di versionare per sbaglio dati reali dei clienti.

### Sviluppo: buildare l'immagine in locale invece di scaricarla

Se hai clonato l'intero repository e vuoi testare le tue modifiche (invece
di usare l'immagine pubblicata), `docker-compose.yml` include anche
`build: .`: basta aggiungere `--build` per far buildare Compose in locale
invece di scaricare da GitHub Container Registry:

```bash
docker compose up -d --build
```

### Immagine pubblicata (dettagli)

Ad ogni push su `master` e ad ogni tag di versione (`vX.Y.Z`), un workflow
GitHub Actions (`.github/workflows/docker-publish.yml`) builda l'immagine e
la pubblica su GitHub Container Registry — non serve un account Docker Hub.
Tag disponibili:

```bash
docker pull ghcr.io/wtrucci/scadenziario:latest
# oppure una versione specifica (il tag Docker non ha il prefisso "v"):
docker pull ghcr.io/wtrucci/scadenziario:0.1.0
```

> Nota: la prima volta che il workflow pubblica un'immagine, il pacchetto su
> GitHub va reso pubblico manualmente (Package → Package settings →
> Change visibility), altrimenti richiede autenticazione anche solo per il
> download.

## Backup e ripristino

L'app fa da sé una copia del database ogni notte alle 03:00 (configurabile,
vedi `BACKUP_*` in `.env.example`), in `data/backup/`. Tiene le ultime 30
copie giornaliere e cancella le più vecchie.

Due dettagli non ovvi:

- la copia usa l'API `backup()` di SQLite, non `cp`: l'app può stare
  scrivendo mentre il job gira, e una copia grezza in quel momento può
  risultare inservibile;
- ogni copia appena scritta viene **riaperta e verificata** con
  `PRAGMA integrity_check`. Se non passa viene cancellata e parte un avviso
  Telegram, perché un backup corrotto lasciato nella cartella è peggio di
  nessun backup: sembra un punto di ripristino.

Le copie stanno **accanto al database**, dentro lo stesso volume: ti
proteggono da una cancellazione per sbaglio, da un import andato storto o da
una corruzione del file, **non** dalla perdita del disco. Per quella serve una
copia fuori sede — un `rsync` di `data/backup/` verso un NAS o uno spazio
cloud — deliberatamente lasciata all'host, per non mettere credenziali di
terze parti dentro l'applicazione.

### Ripristinare

Ogni backup è un database SQLite completo e autonomo: ripristinare significa
rimettere quel file al posto di quello corrente. Il database usa
`journal_mode = delete`, quindi non ci sono file `-wal`/`-shm` da tenere
allineati: c'è un solo file da sostituire.

```bash
docker compose down                                  # 1. ferma l'app SEMPRE
cp data/scadenziario.db data/scadenziario.db.prima   # 2. mettiti al riparo
cp data/backup/scadenziario-AAAAMMGG-HHMMSS.db data/scadenziario.db
docker compose up -d
```

Il passo 1 non è opzionale: sostituire il file mentre l'app lo tiene aperto
lascia il processo agganciato al file vecchio. Il passo 2 serve se il backup
scelto è più vecchio di quanto credevi — senza, quello che stava in mezzo è
perso.

Se ripristini un backup **precedente a una modifica dello schema**, dopo la
copia serve allineare le migrazioni:

```bash
sqlite3 data/backup/<file>.db "select version_num from alembic_version"
docker compose exec app alembic upgrade head
```

Il caso opposto non si risolve: un backup più recente del codice non si può
declassare. Se torni a una versione precedente dell'app, torna anche a un
backup di quel periodo.

> Un ripristino mai provato è un'ipotesi. Vale la pena fare la prova una
> volta su una copia della cartella `data/`, con un compose su un'altra
> porta, così la prima volta che serve davvero non è anche la prima volta che
> lo fai.

La cartella viene creata automaticamente al primo avvio se non esiste già.

## Avvio in locale (sviluppo, senza Docker)

Richiede Python 3.12+.

```bash
python -m venv .venv
source .venv/bin/activate          # su Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# modifica .env con i tuoi valori

alembic upgrade head
uvicorn app.main:app --reload
```

L'applicazione parte su `http://127.0.0.1:8000`.

## Configurazione

Tutta la configurazione passa da variabili d'ambiente (vedi
`.env.example` per l'elenco completo e commentato). Le principali:

| Variabile | Descrizione |
|---|---|
| `SECRET_KEY` | Chiave per firmare i cookie di sessione. Obbligatoria. |
| `DATABASE_URL` | Percorso del database SQLite. |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | Credenziali del bot Telegram per le notifiche. `TELEGRAM_CHAT_ID` accetta anche `CHAT_ID:TOPIC_ID` per un topic di un supergruppo. |
| `NOTIFICATION_CHECK_INTERVAL_MINUTES` | Ogni quanto lo scheduler controlla le scadenze in avvicinamento. |
| `BACKUP_ENABLED` / `BACKUP_DIR` / `BACKUP_HOUR` / `BACKUP_KEEP` | Backup notturno del database: se attivo, dove scrive, a che ora, quante copie tenere (vedi [Backup e ripristino](#backup-e-ripristino)). |
| `TZ` | Timezone usata da scheduler e visualizzazione date (`Europe/Rome`). |
| `FIRST_ADMIN_USERNAME` / `FIRST_ADMIN_PASSWORD` | Credenziali del primo utente admin, create solo se il database utenti è vuoto. |

Il file `.env` non va mai versionato (è già in `.gitignore`).

## Test

```bash
source .venv/bin/activate
python -m pytest -q
```

## Struttura del progetto

```
app/
  models/       modelli SQLAlchemy (Cliente, Servizio, Utente, ...)
  routes/       endpoint FastAPI, raggruppati per area
  services/     logica di business (calcolo occorrenze, riepilogo, notifiche, PDF, backup)
  templates/    template Jinja2 (server-side, HTMX)
  static/       CSS e JS statici
alembic/        migrazioni del database
tests/          test automatici (pytest)
```

## Licenza

[MIT](LICENSE) — uso, modifica e distribuzione liberi, anche a livello
commerciale.
