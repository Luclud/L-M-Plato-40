"""Guest messaging + approval notes (ticket T09).

* ``compose_guest_message(kind, language, **facts)`` - deterministic, polite templates in
  de/en/it/fr/es/nl (Sie / you / Lei / vous / usted / u). Optional LLM rewording (``plato40.llm``,
  behind ``.env``) is accepted only if it keeps every digit (times, room ids) unchanged;
  otherwise the template is used. ``source`` says which one was used.
* ``parse_requested_time(text)`` - requested clock time from free guest text.
* ``approval_note(...)`` - short note for the human approver.

A message produced here is a PROPOSAL. It is never proof of delivery, and the texts never
claim that anything was sent. Times in messages are LOCAL (Europe/Vienna, DST-aware): pass
'HH:MM' local strings or timezone-aware / UTC timestamps (converted with ``clock.local``).
"""
from __future__ import annotations

import datetime as _dt
import re

import pandas as pd

from . import llm
from .clock import local

SUPPORTED_LANGUAGES = ("de", "en", "it", "fr", "es", "nl")
LANGUAGE_NAMES = {"de": "German", "en": "English", "it": "Italian", "fr": "French",
                  "es": "Spanish", "nl": "Dutch"}

# building defaults (building.json operations) used only when the caller omits them
DEFAULT_CHECK_IN_FROM = "15:00"
DEFAULT_CHECK_OUT_UNTIL = "11:00"

# kinds used by agents (names are a contract - extend, never rename/remove)
KINDS = (
    "room_ready_eta",          # facts: room_id, eta_local (HH:MM)
    "early_checkin_possible",  # facts: room_id, eta_local
    "early_checkin_not_possible",  # facts: earliest_local
    "late_checkout_ok",        # facts: until_local
    "late_checkout_not_possible",  # facts: until_local (standard 11:00)
    "waiting_update",          # facts: room_id, eta_local
    "maintenance_consent_request",  # facts: room_id, reason, proposed_window_local
    "service_ack",             # facts: request_type
    "heating_update",          # facts: room_id
    # added in T09
    "room_ready",              # facts: room_id
    "arrival_delay_ack",       # facts: - (optional arrival_local)
    "cleaning_scheduled",      # facts: room_id, slot_local
    "towels_delivery",         # facts: room_id, slot_local
    "maintenance_scheduled",   # facts: room_id, slot_local
    "access_issue_ack",        # facts: - (optional room_id)
    "comfort_hot_update",      # facts: room_id
    "air_quality_update",      # facts: room_id
    "noise_ack",               # facts: -
)

# required facts per kind (missing -> neutral fallback text, ``missing_facts`` reported)
REQUIRED_FACTS = {
    "room_ready_eta": ("room_id", "eta_local"),
    "early_checkin_possible": ("room_id", "eta_local"),
    "early_checkin_not_possible": (),          # earliest_local defaults to check-in time
    "late_checkout_ok": ("until_local",),
    "late_checkout_not_possible": (),          # until_local defaults to check-out time
    "waiting_update": ("room_id", "eta_local"),
    "maintenance_consent_request": ("room_id", "proposed_window_local"),
    "service_ack": (),
    "heating_update": ("room_id",),
    "room_ready": ("room_id",),
    "arrival_delay_ack": (),
    "cleaning_scheduled": ("room_id", "slot_local"),
    "towels_delivery": ("room_id", "slot_local"),
    "maintenance_scheduled": ("room_id", "slot_local"),
    "access_issue_ack": (),
    "comfort_hot_update": ("room_id",),
    "air_quality_update": ("room_id",),
    "noise_ack": (),
}
# optional facts: room_id (all kinds), earliest_local, until_local, reason, reply_by_local,
# request_type, slot_local (service_ack), arrival_local (arrival_delay_ack)

# --- language building blocks ---------------------------------------------------------
_ROOM = {  # (nominative/accusative object, sentence start, "in <room>", dative-ish object)
    "en": ("your room{id}", "Your room{id}", "in your room{id}", "your room{id}"),
    "de": ("Ihr Zimmer{id}", "Ihr Zimmer{id}", "in Ihrem Zimmer{id}", "Ihrem Zimmer{id}"),
    "it": ("la Sua camera{id}", "La Sua camera{id}", "nella Sua camera{id}", "la Sua camera{id}"),
    "fr": ("votre chambre{id}", "Votre chambre{id}", "dans votre chambre{id}", "votre chambre{id}"),
    "es": ("su habitación{id}", "Su habitación{id}", "en su habitación{id}", "su habitación{id}"),
    "nl": ("uw kamer{id}", "Uw kamer{id}", "in uw kamer{id}", "uw kamer{id}"),
}

_SIGNOFF = {
    "en": "Kind regards, your PLATO-40 team",
    "de": "Freundliche Grüße, Ihr PLATO-40-Team",
    "it": "Cordiali saluti, il Suo team PLATO-40",
    "fr": "Cordialement, votre équipe PLATO-40",
    "es": "Saludos cordiales, su equipo PLATO-40",
    "nl": "Met vriendelijke groet, uw PLATO-40-team",
}

