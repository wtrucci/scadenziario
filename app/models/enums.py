import enum


class TipoServizio(enum.Enum):
    licenza = "licenza"
    contratto = "contratto"
    abbonamento = "abbonamento"
    altro = "altro"


class RuoloUtente(enum.Enum):
    admin = "admin"
    utente = "utente"


class CanalNotifica(enum.Enum):
    # New notification channels (e.g. email, webhook) should be added here
    # and handled in app/services/notifiche.py.
    telegram = "telegram"


class TipoNotifica(enum.Enum):
    # The three independent notification triggers (see app/services/notifiche.py).
    # Kept apart from CanalNotifica because a single channel can send any of
    # these, and each is deduplicated separately.
    preavviso = "preavviso"                    # servizio.preavviso_giorni before the occurrence
    promemoria_7_giorni = "promemoria_7_giorni"  # fixed, regardless of preavviso_giorni
    contratto_scaduto = "contratto_scaduto"      # stato_contratto == "scaduto" (never for rinnovo_automatico)
