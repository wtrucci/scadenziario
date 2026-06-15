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

## Modello dati

> NOTA STORICA: il modello del Servizio è stato rivisto. In origine un servizio
> aveva una singola `data_scadenza` e una `ricorrenza` come etichetta. Ora un
> servizio è un CONTRATTO con un periodo e una cadenza di fatturazione, che
> genera più "occorrenze" fatturabili nel tempo. Vedi sotto.

- **Cliente**: id, nome, note, attivo (bool).
- **Servizio**: id, cliente_id (FK), descrizione, tipo
  (licenza/contratto/abbonamento/altro), data_inizio, data_fine,
  cadenza_mesi (int), importo (prezzo unitario di default), quantita
  (default 1), valuta, preavviso_giorni, stato
  (attivo/scaduto/rinnovato/disdetto), referente, note.
  - **data_inizio / data_fine**: il servizio è valido da data_inizio fino a
    data_fine (fine contratto, inclusa).
  - **cadenza_mesi**: ogni quanti mesi il servizio va fatturato (1 = mensile,
    3 = trimestrale, 6 = semestrale, 12 = annuale, ecc.). Sostituisce il
    vecchio Enum `ricorrenza`. NON esistono più servizi "una tantum": tutto è
    ricorrente (per un pagamento singolo si usa data_fine = data_inizio).
  - **importo** è il prezzo unitario di default di OGNI occorrenza; il totale
    di un'occorrenza è `quantita * importo` salvo override (vedi sotto). La
    quantità serve per servizi a postazione/licenza (es. antivirus).
  - **referente**: persona/collega a cui va fatturato il servizio, quando
    diverso dal cliente finale. Campo testuale opzionale.

- **Occorrenza** (concetto CALCOLATO, NON una tabella): una singola scadenza
  fatturabile. Le occorrenze si generano al volo partendo da data_inizio e
  aggiungendo cadenza_mesi ripetutamente, finché la data <= data_fine.
  - Ogni occorrenza cade nel **giorno del mese di data_inizio** (es. inizio il
    15 → ogni occorrenza il giorno 15 del suo mese). Se un mese non ha quel
    giorno (es. il 31 a febbraio), usare l'ultimo giorno valido del mese.
  - Il totale di un'occorrenza usa l'importo/quantita del servizio, a meno che
    esista un OverrideImporto per quella specifica data.

- **OverrideImporto**: id, servizio_id (FK), data_occorrenza, importo,
  quantita. Permette di correggere importo/quantita di UNA specifica
  occorrenza senza cambiare il default del servizio. La logica di calcolo
  delle occorrenze, per ogni data, usa l'override se presente, altrimenti il
  default del servizio. cascade delete con il servizio. Vincolo di unicità su
  (servizio_id, data_occorrenza).

- **Utente**: id, username, password_hash, ruolo, attivo.
- **NotificaLog**: id, servizio_id (FK), canale, inviata_il, esito, dettaglio.

## Funzionalità chiave

- CRUD completo clienti e servizi.
- La pagina servizi mostra anche la colonna **Referente**.
- Dashboard: mostra le **occorrenze** che cadono nel mese selezionato (NON i
  servizi una volta sola). Un servizio mensile valido per 12 mesi compare in
  12 mesi diversi, uno trimestrale ogni 3 mesi, ecc.
  - Le occorrenze di mesi **già passati** vengono comunque mostrate, ma marcate
    visivamente come "passate".
  - Evidenziare le occorrenze scadute / in scadenza entro preavviso_giorni.
- Vista "riepilogo da fatturare" per un mese selezionato:
  - Include SOLO occorrenze di servizi in stato "attivo" (esclude
    disdetti/rinnovati).
  - Raggruppa per cliente, con subtotale per cliente e totale complessivo del
    mese. Il totale di ogni occorrenza rispetta eventuali OverrideImporto.
  - Esportabile in CSV.
- Calcolo occorrenze: implementare in un modulo di servizio dedicato e ben
  testato (è il cuore dell'app). Mantenere SEMPRE Decimal per i valori
  monetari, mai float.
- Invio notifiche automatiche secondo la soglia di preavviso (sulle occorrenze
  in avvicinamento).
- Gestione utenti (solo admin).

> NOTA: con il modello a occorrenze calcolate, lo "scheduler di avanzamento
> automatico delle scadenze" non serve più: le occorrenze future esistono già
> in modo implicito tra data_inizio e data_fine. Il flag rinnovo_automatico e
> il modello RinnovoLog sono quindi rimossi dal progetto. Resta valido lo
> scheduler per le NOTIFICHE.

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