_FALLBACK = {
    "en": "Thank you for your message. We are looking into it and will get back to you shortly.",
    "de": "Vielen Dank für Ihre Nachricht. Wir kümmern uns darum und melden uns in Kürze bei Ihnen.",
    "it": "Grazie per il Suo messaggio. Ce ne stiamo occupando e La ricontatteremo a breve.",
    "fr": "Merci pour votre message. Nous nous en occupons et revenons vers vous très prochainement.",
    "es": "Gracias por su mensaje. Nos estamos ocupando de ello y le responderemos en breve.",
    "nl": "Bedankt voor uw bericht. We kijken ernaar en nemen spoedig contact met u op.",
}

_REQUEST_LABELS = {  # request_type -> (en, de, it, fr, es, nl)
    "early_checkin": ("early check-in", "früher Check-in", "check-in anticipato", "check-in anticipé",
                      "check-in anticipado", "vroeg inchecken"),
    "late_checkout": ("late check-out", "später Check-out", "check-out posticipato", "départ tardif",
                      "check-out tardío", "laat uitchecken"),
    "temperature_hot": ("room too warm", "Zimmer zu warm", "camera troppo calda", "chambre trop chaude",
                        "habitación demasiado caliente", "kamer te warm"),
    "temperature_cold": ("room too cold", "Zimmer zu kalt", "camera troppo fredda", "chambre trop froide",
                         "habitación demasiado fría", "kamer te koud"),
    "noise_complaint": ("noise", "Lärm", "rumore", "bruit", "ruido", "geluidsoverlast"),
    "wifi_help": ("Wi-Fi", "WLAN", "Wi-Fi", "Wi-Fi", "wifi", "wifi"),
    "extra_towels": ("extra towels", "zusätzliche Handtücher", "asciugamani aggiuntivi",
                     "serviettes supplémentaires", "toallas adicionales", "extra handdoeken"),
    "arrival_delay": ("late arrival", "spätere Ankunft", "arrivo in ritardo", "arrivée tardive",
                      "llegada tardía", "late aankomst"),
    "parking": ("parking", "Parken", "parcheggio", "stationnement", "aparcamiento", "parkeren"),
    "maintenance_issue": ("technical issue", "technisches Problem", "guasto tecnico", "problème technique",
                          "avería técnica", "technisch probleem"),
    "cleaning_request": ("cleaning", "Reinigung", "pulizia", "ménage", "limpieza", "schoonmaak"),
    "luggage_storage": ("luggage storage", "Gepäckaufbewahrung", "deposito bagagli", "consigne à bagages",
                        "consigna de equipaje", "bagageopslag"),
    "invoice": ("invoice", "Rechnung", "fattura", "facture", "factura", "factuur"),
    "access_issue": ("room access", "Zimmerzugang", "accesso alla camera", "accès à la chambre",
                     "acceso a la habitación", "toegang tot de kamer"),
    "waiting_for_room": ("waiting for your room", "Warten auf das Zimmer", "attesa della camera",
                         "attente de la chambre", "espera de la habitación", "wachten op de kamer"),
    "air_quality": ("air quality", "Luftqualität", "qualità dell'aria", "qualité de l'air",
                    "calidad del aire", "luchtkwaliteit"),
    "water_issue": ("water issue", "Wasserproblem", "problema idrico", "problème d'eau",
                    "problema de agua", "waterprobleem"),
    "_generic": ("request", "Anfrage", "richiesta", "demande", "solicitud", "verzoek"),
}

_REASONS = {  # reason code -> (en, de, it, fr, es, nl); unknown reasons are inserted verbatim
    "water_leak": ("possible water leak", "Verdacht auf Wasseraustritt", "sospetta perdita d'acqua",
                   "suspicion de fuite d'eau", "posible fuga de agua", "vermoedelijk waterlek"),
    "toilet_cistern": ("toilet cistern repair", "Reparatur der WC-Spülung", "riparazione della cassetta del WC",
                       "réparation de la chasse d'eau", "reparación de la cisterna del inodoro",
                       "reparatie van de toiletspoeling"),
    "heating": ("heating check", "Prüfung der Heizung", "controllo del riscaldamento", "contrôle du chauffage",
                "revisión de la calefacción", "controle van de verwarming"),
    "ventilation": ("ventilation check", "Prüfung der Lüftung", "controllo della ventilazione",
                    "contrôle de la ventilation", "revisión de la ventilación", "controle van de ventilatie"),
    "maintenance": ("maintenance work", "Wartungsarbeiten", "lavori di manutenzione", "travaux de maintenance",
                    "trabajos de mantenimiento", "onderhoudswerkzaamheden"),
    "inspection": ("short inspection", "kurze Inspektion", "breve ispezione", "courte inspection",
                   "breve inspección", "korte inspectie"),
    "repair": ("repair", "Reparatur", "riparazione", "réparation", "reparación", "reparatie"),
    "legionella_flush": ("hygiene flushing of the water pipes", "Hygienespülung der Wasserleitungen",
                         "lavaggio igienico delle tubature", "rinçage hygiénique des conduites d'eau",
                         "lavado higiénico de las tuberías", "hygiënische spoeling van de waterleidingen"),
}
_REASON_ALIASES = {"heating_comfort": "heating", "ventilation_maintenance": "ventilation",
                   "leak": "water_leak", "toilet": "toilet_cistern", "plumbing": "repair"}
