#!/bin/bash
# HimbeerePi - Einrichtung bzw. Aktualisierung auf einem Raspberry Pi (Raspberry Pi OS / Debian).
#
#   sudo git clone https://github.com/GhostBeacon/himbeercloud.git /opt/himbeerepi
#   sudo /opt/himbeerepi/deploy/install.sh
#
# Mehrfach ausfuehrbar: vorhandene Einstellungen und Daten bleiben erhalten. Nach einem
# "git pull" einfach erneut starten, um Updates einzuspielen.
# Gefuehrte Einrichtung mit Menues (ruft dieses Skript selbst auf): deploy/setup.sh
set -euo pipefail

INSTALL_DIR="/opt/himbeerepi"
ENV_DIR="/etc/himbeerepi"
ENV_FILE="$ENV_DIR/himbeerepi.env"
STATE_DIR="/var/lib/himbeerepi"
SERVICE_USER="himbeerepi"

if [ "$(id -u)" -ne 0 ]; then
    echo "Bitte mit sudo ausfuehren." >&2
    exit 1
fi

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
if [ "$REPO_DIR" != "$INSTALL_DIR" ]; then
    echo "Das Repo muss unter $INSTALL_DIR liegen (gefunden: $REPO_DIR)." >&2
    echo "  sudo git clone https://github.com/GhostBeacon/himbeercloud.git $INSTALL_DIR" >&2
    exit 1
fi

echo "==> Pakete"
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-dev sqlite3 curl \
    poppler-utils libimage-exiftool-perl dcraw >/dev/null

echo "==> Dienstbenutzer $SERVICE_USER"
if ! id "$SERVICE_USER" >/dev/null 2>&1; then
    useradd --system --home-dir "$STATE_DIR" --shell /usr/sbin/nologin "$SERVICE_USER"
fi
usermod -aG video "$SERVICE_USER" || true

echo "==> Einstellungen $ENV_FILE"
install -d -m 750 -o root -g "$SERVICE_USER" "$ENV_DIR"
if [ ! -f "$ENV_FILE" ]; then
    install -m 640 -o root -g "$SERVICE_USER" "$INSTALL_DIR/deploy/himbeerepi.env.example" "$ENV_FILE"
    KEY="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
    sed -i "s/^SECRET_KEY=.*/SECRET_KEY=$KEY/" "$ENV_FILE"
    echo "    neu angelegt, SECRET_KEY erzeugt"
fi
# Werte fuer dieses Skript lesen (ohne die Datei auszufuehren)
DATA_DIR="$(sed -n 's/^HIMBEEREPI_DATA_DIR=//p' "$ENV_FILE" | head -n 1)"
DATA_DIR="${DATA_DIR:-/srv/himbeerepi}"
DB_PATH="$(sed -n 's/^HIMBEEREPI_DB=//p' "$ENV_FILE" | head -n 1)"
DB_PATH="${DB_PATH:-$STATE_DIR/users.db}"

echo "==> Ordner"
install -d -m 750 -o "$SERVICE_USER" -g "$SERVICE_USER" "$STATE_DIR" "$(dirname "$DB_PATH")"
install -d -m 750 -o "$SERVICE_USER" -g "$SERVICE_USER" "$DATA_DIR" "$DATA_DIR/tmp_uploads" "$DATA_DIR/thumbnails" "$DATA_DIR/backups"

echo "==> Programmdateien (gehoeren root, der Dienst darf sie nur lesen)"
chown -R root:root "$INSTALL_DIR"
chmod -R go-w "$INSTALL_DIR"
chmod +x "$INSTALL_DIR"/scripts/*.sh "$INSTALL_DIR"/deploy/install.sh

echo "==> Python-Umgebung"
if [ ! -x "$INSTALL_DIR/venv/bin/python3" ]; then
    python3 -m venv "$INSTALL_DIR/venv"
fi
"$INSTALL_DIR/venv/bin/pip" install -q --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install -q -r "$INSTALL_DIR/app/requirements.txt"

echo "==> Datenbank"
sudo -u "$SERVICE_USER" env HIMBEEREPI_DB="$DB_PATH" "$INSTALL_DIR/venv/bin/python3" "$INSTALL_DIR/app/manage.py" init

echo "==> systemd und Cron"
install -m 644 "$INSTALL_DIR/deploy/himbeerepi.service" /etc/systemd/system/himbeerepi.service
if [ "$DATA_DIR" != "/srv/himbeerepi" ]; then
    sed -i "s#/srv/himbeerepi#$DATA_DIR#g" /etc/systemd/system/himbeerepi.service
fi
install -m 644 "$INSTALL_DIR/deploy/himbeerepi.cron" /etc/cron.d/himbeerepi
# Aeltere Spiegelung (rsync direkt im Cronjob, ohne Pruefung der Speicher-Platte) auf das Skript umstellen
if [ -f /etc/cron.d/himbeerepi-mirror ] && ! grep -q "mirror_files.sh" /etc/cron.d/himbeerepi-mirror; then
    printf '# HimbeerePi: woechentliche Spiegelung aller Dateien (angelegt vom Einrichtungsassistenten)\n0 2 * * 0 root %s/scripts/mirror_files.sh >> /var/log/himbeerepi-mirror.log 2>&1\n' \
        "$INSTALL_DIR" > /etc/cron.d/himbeerepi-mirror
    echo "    Spiegelung auf scripts/mirror_files.sh umgestellt"
fi
systemctl daemon-reload
systemctl enable himbeerepi >/dev/null 2>&1
systemctl restart himbeerepi

sleep 3
CODE="$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:5000/login || true)"
if [ "$CODE" = "200" ]; then
    echo "==> Fertig: HimbeerePi laeuft auf 127.0.0.1:5000"
else
    echo "==> Dienst antwortet nicht (HTTP $CODE). Log: journalctl -u himbeerepi -n 50" >&2
    exit 1
fi

if ! sudo -u "$SERVICE_USER" sqlite3 "$DB_PATH" "SELECT 1 FROM users LIMIT 1" 2>/dev/null | grep -q 1; then
    echo
    echo "Noch kein Benutzer vorhanden. Ersten Benutzer anlegen:"
    echo "  cd $INSTALL_DIR/app && sudo -u $SERVICE_USER ../venv/bin/python3 manage.py add-user"
fi
echo
echo "Festplatte, Benutzer, Zugang von aussen (HTTPS), Firewall und Backups bequem einrichten:"
echo "  sudo $INSTALL_DIR/deploy/setup.sh"
