#!/bin/bash
# Optionale Push-Meldungen ueber ntfy.sh, genutzt von backup_db.sh.
# Wird per "source" eingebunden, nicht selbst ausgefuehrt.
#
# Das Topic steht in /etc/himbeerepi/himbeerepi.env:
#     NTFY_TOPIC=<zufaelliger Name>
# ntfy.sh-Kanaele sind oeffentlich - wer den Namen kennt, kann mitlesen. Er wirkt wie ein
# Passwort: lang und zufaellig waehlen, z.B.  openssl rand -hex 16
# Ohne NTFY_TOPIC wird nichts verschickt (nur ein Eintrag im Systemlog).
# Eigener ntfy-Server: NTFY_SERVER=https://ntfy.example.org

HIMBEEREPI_ENV_FILE="${HIMBEEREPI_ENV_FILE:-/etc/himbeerepi/himbeerepi.env}"
NTFY_TOPIC=""
NTFY_SERVER="https://ntfy.sh"
if [ -r "$HIMBEEREPI_ENV_FILE" ]; then
    # Nur die benoetigten Zeilen lesen statt die Datei per "source" auszufuehren
    NTFY_TOPIC="$(sed -n 's/^NTFY_TOPIC=//p' "$HIMBEEREPI_ENV_FILE" | head -n 1 | tr -d '[:space:]"'"'")"
    _server="$(sed -n 's/^NTFY_SERVER=//p' "$HIMBEEREPI_ENV_FILE" | head -n 1 | tr -d '[:space:]"'"'")"
    [ -n "$_server" ] && NTFY_SERVER="${_server%/}"
fi

# notify_backup <Titel> <Nachricht> <Prioritaet: default|high> <Tags, kommagetrennt>
notify_backup() {
    local title="$1"
    local message="$2"
    local priority="$3"
    local tags="$4"

    if [ -z "$NTFY_TOPIC" ]; then
        logger -t himbeerepi "Kein NTFY_TOPIC gesetzt - Meldung nicht verschickt: $title"
        return 0
    fi

    # -f: HTTP-Fehler zaehlen als Fehlschlag; --retry: kurze Netz-/Serverausfaelle ueberbruecken.
    if ! curl -sf --max-time 20 --retry 3 --retry-delay 10 --retry-all-errors \
        -H "Title: $title" \
        -H "Priority: $priority" \
        -H "Tags: $tags" \
        -d "$message" \
        "$NTFY_SERVER/$NTFY_TOPIC" > /dev/null 2>&1; then
        logger -t himbeerepi "Versand an ntfy fehlgeschlagen (nach 4 Versuchen): $title"
        return 1
    fi
}