_LANG_IDX = {lang: i for i, lang in enumerate(("en", "de", "it", "fr", "es", "nl"))}  # label tuple order

# --- templates: kind -> language -> text -----------------------------------------------
# slots: {room} {Room} {in_room} {room_dat} {eta} {earliest} {until} {window} {reason} {req}
#        {slot} {arrival} {reply_by}
TEMPLATES: dict[str, dict[str, str]] = {
    "room_ready_eta": {
        "en": "Thank you for your patience. {Room} is expected to be ready at about {eta}.",
        "de": "Vielen Dank für Ihre Geduld. {Room} ist voraussichtlich gegen {eta} Uhr bezugsfertig.",
        "it": "Grazie per la Sua pazienza. {Room} dovrebbe essere pronta verso le {eta}.",
        "fr": "Merci de votre patience. {Room} devrait être prête vers {eta}.",
        "es": "Gracias por su paciencia. {Room} estará lista previsiblemente hacia las {eta}.",
        "nl": "Bedankt voor uw geduld. {Room} is naar verwachting rond {eta} uur klaar.",
    },
    "early_checkin_possible": {
        "en": "Good news: an early check-in is possible. {Room} is expected to be ready at about {eta}; "
              "from then on you can check in via self check-in.",
        "de": "Gute Nachricht: Ein früher Check-in ist möglich. {Room} ist voraussichtlich gegen {eta} Uhr "
              "bereit; ab dann können Sie den Self-Check-in nutzen.",
        "it": "Buone notizie: il check-in anticipato è possibile. {Room} dovrebbe essere pronta verso le {eta}; "
              "da quel momento potrà effettuare il self check-in.",
        "fr": "Bonne nouvelle : un check-in anticipé est possible. {Room} devrait être prête vers {eta} ; "
              "vous pourrez ensuite utiliser le self check-in.",
        "es": "Buenas noticias: es posible hacer el check-in antes. {Room} estará lista previsiblemente hacia "
              "las {eta}; a partir de entonces podrá utilizar el self check-in.",
        "nl": "Goed nieuws: vroeg inchecken is mogelijk. {Room} is naar verwachting rond {eta} uur klaar; "
              "vanaf dan kunt u via de self-check-in inchecken.",
    },
    "early_checkin_not_possible": {
        "en": "Thank you for your request. Unfortunately an early check-in is not possible this time. "
              "{Room} will be available from {earliest}.",
        "de": "Vielen Dank für Ihre Anfrage. Leider ist ein früher Check-in diesmal nicht möglich. "
              "{Room} steht Ihnen ab {earliest} Uhr zur Verfügung.",
        "it": "Grazie per la Sua richiesta. Purtroppo questa volta il check-in anticipato non è possibile. "
              "{Room} sarà disponibile dalle {earliest}.",
        "fr": "Merci pour votre demande. Malheureusement, un check-in anticipé n'est pas possible cette fois-ci. "
              "{Room} sera disponible à partir de {earliest}.",
        "es": "Gracias por su solicitud. Lamentablemente, esta vez no es posible hacer el check-in antes. "
              "{Room} estará disponible a partir de las {earliest}.",
        "nl": "Bedankt voor uw verzoek. Helaas is vroeg inchecken deze keer niet mogelijk. "
              "{Room} is beschikbaar vanaf {earliest} uur.",
    },
    "late_checkout_ok": {
        "en": "Thank you for your request. A late check-out until {until} is possible for you.",
        "de": "Vielen Dank für Ihre Anfrage. Ein später Check-out bis {until} Uhr ist für Sie möglich.",
        "it": "Grazie per la Sua richiesta. Il check-out posticipato fino alle {until} è possibile.",
        "fr": "Merci pour votre demande. Un départ tardif jusqu'à {until} est possible pour vous.",
        "es": "Gracias por su solicitud. Puede hacer el check-out más tarde, hasta las {until}.",
        "nl": "Bedankt voor uw verzoek. Laat uitchecken tot {until} uur is voor u mogelijk.",
    },
    "late_checkout_not_possible": {
        "en": "Thank you for your request. Unfortunately a late check-out is not possible today. "
              "Check-out is until {until}.",
        "de": "Vielen Dank für Ihre Anfrage. Leider ist ein später Check-out heute nicht möglich. "
              "Der Check-out ist bis {until} Uhr möglich.",
        "it": "Grazie per la Sua richiesta. Purtroppo oggi il check-out posticipato non è possibile. "
              "Il check-out è entro le {until}.",
        "fr": "Merci pour votre demande. Malheureusement, un départ tardif n'est pas possible aujourd'hui. "
              "Le check-out est à effectuer avant {until}.",
        "es": "Gracias por su solicitud. Lamentablemente, hoy no es posible hacer el check-out más tarde. "
              "El check-out es hasta las {until}.",
        "nl": "Bedankt voor uw verzoek. Helaas is laat uitchecken vandaag niet mogelijk. "
              "Uitchecken kan tot {until} uur.",
    },
    "waiting_update": {
        "en": "We apologise for the wait. {Room} is being prepared right now and is expected to be ready "
              "at about {eta}.",
        "de": "Wir entschuldigen uns für die Wartezeit. {Room} wird gerade vorbereitet und ist voraussichtlich "
              "gegen {eta} Uhr bereit.",
        "it": "Ci scusiamo per l'attesa. {Room} è in preparazione e dovrebbe essere pronta verso le {eta}.",
        "fr": "Nous vous prions de nous excuser pour l'attente. {Room} est en cours de préparation et devrait "
              "être prête vers {eta}.",
        "es": "Disculpe la espera. {Room} se está preparando en este momento y estará lista previsiblemente "
              "hacia las {eta}.",
        "nl": "Excuses voor het wachten. {Room} wordt op dit moment klaargemaakt en is naar verwachting rond "
              "{eta} uur klaar.",
    },
    "maintenance_consent_request": {
        "en": "Our technician would need to enter {room} in the time window {window}{reason}. Would that suit "
              "you? We will only enter the room with your consent.",
        "de": "Unser Techniker müsste {room} im Zeitfenster {window} Uhr betreten{reason}. Wäre Ihnen das "
              "recht? Wir betreten das Zimmer nur mit Ihrer Zustimmung.",
        "it": "{Room} richiede un intervento del nostro tecnico nella fascia oraria {window}{reason}. Possiamo "
              "entrare in camera in questo orario? Entreremo solo con il Suo consenso.",
        "fr": "Notre technicien devrait intervenir {in_room} dans le créneau {window}{reason}. Ce créneau vous "
              "convient-il ? Nous n'entrerons dans la chambre qu'avec votre accord.",
        "es": "Nuestro técnico necesitaría entrar en {room} en la franja horaria {window}{reason}. ¿Le parece "
              "bien? Solo entraremos en la habitación con su consentimiento.",
        "nl": "Onze technicus zou {room} in het tijdvak {window} uur moeten betreden{reason}. Komt dat u uit? "
              "We betreden de kamer alleen met uw toestemming.",
    },
    "service_ack": {
        "en": "Thank you for your message ({req}). We have received your request and will take care of it.",
        "de": "Vielen Dank für Ihre Nachricht ({req}). Wir haben Ihre Anfrage erhalten und kümmern uns darum.",
        "it": "Grazie per il Suo messaggio ({req}). Abbiamo ricevuto la Sua richiesta e ce ne occuperemo.",
        "fr": "Merci pour votre message ({req}). Nous avons bien reçu votre demande et nous nous en occupons.",
        "es": "Gracias por su mensaje ({req}). Hemos recibido su solicitud y nos ocuparemos de ella.",
        "nl": "Bedankt voor uw bericht ({req}). We hebben uw verzoek ontvangen en gaan ermee aan de slag.",
    },
    "heating_update": {
        "en": "We are aware of the temperature issue {in_room} and are working on it. Thank you for your patience.",
        "de": "Wir wissen von dem Temperaturproblem {in_room} und arbeiten bereits daran. Vielen Dank für Ihre "
              "Geduld.",
        "it": "Siamo al corrente del problema di temperatura {in_room} e ce ne stiamo occupando. Grazie per la "
              "Sua pazienza.",
        "fr": "Nous sommes informés du problème de température {in_room} et nous y travaillons. Merci de votre "
              "patience.",
        "es": "Estamos al tanto del problema de temperatura {in_room} y ya estamos trabajando en ello. Gracias "
              "por su paciencia.",
        "nl": "We zijn op de hoogte van het temperatuurprobleem {in_room} en werken eraan. Bedankt voor uw geduld.",
    },
    "room_ready": {
        "en": "Good news: {room} is ready. You can now check in via self check-in.",
        "de": "Gute Nachricht: {room} ist bereit. Sie können jetzt über den Self-Check-in einchecken.",
        "it": "Buone notizie: {room} è pronta. Ora può effettuare il self check-in.",
        "fr": "Bonne nouvelle : {room} est prête. Vous pouvez dès maintenant utiliser le self check-in.",
        "es": "Buenas noticias: {room} ya está lista. Ahora puede hacer el self check-in.",
        "nl": "Goed nieuws: {room} is klaar. U kunt nu inchecken via de self-check-in.",
    },
    "arrival_delay_ack": {
        "en": "Thank you for letting us know. Self check-in is also available for late arrivals.",
        "de": "Vielen Dank für Ihre Nachricht. Der Self-Check-in steht Ihnen auch bei später Ankunft zur "
              "Verfügung.",
        "it": "Grazie per averci avvisato. Il self check-in è disponibile anche in caso di arrivo tardivo.",
        "fr": "Merci de nous avoir prévenus. Le self check-in est également disponible en cas d'arrivée tardive.",
        "es": "Gracias por avisarnos. El self check-in también está disponible si llega tarde.",
        "nl": "Bedankt voor het doorgeven. De self-check-in is ook bij late aankomst beschikbaar.",
    },
    "cleaning_scheduled": {
        "en": "Thank you for your request. Our housekeeping team will clean {room} at about {slot}. "
              "If you would prefer a different time, please let us know.",
        "de": "Vielen Dank für Ihre Anfrage. Unser Housekeeping-Team reinigt {room} gegen {slot} Uhr. "
              "Falls Ihnen eine andere Zeit lieber ist, geben Sie uns bitte Bescheid.",
        "it": "Grazie per la Sua richiesta. Il nostro servizio di pulizia passerà {in_room} verso le {slot}. "
              "Se preferisce un altro orario, ce lo faccia sapere.",
        "fr": "Merci pour votre demande. Notre équipe de ménage nettoiera {room} vers {slot}. "
              "Si vous préférez un autre horaire, n'hésitez pas à nous le dire.",
        "es": "Gracias por su solicitud. Nuestro equipo de limpieza limpiará {room} hacia las {slot}. "
              "Si prefiere otro horario, por favor indíquenoslo.",
        "nl": "Bedankt voor uw verzoek. Onze schoonmaakdienst maakt {room} rond {slot} uur schoon. "
              "Schikt een ander tijdstip u beter, laat het ons dan weten.",
    },
    "towels_delivery": {
        "en": "Thank you for your request. We will bring the towels to {room} at about {slot}.",
        "de": "Vielen Dank für Ihre Anfrage. Wir bringen die Handtücher gegen {slot} Uhr in {room}.",
        "it": "Grazie per la Sua richiesta. Porteremo gli asciugamani {in_room} verso le {slot}.",
        "fr": "Merci pour votre demande. Nous apporterons les serviettes {in_room} vers {slot}.",
        "es": "Gracias por su solicitud. Le llevaremos las toallas a {room} hacia las {slot}.",
        "nl": "Bedankt voor uw verzoek. We brengen de handdoeken rond {slot} uur naar {room}.",
    },
    "maintenance_scheduled": {
        "en": "Thank you for reporting this. Our technician will take care of the issue {in_room} at about {slot}.",
        "de": "Vielen Dank für Ihre Meldung. Unser Techniker kümmert sich gegen {slot} Uhr um das Problem "
              "{in_room}.",
        "it": "Grazie per la segnalazione. Il nostro tecnico si occuperà del problema {in_room} verso le {slot}.",
        "fr": "Merci pour votre signalement. Notre technicien s'occupera du problème {in_room} vers {slot}.",
        "es": "Gracias por avisarnos. Nuestro técnico se ocupará del problema {in_room} hacia las {slot}.",
        "nl": "Bedankt voor uw melding. Onze technicus pakt het probleem {in_room} rond {slot} uur aan.",
    },
    "access_issue_ack": {
        "en": "Thank you for letting us know. We are checking the access to {room} right away and will get back "
              "to you as soon as possible.",
        "de": "Vielen Dank für Ihre Nachricht. Wir prüfen sofort den Zugang zu {room_dat} und melden uns so "
              "schnell wie möglich bei Ihnen.",
        "it": "Grazie per la segnalazione. Stiamo verificando subito l'accesso per {room} e La ricontatteremo "
              "il prima possibile.",
        "fr": "Merci de nous avoir prévenus. Nous vérifions immédiatement l'accès à {room} et revenons vers vous "
              "dès que possible.",
        "es": "Gracias por avisarnos. Comprobamos de inmediato el acceso a {room} y le responderemos lo antes "
              "posible.",
        "nl": "Bedankt voor uw melding. We controleren direct de toegang tot {room} en nemen zo snel mogelijk "
              "contact met u op.",
    },
    "comfort_hot_update": {
        "en": "Thank you for letting us know that it is too warm {in_room}. We are checking the heating and "
              "ventilation settings and will get back to you.",
        "de": "Vielen Dank für den Hinweis, dass es {in_room} zu warm ist. Wir prüfen die Heizungs- und "
              "Lüftungseinstellungen und melden uns bei Ihnen.",
        "it": "Grazie per averci segnalato che fa troppo caldo {in_room}. Stiamo verificando le impostazioni di "
              "riscaldamento e ventilazione e La ricontatteremo.",
        "fr": "Merci de nous avoir signalé qu'il fait trop chaud {in_room}. Nous vérifions les réglages du "
              "chauffage et de la ventilation et revenons vers vous.",
        "es": "Gracias por avisarnos de que hace demasiado calor {in_room}. Estamos revisando la configuración "
              "de la calefacción y la ventilación y le informaremos.",
        "nl": "Bedankt voor uw melding dat het {in_room} te warm is. We controleren de instellingen van de "
              "verwarming en ventilatie en laten van ons horen.",
    },
    "air_quality_update": {
        "en": "Thank you for letting us know about the air quality {in_room}. We are checking the ventilation "
              "and will get back to you.",
        "de": "Vielen Dank für Ihren Hinweis zur Luftqualität {in_room}. Wir prüfen die Lüftung und melden uns "
              "bei Ihnen.",
        "it": "Grazie per la segnalazione sulla qualità dell'aria {in_room}. Stiamo verificando la ventilazione "
              "e La ricontatteremo.",
        "fr": "Merci pour votre signalement concernant la qualité de l'air {in_room}. Nous vérifions la "
              "ventilation et revenons vers vous.",
        "es": "Gracias por informarnos sobre la calidad del aire {in_room}. Estamos revisando la ventilación y "
              "le informaremos.",
        "nl": "Bedankt voor uw melding over de luchtkwaliteit {in_room}. We controleren de ventilatie en laten "
              "van ons horen.",
    },
    "noise_ack": {
        "en": "Thank you for letting us know, and we are sorry for the disturbance. We are looking into the "
              "noise and will take care of it.",
        "de": "Vielen Dank für Ihre Nachricht, und entschuldigen Sie die Störung. Wir gehen der "
              "Lärmbelästigung nach und kümmern uns darum.",
        "it": "Grazie per la segnalazione e ci scusiamo per il disturbo. Stiamo verificando la fonte del "
              "rumore e ce ne occuperemo.",
        "fr": "Merci de nous avoir prévenus, et veuillez nous excuser pour ce désagrément. Nous examinons la "
              "source du bruit et nous nous en occupons.",
        "es": "Gracias por avisarnos y disculpe las molestias. Estamos investigando el ruido y nos ocuparemos "
              "de ello.",
        "nl": "Bedankt voor uw melding en excuses voor de overlast. We zoeken uit waar het geluid vandaan komt "
              "en pakken het aan.",
    },
}

