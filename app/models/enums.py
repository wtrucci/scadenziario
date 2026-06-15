import enum


class TipoServizio(enum.Enum):
    licenza = "licenza"
    contratto = "contratto"
    abbonamento = "abbonamento"
    altro = "altro"


class StatoServizio(enum.Enum):
    attivo = "attivo"
    scaduto = "scaduto"
    rinnovato = "rinnovato"
    disdetto = "disdetto"


class RuoloUtente(enum.Enum):
    admin = "admin"
    utente = "utente"


class CanalNotifica(enum.Enum):
    # New notification channels (e.g. email, webhook) should be added here
    # and handled in app/services/notifiche.py.
    telegram = "telegram"
