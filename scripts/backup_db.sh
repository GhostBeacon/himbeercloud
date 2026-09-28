#!/bin/bash
# Taegliches WAL-sicheres Backup der Cloud-Datenbank (Benutzer, Ordner, Dateiliste).
# Laeuft per Cron (deploy/raspicloud.cron) als Benutzer raspicloud. Ziel:
#   <RASPICLOUD_DATA_DIR>/backups             immer
#   <RASPICLOUD_BACKUP_MOUNT>/db_backups      zusaetzlich mit Backup-Festplatte (Ausbau 2)
# Sicherungen aelter als 14 Tage werden geloescht.
#
# Es wird nicht nur geprueft, ob "sqlite3 .backup" fehlerfrei durchlaeuft, sondern zusaetzlich,
# ob die ENTSTANDENE Kopie selbst eine gueltige Datenbank ist (PRAGMA integrity_check).
#
# Hinweis: Gesichert wird nur die Datenbank. Die hochgeladenen Dateien spiegelt bei Ausbau 2
# ein eigener woechentlicher Cronjob (/etc/cron.d/raspicloud-mirror) auf die Backup-Festplatte.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/notify_ntfy.sh"

ENV_FILE="${RASPICLOUD_ENV_FILE:-/etc/raspicloud/raspicloud.env}"
read_env() { [ -r "$ENV_FILE" ] && sed -n "s/^$1=//p" "$ENV_FILE" | head -n 1 | tr -d '"'"'"; }

DB_PATH="$(read_env RASPICLOUD_DB)";                DB_PATH="${DB_PATH:-/var/lib/raspicloud/users.db}"
DATA_DIR="$(read_env RASPICLOUD_DATA_DIR)";         DATA_DIR="${DATA_DIR:-/srv/raspicloud}"
BACKUP_MOUNT="$(read_env RASPICLOUD_BACKUP_MOUNT)"

BACKUP_DIR_PRIMARY="$DATA_DIR/backups"
LOG_FILE="$BACKUP_DIR_PRIMARY/backup_db.log"
TIMESTAMP=$(date +%Y-%m-%d_%H-%M)
BACKUP_FILENAME="users_db_${TIMESTAMP}.db"
BACKUP_FULL_PATH="$BACKUP_DIR_PRIMARY/$BACKUP_FILENAME"

mkdir -p "$BACKUP_DIR_PRIMARY"
echo "$(date): Starte taegliches DB-Backup..." >> "$LOG_FILE"

BACKUP_OK=false
FAILURE_REASON=""

if sqlite3 "$DB_PATH" ".backup '$BACKUP_FULL_PATH'" 2>>"$LOG_FILE"; then
    INTEGRITY_RESULT=$(sqlite3 "$BACKUP_FULL_PATH" "PRAGMA integrity_check;" 2>&1)
    if [ "$INTEGRITY_RESULT" = "ok" ]; then
        BACKUP_OK=true
    else
        FAILURE_REASON="Integritätsprüfung fehlgeschlagen: $INTEGRITY_RESULT"
    fi
else
    FAILURE_REASON="sqlite3 .backup-Befehl selbst ist fehlgeschlagen"
fi

if [ "$BACKUP_OK" = true ]; then
    SECONDARY_NOTE=""
    if [ -n "$BACKUP_MOUNT" ]; then
        if mountpoint -q "$BACKUP_MOUNT"; then
            mkdir -p "$BACKUP_MOUNT/db_backups" \
                && cp "$BACKUP_FULL_PATH" "$BACKUP_MOUNT/db_backups/$BACKUP_FILENAME" \
                && find "$BACKUP_MOUNT/db_backups" -maxdepth 1 -name "users_db_*.db" -mtime +14 -delete \
                || SECONDARY_NOTE=" (Kopie auf die Backup-Festplatte fehlgeschlagen)"
        else
            SECONDARY_NOTE=" (Backup-Festplatte war nicht eingehaengt - nur im Datenordner gesichert)"
        fi
    fi

    find "$BACKUP_DIR_PRIMARY" -maxdepth 1 -name "users_db_*.db" -mtime +14 -delete
    find "$BACKUP_DIR_PRIMARY" -maxdepth 1 -name "*.failed" -mtime +7 -delete

    echo "$(date): Backup erfolgreich abgeschlossen, Integritaet geprueft.${SECONDARY_NOTE}" >> "$LOG_FILE"
    notify_backup "RaspiCloud: Datenbank-Backup OK" \
        "Tägliches DB-Backup erfolgreich, Integrität geprüft.${SECONDARY_NOTE}" \
        "default" "white_check_mark"
else
    echo "$(date): FEHLGESCHLAGEN - $FAILURE_REASON" >> "$LOG_FILE"
    notify_backup "RaspiCloud: Datenbank-Backup FEHLGESCHLAGEN" \
        "Das tägliche DB-Backup ist fehlgeschlagen: ${FAILURE_REASON}. Log prüfen: $LOG_FILE" \
        "high" "rotating_light,warning"
    # Fehlerhafte Kopie zur Fehlersuche behalten, aber nicht mit gueltigen Sicherungen verwechseln
    if [ -f "$BACKUP_FULL_PATH" ]; then
        mv "$BACKUP_FULL_PATH" "${BACKUP_FULL_PATH}.failed"
    fi
    exit 1
fi