# optional sentences appended when the fact is given: kind -> fact -> language -> text
OPTIONAL_CLAUSES = {
    "maintenance_consent_request": {"reply_by_local": {
        "en": "Please reply by {reply_by} if possible.",
        "de": "Bitte antworten Sie nach Möglichkeit bis {reply_by} Uhr.",
        "it": "La preghiamo di risponderci, se possibile, entro le {reply_by}.",
        "fr": "Merci de nous répondre si possible avant {reply_by}.",
        "es": "Le rogamos que nos responda, si es posible, antes de las {reply_by}.",
        "nl": "Wilt u indien mogelijk vóór {reply_by} uur reageren?",
    }},
    "service_ack": {"slot_local": {
        "en": "Our team will take care of it from about {slot}.",
        "de": "Unser Team kümmert sich ab ca. {slot} Uhr darum.",
        "it": "Il nostro team se ne occuperà a partire dalle {slot} circa.",
        "fr": "Notre équipe s'en occupera à partir d'environ {slot}.",
        "es": "Nuestro equipo se ocupará a partir de las {slot} aproximadamente.",
        "nl": "Ons team pakt dit vanaf ongeveer {slot} uur op.",
    }},
    "arrival_delay_ack": {"arrival_local": {
        "en": "We have noted your expected arrival at about {arrival}.",
        "de": "Wir haben Ihre voraussichtliche Ankunft gegen {arrival} Uhr notiert.",
        "it": "Abbiamo preso nota del Suo arrivo previsto verso le {arrival}.",
        "fr": "Nous avons noté votre arrivée prévue vers {arrival}.",
        "es": "Hemos tomado nota de su llegada prevista hacia las {arrival}.",
        "nl": "We hebben uw verwachte aankomst rond {arrival} uur genoteerd.",
    }},
}

