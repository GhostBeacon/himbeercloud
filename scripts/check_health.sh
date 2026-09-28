#!/bin/bash
# Prueft alle 5 Minuten (per Cron, als root), ob die Cloud noch antwortet.
# Startet den Dienst nur neu, wenn ZWEI Versuche direkt hintereinander fehlschlagen (mit 15
# Sekunden Abstand) - eine einzelne kurze Verlangsamung (z.B. waehrend eines grossen Uploads)
# soll keinen laufenden Upload abbrechen.

LOG_FILE="/var/log/himbeerepi-health.log"

check_once() {
    curl -4 -s -o /dev/null -w "%{http_code}" --max-time 10 http://127.0.0.1:5000/login
}

RESPONSE1=$(check_once)

if [ "$RESPONSE1" != "200" ]; then
    sleep 15
    RESPONSE2=$(check_once)

    if [ "$RESPONSE2" != "200" ]; then
        echo "$(date): Cloud antwortet zweimal in Folge nicht (Code: $RESPONSE1 / $RESPONSE2) - starte himbeerepi.service neu." >> "$LOG_FILE"
        systemctl restart himbeerepi
    else
        echo "$(date): Kurzzeitige Verlangsamung (Code: $RESPONSE1), zweiter Versuch erfolgreich - kein Neustart." >> "$LOG_FILE"
    fi
fi
