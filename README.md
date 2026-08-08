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
  ricorrenti (data inizio, durata in mesi, cadenza di fatturazione,
  importo, quantità, rinnovo automatico).
- **Occorrenze calcolate**: le singole scadenze fatturabili non sono
  righe nel database, ma calcolate al volo dal contratto (data inizio +
  cadenza), incluso il rinnovo automatico.
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

## Stack tecnologico

- **Backend**: Python 3.12+, FastAPI, SQLAlchemy 2.x
- **Database**: SQLite (compatibile con una futura migrazione a
  PostgreSQL), migrazioni con Alembic
- **Frontend**: template server-side Jinja2 + HTMX (nessuna SPA, nessun
  build step JavaScript)
- **Notifiche**: Telegram Bot API, scheduler interno APScheduler
- **PDF**: fpdf2 (Python puro, nessuna dipendenza di sistema)

## Avvio con Docker (consigliato)

Richiede Docker e Docker Compose.

```bash
cp .env.example .env
# modifica .env: SECRET_KEY, credenziali Telegram, primo utente admin
docker compose up --build
```

L'applicazione parte su `http://localhost:8000`. Al primo avvio vengono
applicate automaticamente tutte le migrazioni del database e viene creato
l'utente admin definito in `.env` (`FIRST_ADMIN_USERNAME` /
`FIRST_ADMIN_PASSWORD`).

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

### Immagine già pronta (GitHub Container Registry)

Ad ogni push su `master` e ad ogni tag di versione (`vX.Y.Z`), un workflow
GitHub Actions (`.github/workflows/docker-publish.yml`) builda l'immagine e
la pubblica su GitHub Container Registry — non serve un account Docker Hub.
Per usarla senza clonare il repository:

```bash
docker pull ghcr.io/wtrucci/scadenziario:latest
# oppure una versione specifica (il tag Docker non ha il prefisso "v"):
docker pull ghcr.io/wtrucci/scadenziario:0.1.0
```

Va poi eseguita passando le stesse variabili d'ambiente di `.env.example`
e montando una cartella locale per i dati, ad esempio:

```bash
docker run --env-file .env -p 8000:8000 -v ./data:/app/data \
  ghcr.io/wtrucci/scadenziario:latest
```

oppure sostituendo `build: .` con `image: ghcr.io/wtrucci/scadenziario:latest`
in `docker-compose.yml` (il mount di `./data` resta invariato).

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
