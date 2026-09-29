#!/bin/bash
# Woechentliche Spiegelung aller hochgeladenen Dateien auf die Backup-Festplatte (Ausbau 2).
# Laeuft per Cron (/etc/cron.d/himbeerepi-mirror, angelegt vom Einrichtungsassistenten) als root:
#   <HIMBEEREPI_DATA_DIR>/  ->  <HIMBEEREPI_BACKUP_MOUNT>/dateien/
#
# Die Spiegelung loescht auf der Backup-Platte alles, was im Datenordner fehlt (rsync --delete).
# Waere die Speicher-Platte nicht eingehaengt (Kabel lose, Platte defekt), laege unter dem
# Datenordner nur ein leeres Verzeichnis - und die Sicherung wuerde komplett geleert. Deshalb
# wird vorher geprueft und im Zweifel abgebrochen (mit Push-Meldung, falls ntfy eingerichtet ist):
#   - die Backup-Platte ist eingehaengt
#   - steht der Datenordner in /etc/fstab (Ausbau 1), ist die Speicher-Platte eingehaengt
#   - Dateien, die laut Datenbank vorhanden sein muessen, liegen wirklich im Datenordner

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/notify_ntfy.sh"

ENV_FILE="${HIMBEEREPI_ENV_FILE:-/etc/himbeerepi/himbeerepi.env}"
FSTAB="${HIMBEEREPI_FSTAB:-/etc/fstab}"
read_env() { [ -r "$ENV_FILE" ] && sed -n "s/^$1=//p" "$ENV_FILE" | head -n 1 | tr -d '"'"'"; }

DB_PATH="$(read_env HIMBEEREPI_DB)";        DB_PATH="${DB_PATH:-/var/lib/himbeerepi/users.db}"
DATA_DIR="$(read_env HIMBEEREPI_DATA_DIR)"; DATA_DIR="${DATA_DIR:-/srv/himbeerepi}"
BACKUP_MOUNT="$(read_env HIMBEEREPI_BACKUP_MOUNT)"

abort() {
    echo "$(date): ABGEBROCHEN - $1"
    notify_backup "HimbeerePi: Spiegelung abgebrochen" \
        "$1 Die Sicherung auf der Backup-Platte bleibt unverändert." "high" "warning" || true
    exit 1
}

echo "$(date): Starte Spiegelung $DATA_DIR -> ${BACKUP_MOUNT:-?}/dateien"

[ -n "$BACKUP_MOUNT" ] || abort "Keine Backup-Festplatte eingerichtet (HIMBEEREPI_BACKUP_MOUNT fehlt)."
mountpoint -q "$BACKUP_MOUNT" || abort "Die Backup-Festplatte ist nicht eingehängt ($BACKUP_MOUNT)."

if awk -v d="$DATA_DIR" '$1 !~ /^#/ && $2 == d { found = 1 } END { exit !found }' "$FSTAB" 2>/dev/null \
        && ! mountpoint -q "$DATA_DIR"; then
    abort "Die Speicher-Festplatte ist nicht eingehängt ($DATA_DIR)."
fi

# Stichprobe: bis zu 50 Dateien aus der Datenbank muessen im Datenordner liegen. Fehlen alle,
# stimmt etwas mit dem Datenordner nicht (falsche Platte, leeres Verzeichnis).
if [ -r "$DB_PATH" ]; then
    sample="$(sqlite3 "$DB_PATH" "SELECT filename FROM files ORDER BY RANDOM() LIMIT 50;" 2>/dev/null)" \
        || abort "Die Datenbank $DB_PATH ist nicht lesbar."
    if [ -n "$sample" ]; then
        present=0
        while IFS= read -r name; do
            [ -f "$DATA_DIR/$name" ] && present=$((present + 1))
        done <<< "$sample"
        [ "$present" -gt 0 ] || abort "Im Datenordner $DATA_DIR fehlen alle geprüften Dateien aus der Datenbank."
    fi
fi

mkdir -p "$BACKUP_MOUNT/dateien"
if rsync -a --delete --exclude=/lost+found --exclude=/tmp_uploads "$DATA_DIR/" "$BACKUP_MOUNT/dateien/"; then
    echo "$(date): Spiegelung erfolgreich abgeschlossen"
else
    code=$?
    echo "$(date): FEHLGESCHLAGEN - rsync Exit $code"
    notify_backup "HimbeerePi: Spiegelung FEHLGESCHLAGEN" \
        "Die wöchentliche Spiegelung auf die Backup-Platte ist fehlgeschlagen (rsync Exit $code). Log: /var/log/himbeerepi-mirror.log" \
        "high" "rotating_light,warning" || true
    exit 1
fi
