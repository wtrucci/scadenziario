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
  step JavaScript, niente npm per il frontend).
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

## Modello dati (iniziale)

- **Cliente**: id, nome, note, attivo (bool).
- **Servizio**: id, cliente_id (FK), descrizione, tipo
  (licenza/contratto/abbonamento/altro), data_scadenza, importo (prezzo
  unitario), quantita (default 1), valuta, ricorrenza
  (annuale/mensile/una-tantum), preavviso_giorni, stato
  (attivo/scaduto/rinnovato/disdetto), referente, note.
  - **importo** è il prezzo unitario; il totale del servizio è
    `quantita * importo`. La quantità serve per servizi a postazione/licenza
    (es. antivirus: N postazioni x prezzo unitario).
  - **referente**: persona/collega a cui va fatturato il servizio, quando
    diverso dal cliente finale (es. fatturo a un collega i servizi dei suoi
    clienti). Campo testuale opzionale.
- **Utente**: id, username, password_hash, ruolo, attivo.
- **NotificaLog**: id, servizio_id (FK), canale, inviata_il, esito, dettaglio.

## Funzionalità chiave

- CRUD completo clienti e servizi.
- Dashboard con servizi raggruppati per mese di scadenza.
- Vista "riepilogo da fatturare" per un mese selezionato: servizi in scadenza
  raggruppati per cliente (e con possibilità di raggruppare/filtrare per
  referente) con somma dei totali (`quantita * importo`). Esportabile (CSV).
- Invio notifiche automatiche secondo la soglia di preavviso.
- Gestione utenti (solo admin).

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
- Niente fatturazione vera/PDF in questa fase (solo riepilogo + export CSV).

## Modalità di lavoro con l'autore

- Procedere per piccoli incrementi verificabili, un blocco alla volta.
- Prima di modifiche estese, spiegare il piano e attendere conferma.
- Dopo ogni blocco significativo, proporre un commit Git con messaggio chiaro.