# phrases that would imply a delivery/confirmation we cannot prove (checked in tests + LLM guard)
DELIVERY_PHRASES = ("we sent", "we have sent", "has been sent", "was sent", "delivered", "gesendet",
                    "geschickt", "zugestellt", "inviato", "consegnato", "envoyé", "livré", "enviado",
                    "entregado", "verzonden", "verstuurd", "afgeleverd")


# --- helpers -------------------------------------------------------------------------
def to_local_hm(value) -> str | None:
    """'HH:MM' local (Europe/Vienna, DST-aware) from 'H:MM'/'HH:MM' strings, datetime.time,
    or timestamps (naive timestamps are treated as UTC, like all dataset timestamps)."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, _dt.time):
        return f"{value.hour:02d}:{value.minute:02d}"
    if isinstance(value, str):
        s = value.strip()
        m = re.fullmatch(r"([01]?\d|2[0-3])[:.h]([0-5]\d)", s)
        if m:
            return f"{int(m.group(1)):02d}:{m.group(2)}"
        if not re.search(r"\d{4}-\d{2}-\d{2}", s):
            return None
    try:
        ts = pd.Timestamp(value)
    except (ValueError, TypeError):
        return None
    if pd.isna(ts):
        return None
    return local(ts).strftime("%H:%M")


def _window(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, (tuple, list)) and len(value) == 2:
        a, b = to_local_hm(value[0]), to_local_hm(value[1])
        return f"{a}–{b}" if a and b else None
    s = str(value).strip()
    parts = re.split(r"\s*(?:–|—|-|bis|to)\s*", s)
    if len(parts) == 2:
        a, b = to_local_hm(parts[0]), to_local_hm(parts[1])
        if a and b:
            return f"{a}–{b}"
    return s or None


def _label(table: dict, key, lang: str, aliases: dict | None = None) -> str | None:
    if key is None or str(key).strip() == "":
        return None
    k = str(key).strip()
    k = (aliases or {}).get(k, k)
    row = table.get(k)
    return row[_LANG_IDX[lang]] if row else k


def _present(v) -> bool:
    return v is not None and not (isinstance(v, float) and pd.isna(v)) and str(v).strip() != ""


def _slots(lang: str, facts: dict) -> tuple[dict, list[str]]:
    """Render fact values into template slots; returns (slots, invalid_fact_names)."""
    bad: list[str] = []
    room_id = facts.get("room_id")
    rid = f" {str(room_id).strip()}" if _present(room_id) else ""
    room, Room, in_room, room_dat = (p.format(id=rid) for p in _ROOM[lang])
    s = {"room": room, "Room": Room, "in_room": in_room, "room_dat": room_dat}
    for slot, fact, default in (("eta", "eta_local", None), ("earliest", "earliest_local", DEFAULT_CHECK_IN_FROM),
                                ("until", "until_local", None), ("slot", "slot_local", None),
                                ("arrival", "arrival_local", None), ("reply_by", "reply_by_local", None)):
        raw = facts.get(fact)
        val = to_local_hm(raw) if _present(raw) else default
        if _present(raw) and val is None:
            bad.append(fact)
        s[slot] = val
    s["window"] = _window(facts.get("proposed_window_local"))
    reason = _label(_REASONS, facts.get("reason"), lang, _REASON_ALIASES)
    s["reason"] = f" ({reason})" if reason else ""
    s["req"] = _label(_REQUEST_LABELS, facts.get("request_type") or "_generic", lang)
    return s, bad


_LLM_SYSTEM = ("You polish short hotel guest messages for the fictional self-check-in hotel PLATO-40. "
               "Rewrite the message naturally and politely in the requested language using the formal "
               "register (German Sie, French vous, Italian Lei, Spanish usted, Dutch u). Keep every number, "
               "time and room id exactly as given, do not add facts, promises, prices or apologies that are "
               "not in the original, and never claim that anything was sent or delivered. Answer with the "
               "message text only.")


def _digits(text: str) -> list[str]:
    return sorted(re.findall(r"\d+", text))


def facts_preserved(template_text: str, candidate: str) -> bool:
    """Guard for LLM output: identical digits (times, room ids), no slots, no delivery claims."""
    if not candidate or "{" in candidate or "}" in candidate:
        return False
    if len(candidate) > 2 * len(template_text) + 80:
        return False
    if _digits(candidate) != _digits(template_text):
        return False
    low = candidate.lower()
    return not any(p in low for p in DELIVERY_PHRASES)


def compose_guest_message(kind: str, language: str | None, **facts) -> dict:
    """Return {'language', 'text', 'source', 'kind', 'requested_language'} - source 'template'|'llm'.

    Unsupported/missing language -> English (``requested_language`` keeps the original).
    Unknown kind -> ``service_ack``. Missing/invalid required facts -> neutral fallback text
    (no invented facts) and ``missing_facts`` lists them.
    """
    req_lang = (str(language).strip().lower()[:2] if _present(language) else "")
    lang = req_lang if req_lang in SUPPORTED_LANGUAGES else "en"
    kind = kind if kind in TEMPLATES else "service_ack"
    slots, bad = _slots(lang, facts)
    if kind == "late_checkout_not_possible" and slots["until"] is None and "until_local" not in bad:
        slots["until"] = DEFAULT_CHECK_OUT_UNTIL
    slot_of = {"eta_local": "eta", "until_local": "until", "slot_local": "slot",
               "proposed_window_local": "window"}
    missing = [f for f in REQUIRED_FACTS[kind]
               if f in bad or (f == "room_id" and not _present(facts.get("room_id")))
               or (f in slot_of and not slots[slot_of[f]])]
    if missing:
        body = _FALLBACK[lang]
    else:
        body = TEMPLATES[kind][lang].format(**slots)
        for fact, clause in OPTIONAL_CLAUSES.get(kind, {}).items():
            slot = {"reply_by_local": "reply_by", "slot_local": "slot", "arrival_local": "arrival"}[fact]
            if slots.get(slot):
                body += " " + clause[lang].format(**slots)
    text = f"{body}\n{_SIGNOFF[lang]}"
    out = {"language": lang, "text": text, "source": "template", "kind": kind,
           "requested_language": req_lang or None}
    if missing:
        out["missing_facts"] = missing

    if not missing and llm.enabled():
        prompt = f"Language: {LANGUAGE_NAMES[lang]}\nMessage:\n{body}"
        cand = llm.complete(_LLM_SYSTEM, prompt)
        if cand and facts_preserved(body, cand.strip()):
            out["text"] = f"{cand.strip()}\n{_SIGNOFF[lang]}"
            out["source"] = "llm"
    return out


# --- requested-time parsing -----------------------------------------------------------
# explicit clock times: 14:30, 13.00, 14h15, 14 h 15, 2:30 pm
_HM = re.compile(r"(?<![\d.:,])([01]?\d|2[0-3])\s?(?::|\.|h\s?)([0-5]\d)(?![\d]|[.,]\d)"
                 r"(?:\s?([ap])\.?\s?m\b\.?)?", re.I)
# hour with unit: 14 Uhr, 14h, 2 pm, 14 uur, 9 o'clock
_H_UNIT = re.compile(r"(?<![\d.:,])([01]?\d|2[0-3])\s?(uhr\b|uur\b|h\b|o'?clock\b|([ap])\.?\s?m\b\.?)", re.I)
_MIDNIGHT = re.compile(r"\b(midnight|mitternacht|minuit|mezzanotte|medianoche|middernacht)\b", re.I)
_NOON = re.compile(r"\b(noon|midday|mittag|midi|mezzogiorno|mediod[ií]a)\b", re.I)
# bare hour after a time preposition: "um 14", "alle 13", "vers 15", "a las 12", "om 13", "at 2"
_PREP_H = re.compile(r"(?:\b(?:um|gegen|ab|bis|at|around|by|until|till|after|before|from|alle|verso le|"
                     r"entro le|dalle|fino alle|vers|avant|jusqu'à|a las|sobre las|hacia las|hasta las|"
                     r"om|rond|tot|vanaf|na)|(?<!\w)à)\s+([01]?\d|2[0-3])(?![\d:.,]|\s?(?:%|€|eur|min|"
                     r"personen|people|guests|gäste|nights|nächte|nachten|tage|days))", re.I)


def _ampm(h: int, ap: str | None) -> int:
    if not ap:
        return h
    ap = ap.lower()
    if ap == "a":
        return 0 if h == 12 else h
    return h if h == 12 else (h + 12 if h < 12 else h)


def parse_requested_time(text: str) -> str | None:
    """Extract the (first) requested clock time 'HH:MM' from free guest text, or None.

    Handles 'gegen 14:30', 'bis 12:00', 'at 12:45', 'vers 14h15', 'alle 13:00', 'om 13.00',
    '14 Uhr', '2:30 pm', '3pm', 'nach Mitternacht' (-> '00:00'), 'noon' (-> '12:00') and
    'um 14'. Dates such as '12.03.2026' are not mistaken for times. Returned times are the
    guest's local clock times (no timezone conversion).
    """
    if not isinstance(text, str) or not text.strip():
        return None
    cands: list[tuple[int, int, int]] = []
    for m in _HM.finditer(text):
        cands.append((m.start(), _ampm(int(m.group(1)), m.group(3)), int(m.group(2))))
    explicit = {c[0] for c in cands}
    for m in _H_UNIT.finditer(text):
        if m.start() in explicit:          # "14 h 15" already parsed as 14:15
            continue
        h = int(m.group(1))
        ap = m.group(3)
        if ap and h > 12:
            continue
        cands.append((m.start(), _ampm(h, ap), 0))
    if not cands:
        for m in _MIDNIGHT.finditer(text):
            cands.append((m.start(), 0, 0))
        for m in _NOON.finditer(text):
            cands.append((m.start(), 12, 0))
    if not cands:
        for m in _PREP_H.finditer(text):
            cands.append((m.start(1), int(m.group(1)), 0))
    if not cands:
        return None
    _, h, mi = min(cands)
    if not (0 <= h <= 23):
        return None
    return f"{h:02d}:{mi:02d}"


def approval_note(decision_reason: str, evidence: list[str], proposal: str, if_no_action: str) -> str:
    """Short, structured note for the human approver (English, deterministic).

    Four lines: why, proposal, evidence (first 3 + count), consequence of no action.
    """
    def clean(s, n=300):
        s = re.sub(r"\s+", " ", str(s or "")).strip()
        return s if len(s) <= n else s[: n - 1].rstrip() + "…"

    ev = [clean(e, 120) for e in (evidence or []) if _present(e)]
    ev_txt = "; ".join(ev[:3]) + (f" (+{len(ev) - 3} more)" if len(ev) > 3 else "") if ev else "none given"
    return (f"Why: {clean(decision_reason) or 'n/a'}\n"
            f"Proposal: {clean(proposal) or 'n/a'}\n"
            f"Evidence: {ev_txt}\n"
            f"If no action: {clean(if_no_action) or 'n/a'}")
