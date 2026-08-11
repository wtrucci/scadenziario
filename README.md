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

- il backup è copiare la cartella `data/` (l'app va fermata prima, per
  evitare di copiare il file mentre SQLite ci scrive);
- il database resta a disposizione anche rimuovendo il container
  (`docker compose down`) o l'immagine;
- `data/` è già esclusa da Git (vedi `.gitignore`), quindi non c'è rischio
  di versionare per sbaglio dati reali dei clienti.

La cartella viene creata automaticamente al primo avvio se non esiste già.

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
  services/     logica di business (calcolo occorrenze, riepilogo, notifiche, PDF)
  templates/    template Jinja2 (server-side, HTMX)
  static/       CSS e JS statici
alembic/        migrazioni del database
tests/          test automatici (pytest)
```

## Licenza

[MIT](LICENSE) — uso, modifica e distribuzione liberi, anche a livello
commerciale.
