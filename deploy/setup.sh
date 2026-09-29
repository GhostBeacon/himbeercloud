#!/bin/bash
# HimbeerePi - Einrichtungsassistent
#
#   sudo /opt/himbeerepi/deploy/setup.sh
#
# Fuehrt mit Menues durch die Einrichtung, in drei Stufen:
#   Grundeinrichtung  ohne externe Festplatte - Dateien liegen auf der SD-Karte (/srv/himbeerepi):
#                     Installation, Benutzer, Zugang (Domain mit HTTPS oder Heimnetz), Firewall
#   Ausbau 1          Festplatte als Speicher - die vorhandenen Dateien werden auf die Platte
#                     kopiert, geprueft, und die Platte wird unter /srv/himbeerepi eingehaengt
#   Ausbau 2          zweite Festplatte als Backup (/srv/himbeerepi-backup) mit taeglicher
#                     Datenbank-Kopie und woechentlicher Spiegelung aller Dateien
# Jeder Schritt laesst sich einzeln wiederholen; der Assistent kann jederzeit erneut gestartet
# werden, um etwas zu aendern. Protokoll: /var/log/himbeerepi-setup.log
#
# Die Menues nutzen whiptail (auf Raspberry Pi OS vorinstalliert).

set -uo pipefail

INSTALL_DIR="${HIMBEEREPI_INSTALL_DIR:-/opt/himbeerepi}"
ENV_FILE="${HIMBEEREPI_ENV_FILE:-/etc/himbeerepi/himbeerepi.env}"
FSTAB="${HIMBEEREPI_FSTAB:-/etc/fstab}"
CADDYFILE="${HIMBEEREPI_CADDYFILE:-/etc/caddy/Caddyfile}"
MIRROR_CRON="${HIMBEEREPI_MIRROR_CRON:-/etc/cron.d/himbeerepi-mirror}"
LOG="${HIMBEEREPI_SETUP_LOG:-/var/log/himbeerepi-setup.log}"
SERVICE_USER="himbeerepi"
DATA_DIR_DEFAULT="/srv/himbeerepi"
BACKUP_MOUNT="/srv/himbeerepi-backup"
MIGRATE_MOUNT="/mnt/himbeerepi-umzug"
HDD_WATTS_EACH=7
CADDY_MARKER="# Verwaltet vom HimbeerePi-Einrichtungsassistenten"
TITLE="HimbeerePi – Einrichtung"

# ---------------------------------------------------------------- Dialoge

# whiptail schreibt die Antwort auf stderr - hier auf stdout umgelenkt, damit $(...) sie liest
wt() {
    whiptail --title "$TITLE" --backtitle "HimbeerePi $(cat "$INSTALL_DIR/VERSION" 2>/dev/null)" "$@" 3>&1 1>&2 2>&3
}
msg()      { wt --msgbox "$1" "${2:-16}" 76; }
ask()      { wt --yesno "$1" "${2:-14}" 76; }
ask_no()   { wt --defaultno --yesno "$1" "${2:-14}" 76; }
input()    { wt --inputbox "$1" "${3:-12}" 76 "${2:-}"; }
password() { wt --passwordbox "$1" 10 76; }
info()     { wt --infobox "$1" 8 76; }

log() { echo "$(date '+%F %T') $*" >> "$LOG"; }

# run <Text fuer die Anzeige> <Befehl...>: fuehrt aus, Ausgabe ins Protokoll; bei Fehler Meldung
run() {
    local text="$1"; shift
    info "$text\n\nBitte warten ..."
    log "START: $*"
    if "$@" >> "$LOG" 2>&1; then
        log "OK: $*"
        return 0
    fi
    log "FEHLER: $*"
    msg "Fehler bei: $text\n\nLetzte Zeilen aus $LOG:\n\n$(tail -n 8 "$LOG" | cut -c1-70)" 22
    return 1
}

# ---------------------------------------------------------------- Einstellungsdatei

env_get() {
    [ -r "$ENV_FILE" ] && sed -n "s/^$1=//p" "$ENV_FILE" | head -n 1
}

# env_set KEY WERT: ersetzt KEY= oder #KEY= oder haengt an. Schreibt in die bestehende Datei,
# damit Besitzer und Rechte (root:himbeerepi 640) erhalten bleiben.
env_set() {
    local key="$1" value="$2" tmp
    tmp="$(mktemp)"
    awk -v k="$key" -v v="$value" '
        !done && ($0 ~ "^" k "=" || $0 ~ "^#" k "=") { print k "=" v; done = 1; next }
        { print }
        END { if (!done) print k "=" v }' "$ENV_FILE" > "$tmp" && cat "$tmp" > "$ENV_FILE"
    rm -f "$tmp"
    log "Einstellung: $key gesetzt"
}

env_unset() {
    sed -i "s/^$1=/#$1=/" "$ENV_FILE"
    log "Einstellung: $1 entfernt"
}

data_dir()    { local d; d="$(env_get HIMBEEREPI_DATA_DIR)"; echo "${d:-$DATA_DIR_DEFAULT}"; }
installed()   { [ -x "$INSTALL_DIR/venv/bin/python3" ] && [ -f "$ENV_FILE" ]; }
restart_app() { systemctl restart himbeerepi >> "$LOG" 2>&1; }
app_ok()      { [ "$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 http://127.0.0.1:5000/login || true)" = "200" ]; }

need_install() {
    installed && return 0
    msg "Dafür muss HimbeerePi zuerst installiert sein (Grundeinrichtung bzw. \"HimbeerePi installieren\")."
    return 1
}

apt_install() {
    local missing=() p
    for p in "$@"; do
        dpkg -s "$p" >/dev/null 2>&1 || missing+=("$p")
    done
    [ ${#missing[@]} -eq 0 ] && return 0
    run "Installiere ${missing[*]}" bash -c "apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq ${missing[*]}"
}

manage() {
    local db
    db="$(env_get HIMBEEREPI_DB)"
    sudo -u "$SERVICE_USER" env HIMBEEREPI_DB="${db:-/var/lib/himbeerepi/users.db}" \
        "$INSTALL_DIR/venv/bin/python3" "$INSTALL_DIR/app/manage.py" "$@"
}

lan_ip()     { hostname -I 2>/dev/null | awk '{print $1}'; }
lan_subnet() {
    local dev
    dev="$(ip -4 route show default 2>/dev/null | awk '{print $5; exit}')"
    [ -n "$dev" ] && ip -o -4 route show dev "$dev" scope link 2>/dev/null | awk '{print $1; exit}'
}

human() { numfmt --to=iec --suffix=B "$1" 2>/dev/null || echo "$1 Bytes"; }

# Stromschaetzung: pauschal je eingehaengter externer Platte
update_hdd_watts() {
    local n=0
    mountpoint -q "$(data_dir)" && n=$((n + 1))
    mountpoint -q "$BACKUP_MOUNT" && n=$((n + 1))
    env_set HIMBEEREPI_HDD_WATTS "$((n * HDD_WATTS_EACH))"
}

# ---------------------------------------------------------------- Festplatten

# Platte, auf der das System laeuft (SD-Karte oder USB-/NVMe-Boot) - wird nie angeboten
root_disk() {
    local src parent
    src="$(findmnt -no SOURCE / 2>/dev/null)"
    parent="$(lsblk -no PKNAME "$src" 2>/dev/null | head -n 1)"
    if [ -n "$parent" ]; then echo "$parent"; else basename "$src"; fi
}

field() { sed -n "s/.* $1=\"\([^\"]*\)\".*/\1/p"; }

# Menuepunkte fuer alle Platten ausser der Systemplatte: "/dev/sda" "500G Samsung SSD (usb)"
disk_items() {
    local root line name size model tran used
    root="$(root_disk)"
    lsblk -dpnP -o NAME,TYPE,SIZE,TRAN,MODEL 2>/dev/null | while read -r line; do
        line=" $line"
        [ "$(echo "$line" | field TYPE)" = "disk" ] || continue
        name="$(echo "$line" | field NAME)"
        [ -n "$root" ] && [ "$name" = "/dev/$root" ] && continue
        case "$name" in /dev/zram*|/dev/ram*) continue ;; esac
        size="$(echo "$line" | field SIZE)"; model="$(echo "$line" | field MODEL)"; tran="$(echo "$line" | field TRAN)"
        used="$(lsblk -nlo MOUNTPOINT "$name" 2>/dev/null | grep -v '^$' | tr '\n' ' ')"
        printf '%s\n%s\n' "$name" "$size ${model:-Laufwerk}${tran:+ ($tran)}${used:+ – IN BENUTZUNG: $used}"
    done
}

# Partitionen mit ext4 ausser auf der Systemplatte. Typ und Name kommen direkt von blkid
# (lsblk liest sie aus der udev-Datenbank, die nicht immer aktuell ist).
partition_items() {
    local root line name
    root="$(root_disk)"
    lsblk -pnP -o NAME,TYPE,SIZE,PKNAME 2>/dev/null | while read -r line; do
        line=" $line"
        [ "$(echo "$line" | field TYPE)" = "part" ] || continue
        [ -n "$root" ] && [ "$(echo "$line" | field PKNAME)" = "/dev/$root" ] && continue
        name="$(echo "$line" | field NAME)"
        [ "$(blkid -s TYPE -o value "$name" 2>/dev/null)" = "ext4" ] || continue
        printf '%s\n%s\n' "$name" "$(echo "$line" | field SIZE) ext4 $(blkid -s LABEL -o value "$name" 2>/dev/null)"
    done
}

first_partition() {
    case "$1" in
        *[0-9]) echo "${1}p1" ;;   # /dev/nvme0n1 -> /dev/nvme0n1p1, /dev/mmcblk1 -> /dev/mmcblk1p1
        *)      echo "${1}1" ;;    # /dev/sda -> /dev/sda1
    esac
}

# Platten, die das System oder die jeweils andere Cloud-Platte tragen, nie formatieren
disk_protected() {
    local disk="$1" target="$2" m
    while read -r m; do
        [ -z "$m" ] && continue
        [ "$m" = "$target" ] && continue
        case "$m" in
            /|/boot|/boot/*|"$(data_dir)"|"$BACKUP_MOUNT") echo "$m"; return 0 ;;
        esac
    done < <(lsblk -nlo MOUNTPOINT "$disk" 2>/dev/null)
    return 1
}

# Platte auswaehlen und formatieren. Ergebnis: Partition in PART. $1 Ziel-Einhaengepunkt, $2 Label
PART=""
choose_and_format() {
    local target="$1" label="$2" items disk used confirm
    mapfile -t items < <(disk_items)
    if [ ${#items[@]} -eq 0 ]; then
        msg "Keine zusätzliche Platte gefunden. Bitte die USB-Platte anschließen und den Schritt wiederholen."
        return 1
    fi
    disk="$(wt --menu "Welche Platte soll formatiert werden?\n(Die Systemplatte/SD-Karte wird nicht angezeigt.)" 18 76 6 "${items[@]}")" || return 1
    if used="$(disk_protected "$disk" "$target")"; then
        msg "$disk wird gerade als $used benutzt und kann hier nicht formatiert werden."
        return 1
    fi
    ask_no "ACHTUNG: Alle Daten auf dieser Platte werden gelöscht!\n\n$(lsblk -o NAME,SIZE,FSTYPE,LABEL,MOUNTPOINT "$disk" 2>/dev/null)\n\nWirklich $disk formatieren?" 20 || return 1
    confirm="$(input "Zur Sicherheit den Namen der Platte eintippen: $(basename "$disk")")" || return 1
    if [ "$confirm" != "$(basename "$disk")" ]; then
        msg "Eingabe stimmt nicht überein – nichts wurde verändert."
        return 1
    fi
    apt_install parted || return 1
    PART="$(first_partition "$disk")"
    lsblk -pnlo NAME,MOUNTPOINT "$disk" | awk '$2 != "" {print $2}' | while read -r m; do umount "$m" >> "$LOG" 2>&1; done
    run "Formatiere $disk (ext4)" bash -c "wipefs -a '$disk' && parted -s '$disk' mklabel gpt mkpart '$label' ext4 0% 100% && (partprobe '$disk' || true) && (udevadm settle 2>/dev/null || true) && sleep 2 && mkfs.ext4 -F -L '$label' '$PART'"
}

# Vorhandene ext4-Partition auswaehlen. Ergebnis in PART.
choose_partition() {
    local target="$1" items used
    mapfile -t items < <(partition_items)
    if [ ${#items[@]} -eq 0 ]; then
        msg "Keine ext4-Partition gefunden. Platten mit NTFS/exFAT/FAT bitte über \"neu einrichten\" formatieren (dabei gehen die Daten darauf verloren)."
        return 1
    fi
    PART="$(wt --menu "Welche Partition soll verwendet werden?" 18 76 6 "${items[@]}")" || return 1
    used="$(findmnt -no TARGET "$PART" 2>/dev/null | head -n 1)"
    if [ -n "$used" ] && [ "$used" != "$target" ]; then
        msg "$PART ist bereits unter $used eingehängt und kann hier nicht verwendet werden."
        return 1
    fi
}

# Menue "neu formatieren / vorhandene Partition". Ergebnis in PART.
choose_disk() {
    local target="$1" label="$2" text="$3" choice
    choice="$(wt --menu "$text\n\nWas möchtest du tun?" 20 76 3 \
        "neu"       "Platte neu einrichten (wird FORMATIERT)" \
        "vorhanden" "Vorhandene ext4-Partition verwenden (Daten bleiben)" \
        "abbrechen" "Nichts ändern")" || return 1
    case "$choice" in
        neu)       choose_and_format "$target" "$label" ;;
        vorhanden) choose_partition "$target" ;;
        *)         return 1 ;;
    esac
}

fstab_set() {
    local part="$1" mp="$2" uuid tmp
    uuid="$(blkid -s UUID -o value "$part")"
    [ -n "$uuid" ] || return 1
    [ -f "$FSTAB.himbeerepi-bak" ] || cp "$FSTAB" "$FSTAB.himbeerepi-bak"
    tmp="$(mktemp)"
    awk -v mp="$mp" '$2 != mp' "$FSTAB" > "$tmp" && cat "$tmp" > "$FSTAB"
    rm -f "$tmp"
    echo "UUID=$uuid  $mp  ext4  defaults,nofail,x-systemd.device-timeout=10s  0  2" >> "$FSTAB"
    systemctl daemon-reload >> "$LOG" 2>&1
    log "fstab: UUID=$uuid -> $mp"
}

fstab_remove() {
    local mp="$1" tmp
    tmp="$(mktemp)"
    awk -v mp="$mp" '$2 != mp' "$FSTAB" > "$tmp" && cat "$tmp" > "$FSTAB"
    rm -f "$tmp"
    systemctl daemon-reload >> "$LOG" 2>&1
    log "fstab: Eintrag fuer $mp entfernt"
}

mount_fstab() {
    local mp="$1"
    mkdir -p "$mp"
    mount "$mp" >> "$LOG" 2>&1 && mountpoint -q "$mp"
}

# ---------------------------------------------------------------- Grundeinrichtung

step_install() {
    local verb="installieren" text
    installed && verb="aktualisieren"
    ask "HimbeerePi $verb\n\nInstalliert Pakete, Dienst, Datenbank, tägliches Backup und Gesundheitsprüfung. Einstellungen, Benutzer und Dateien bleiben erhalten.\n\nOhne externe Festplatte liegen die Dateien zunächst auf der SD-Karte unter $(data_dir). Eine Festplatte lässt sich später jederzeit ergänzen (Ausbau 1) – die Dateien ziehen dann automatisch um.\n\nDauer: etwa 2–10 Minuten." 20 || return 0
    text="HimbeerePi wird installiert"
    [ "$verb" = "aktualisieren" ] && text="HimbeerePi wird aktualisiert"
    if run "$text" "$INSTALL_DIR/deploy/install.sh"; then
        if [ "$verb" = "aktualisieren" ]; then
            msg "HimbeerePi ist aktualisiert und läuft."
        else
            msg "HimbeerePi läuft.\n\nWeiter geht es mit dem ersten Benutzer."
        fi
    fi
}

create_user() {
    local first="$1" name pw1 pw2 quota stats_flag=() quota_flag=() out
    while true; do
        name="$(input "Benutzername (Buchstaben, Ziffern, . _ -)" "")" || return 1
        [[ "$name" =~ ^[A-Za-z0-9._-]{1,32}$ ]] && break
        msg "Ungültiger Benutzername. Erlaubt: A–Z, a–z, 0–9, Punkt, Unterstrich, Bindestrich (max. 32 Zeichen)."
    done
    while true; do
        pw1="$(password "Passwort für $name (mindestens 12 Zeichen)")" || return 1
        pw2="$(password "Passwort wiederholen")" || return 1
        if [ "$pw1" != "$pw2" ]; then msg "Die Passwörter stimmen nicht überein."; continue; fi
        if [ ${#pw1} -lt 12 ]; then msg "Das Passwort ist zu kurz (mindestens 12 Zeichen)."; continue; fi
        break
    done
    if [ "$first" = "1" ]; then
        msg "Der erste Benutzer bekommt kein Speicherlimit und sieht den Pi-Status (Auslastung, Backups)." 10
    else
        while true; do
            quota="$(input "Speicherlimit in GB für $name\n(leer lassen = kein Limit, sieht dann den ganzen Speicher)" "")" || return 1
            [ -z "$quota" ] || [[ "$quota" =~ ^[0-9]+([.,][0-9]+)?$ ]] && break
            msg "Bitte eine Zahl eingeben, z. B. 500."
        done
        [ -n "$quota" ] && quota_flag=(--quota-gb "$quota")
        ask_no "Soll $name den Pi-Status (Auslastung, Backups) sehen?" 10 || stats_flag=(--no-stats)
    fi
    if out="$(printf '%s\n' "$pw1" | manage add-user --username "$name" --password-stdin "${quota_flag[@]}" "${stats_flag[@]}" 2>&1)"; then
        log "Benutzer $name angelegt"
        msg "Benutzer \"$name\" wurde angelegt.\n\nTipp: Nach der ersten Anmeldung oben auf \"2FA\" klicken und die Zwei-Faktor-Anmeldung einrichten."
    else
        msg "Benutzer konnte nicht angelegt werden:\n\n$(echo "$out" | tail -n 3)"
        return 1
    fi
}

step_users() {
    need_install || return 0
    local count
    count="$(manage count-users 2>>"$LOG" | tail -n 1)"
    if [ "${count:-0}" = "0" ]; then
        msg "Benutzer\n\nJetzt legst du dein eigenes Konto an (Administrator)." 10
        create_user 1 || return 0
    fi
    while ask_no "Benutzer\n\nVorhandene Benutzer:\n$(manage list-users 2>/dev/null)\n\nWeiteren Benutzer anlegen (z. B. für die Familie)?" 20; do
        create_user 0
    done
}

write_caddyfile() {
    local site="$1"
    mkdir -p "$(dirname "$CADDYFILE")"
    if [ -f "$CADDYFILE" ] && ! grep -q "$CADDY_MARKER" "$CADDYFILE"; then
        cp "$CADDYFILE" "$CADDYFILE.himbeerepi-bak-$(date +%Y%m%d%H%M%S)"
        log "Caddyfile gesichert"
    fi
    printf '%s\n# Neu erzeugen: sudo %s/deploy/setup.sh -> Zugang\n\n%s {\n\treverse_proxy 127.0.0.1:5000\n}\n' \
        "$CADDY_MARKER" "$INSTALL_DIR" "$site" > "$CADDYFILE"
    log "Caddyfile: $site"
}

access_internet() {
    local domain dns_ip pub_ip code i
    domain="$(input "Unter welcher Adresse soll die Cloud erreichbar sein?\n\nBeispiel: cloud.meinname.de\n(Die Domain oder DynDNS-Adresse muss auf deinen Internetanschluss zeigen.)" "$(env_get HIMBEEREPI_DOMAIN)" 14)" || return 1
    domain="$(echo "$domain" | tr 'A-Z' 'a-z' | sed 's#^https\?://##; s#/.*$##')"
    if ! [[ "$domain" =~ ^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$ ]]; then
        msg "\"$domain\" ist keine gültige Adresse."
        return 1
    fi
    info "Prüfe, ob $domain auf deinen Anschluss zeigt ..."
    dns_ip="$(getent ahostsv4 "$domain" 2>/dev/null | awk 'NR==1 {print $1}')"
    pub_ip="$(curl -4 -s --max-time 8 https://api.ipify.org || true)"
    if [ -z "$dns_ip" ]; then
        ask_no "$domain ist im DNS nicht zu finden.\n\nLege beim Domain- oder DynDNS-Anbieter einen Eintrag an, der auf ${pub_ip:-deine öffentliche IP} zeigt. Änderungen brauchen manchmal einige Minuten.\n\nTrotzdem fortfahren?" 16 || return 1
    elif [ -n "$pub_ip" ] && [ "$dns_ip" != "$pub_ip" ]; then
        ask_no "$domain zeigt auf $dns_ip,\ndein Anschluss hat aber die IP $pub_ip.\n\nPrüfe den DNS-Eintrag bzw. DynDNS. (Bei DS-Lite-Anschlüssen ohne eigene IPv4 klappt der Zugang von außen so nicht.)\n\nTrotzdem fortfahren?" 16 || return 1
    fi
    msg "Router einstellen (Portfreigabe)\n\nIm Router für den Raspberry Pi ($(lan_ip)) freigeben:\n\n   TCP-Port 80   (für das Zertifikat)\n   TCP-Port 443  (HTTPS)\n\nFRITZ!Box: Internet → Freigaben → Portfreigaben → Gerät hinzufügen.\n\nNiemals Port 22 oder 5000 freigeben." 18
    ask "Sind die Portfreigaben eingerichtet?" 8 || return 1
    apt_install caddy || return 1
    write_caddyfile "$domain"
    env_set HIMBEEREPI_DOMAIN "$domain"
    env_set HIMBEEREPI_ACCESS internet
    env_unset HIMBEEREPI_INSECURE_COOKIE
    restart_app
    systemctl enable caddy >> "$LOG" 2>&1
    systemctl restart caddy >> "$LOG" 2>&1
    # Auf das Zertifikat warten. --resolve prueft am Pi selbst (klappt auch, wenn der Router
    # Aufrufe der eigenen Adresse von innen nicht zulaesst) - das Zertifikat wird trotzdem geprueft.
    for i in $(seq 1 24); do
        info "Warte auf das HTTPS-Zertifikat von Let's Encrypt ... ($((i * 5)) s)"
        code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 --resolve "$domain:443:127.0.0.1" "https://$domain/login" || true)"
        [ "$code" = "200" ] && break
        sleep 5
    done
    if [ "$code" = "200" ]; then
        log "HTTPS ok fuer $domain"
        msg "Geschafft!\n\nDie Cloud ist erreichbar unter:\n\n   https://$domain\n\nDas Zertifikat erneuert Caddy automatisch."
    else
        msg "Das Zertifikat konnte (noch) nicht geholt werden.\n\nHäufige Ursachen: Portfreigabe 80/443 fehlt, der DNS-Eintrag zeigt noch nicht auf deinen Anschluss, oder der Anschluss hat keine eigene IPv4 (DS-Lite).\n\nCaddy versucht es im Hintergrund weiter. Log ansehen:\n   journalctl -u caddy -n 50" 18
    fi
}

access_lan() {
    ask_no "Nur im Heimnetz\n\nDie Cloud ist dann unter http://$(lan_ip) erreichbar – ohne Verschlüsselung und nicht von unterwegs. Passwörter gehen unverschlüsselt durchs Heimnetz (WLAN!).\n\nFür unterwegs besser später \"Domain mit HTTPS\" wählen oder ein VPN nutzen.\n\nSo einrichten?" 16 || return 1
    apt_install caddy || return 1
    write_caddyfile ":80"
    env_set HIMBEEREPI_ACCESS heimnetz
    env_set HIMBEEREPI_INSECURE_COOKIE 1
    env_unset HIMBEEREPI_DOMAIN
    restart_app
    systemctl enable caddy >> "$LOG" 2>&1
    systemctl restart caddy >> "$LOG" 2>&1
    sleep 2
    if [ "$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1/login || true)" = "200" ]; then
        msg "Die Cloud ist im Heimnetz erreichbar unter:\n\n   http://$(lan_ip)\n\n(Am Router keine Portfreigabe einrichten!)"
    else
        msg "Caddy antwortet nicht. Log ansehen: journalctl -u caddy -n 50"
    fi
}

step_access() {
    need_install || return 0
    local choice
    choice="$(wt --menu "Zugang\n\nWie soll die Cloud erreichbar sein?" 16 76 3 \
        "internet" "Von überall: eigene Domain mit HTTPS (empfohlen)" \
        "heimnetz" "Nur im Heimnetz, ohne Verschlüsselung" \
        "weiter"   "Nichts ändern / überspringen")" || return 0
    case "$choice" in
        internet) access_internet ;;
        heimnetz) access_lan ;;
    esac
    return 0
}

ssh_peers() {
    ss -Htn state established '( sport = :22 )' 2>/dev/null | awk '{print $4}' \
        | sed -E 's/^\[//; s/^::ffff://; s/\]?:[0-9]+$//' | sort -u
}

step_firewall() {
    local subnet access peers rules p
    subnet="$(lan_subnet)"
    access="$(env_get HIMBEEREPI_ACCESS)"
    peers="$(ssh_peers)"
    rules="  - alles Eingehende blockieren, außer:\n"
    rules+="  - SSH (Port 22) aus dem Heimnetz ${subnet:-(nicht erkannt)}\n"
    for p in $peers; do rules+="  - SSH von deiner aktuellen Verbindung $p\n"; done
    case "$access" in
        internet) rules+="  - HTTP/HTTPS (80, 443) von überall (für Caddy)\n" ;;
        heimnetz) rules+="  - HTTP (80) aus dem Heimnetz\n" ;;
        *)        rules+="  - (noch kein Zugang eingerichtet)\n" ;;
    esac
    if [ -z "$subnet" ] && [ -z "$peers" ]; then
        msg "Das Heimnetz konnte nicht erkannt werden – die Firewall wird nicht eingerichtet, um dich nicht auszusperren."
        return 0
    fi
    ask "Firewall\n\nFolgende Regeln werden eingerichtet (ufw):\n\n$rules\nEinrichten?" 20 || return 0
    apt_install ufw || return 0
    {
        ufw default deny incoming
        ufw default allow outgoing
        [ -n "$subnet" ] && ufw allow from "$subnet" to any port 22 proto tcp
        for p in $peers; do ufw allow from "$p" to any port 22 proto tcp; done
        case "$access" in
            internet) ufw allow 80/tcp; ufw allow 443/tcp ;;
            heimnetz) [ -n "$subnet" ] && ufw allow from "$subnet" to any port 80 proto tcp ;;
        esac
        ufw --force enable
    } >> "$LOG" 2>&1
    msg "Firewall ist aktiv.\n\n$(ufw status 2>/dev/null | head -n 14)" 22
}

step_ntfy() {
    local topic
    topic="$(env_get NTFY_TOPIC)"
    [ -z "$topic" ] && topic="himbeerepi-$(od -An -tx1 -N8 /dev/urandom | tr -d ' \n')"
    topic="$(input "Push-Meldungen über ntfy\n\nNach jedem nächtlichen Backup kommt eine Meldung aufs Handy. Dafür die App \"ntfy\" installieren und diesen Kanal abonnieren. Der Name wirkt wie ein Passwort – zufällig lassen!\n\nKanal (leer = ausschalten):" "$topic" 18)" || return 0
    if [ -z "$topic" ]; then
        env_unset NTFY_TOPIC
        msg "Push-Meldungen sind ausgeschaltet."
        return 0
    fi
    if ! [[ "$topic" =~ ^[A-Za-z0-9_-]{8,64}$ ]]; then
        msg "Ungültiger Kanalname (8–64 Zeichen, nur Buchstaben, Ziffern, _ und -)."
        return 0
    fi
    env_set NTFY_TOPIC "$topic"
    # shellcheck source=/dev/null
    if (HIMBEEREPI_ENV_FILE="$ENV_FILE" && source "$INSTALL_DIR/scripts/notify_ntfy.sh" \
        && notify_backup "HimbeerePi: Test" "Push-Meldungen funktionieren." "default" "tada"); then
        msg "Eine Testmeldung wurde an den Kanal\n\n   $topic\n\ngeschickt. In der ntfy-App diesen Kanal abonnieren."
    else
        msg "Die Testmeldung konnte nicht verschickt werden (Internet?). Der Kanal ist trotzdem gespeichert."
    fi
}

step_price() {
    local price
    price="$(input "Strompreis in Euro pro kWh (für die geschätzten Stromkosten im Pi-Status)" "$(env_get HIMBEEREPI_POWER_PRICE | sed 's/^$/0.35/')")" || return 0
    price="${price/,/.}"
    if ! [[ "$price" =~ ^[0-9]+(\.[0-9]+)?$ ]]; then
        msg "Bitte eine Zahl eingeben, z. B. 0.35"
        return 0
    fi
    env_set HIMBEEREPI_POWER_PRICE "$price"
    restart_app
}

step_extras() {
    need_install || return 0
    local choice
    while true; do
        choice="$(wt --menu "Extras (optional)" 14 76 3 \
            "ntfy"   "Push-Meldungen zum Backup (ntfy)" \
            "strom"  "Strompreis für die Kostenanzeige" \
            "fertig" "Fertig")" || return 0
        case "$choice" in
            ntfy)  step_ntfy ;;
            strom) step_price ;;
            *)     return 0 ;;
        esac
    done
}

# ---------------------------------------------------------------- Ausbau 1: Speicher-Festplatte

# Rueckfall, falls nach dem Umschalten etwas schiefgeht: alten Zustand wiederherstellen
restore_sd_copy() {
    local dir="$1" old="$2"
    mountpoint -q "$dir" && umount "$dir" >> "$LOG" 2>&1
    fstab_remove "$dir"
    if [ -d "$old" ]; then
        rmdir "$dir" 2>/dev/null
        mv "$old" "$dir"
    fi
    update_hdd_watts
    restart_app
    log "Umzug zurueckgenommen, Dateien wieder auf der SD-Karte"
}

step_storage() {
    need_install || return 0
    local dir used_bytes files avail old diff
    dir="$(data_dir)"
    if mountpoint -q "$dir"; then
        msg "Ausbau 1: Speicher-Festplatte\n\nIst bereits eingerichtet:\n$(findmnt -no SOURCE "$dir") unter $dir, $(df -h --output=size,avail "$dir" | tail -n 1 | awk '{print $1 " gesamt, " $2 " frei"}')."
        return 0
    fi
    used_bytes="$(du -sb "$dir" 2>/dev/null | awk '{print $1}')"
    files="$(find "$dir" -type f 2>/dev/null | wc -l)"
    choose_disk "$MIGRATE_MOUNT" "himbeerepi" "Ausbau 1: Festplatte als Speicher\n\nDie Dateien liegen bisher auf der SD-Karte ($files Datei(en), $(human "${used_bytes:-0}")). Sie werden auf die Festplatte kopiert und geprüft; danach nutzt die Cloud die Platte. Während des Umzugs ist die Cloud kurz nicht erreichbar. Die Kopie auf der SD-Karte bleibt, bis du sie löschst." || return 0

    # Partition vorübergehend einhängen, Platz und vorhandenen Inhalt prüfen
    mkdir -p "$MIGRATE_MOUNT"
    if ! mount "$PART" "$MIGRATE_MOUNT" >> "$LOG" 2>&1; then
        msg "$PART lässt sich nicht einhängen. Details: $LOG"
        return 0
    fi
    avail="$(df -B1 --output=avail "$MIGRATE_MOUNT" | tail -n 1 | tr -d ' ')"
    if [ "${avail:-0}" -lt "$(( ${used_bytes:-0} + 104857600 ))" ]; then
        umount "$MIGRATE_MOUNT" >> "$LOG" 2>&1
        msg "Auf $PART ist zu wenig Platz: frei $(human "$avail"), benötigt $(human "${used_bytes:-0}")."
        return 0
    fi
    if [ -n "$(find "$MIGRATE_MOUNT" -mindepth 1 -maxdepth 1 ! -name 'lost+found' -print -quit 2>/dev/null)" ]; then
        if ! ask_no "Auf $PART liegen bereits Dateien. Die Cloud-Dateien werden dazukopiert, Vorhandenes bleibt erhalten.\n\nFortfahren?" 12; then
            umount "$MIGRATE_MOUNT" >> "$LOG" 2>&1
            return 0
        fi
    fi

    # Kopieren und pruefen (Dienst angehalten, damit sich nichts mehr aendert)
    if ! apt_install rsync; then
        umount "$MIGRATE_MOUNT" >> "$LOG" 2>&1
        return 0
    fi
    systemctl stop himbeerepi >> "$LOG" 2>&1
    if ! run "Kopiere $files Datei(en) ($(human "${used_bytes:-0}")) auf die Festplatte" rsync -aHAX "$dir/" "$MIGRATE_MOUNT/"; then
        umount "$MIGRATE_MOUNT" >> "$LOG" 2>&1
        restart_app
        return 0
    fi
    info "Prüfe die Kopie (Prüfsummen aller Dateien) ...\n\nBitte warten ..."
    diff="$(rsync -a --checksum --dry-run --itemize-changes "$dir/" "$MIGRATE_MOUNT/" 2>>"$LOG")"
    if [ -n "$diff" ]; then
        log "Pruefung fehlgeschlagen: $diff"
        umount "$MIGRATE_MOUNT" >> "$LOG" 2>&1
        restart_app
        msg "Die Kopie weicht vom Original ab – der Umzug wurde abgebrochen. Die Cloud läuft unverändert von der SD-Karte weiter. Details: $LOG"
        return 0
    fi
    chown "$SERVICE_USER:$SERVICE_USER" "$MIGRATE_MOUNT"
    chmod 750 "$MIGRATE_MOUNT"
    umount "$MIGRATE_MOUNT" >> "$LOG" 2>&1
    rmdir "$MIGRATE_MOUNT" 2>/dev/null

    # Umschalten: SD-Ordner beiseitelegen, Platte an seiner Stelle einhaengen
    old="${dir}.sd-kopie-$(date +%Y%m%d-%H%M)"
    mv "$dir" "$old"
    mkdir -p "$dir"
    if ! fstab_set "$PART" "$dir" || ! mount_fstab "$dir"; then
        restore_sd_copy "$dir" "$old"
        msg "Die Platte konnte nicht eingehängt werden. Alles wurde zurückgenommen – die Cloud läuft wie vorher von der SD-Karte. Details: $LOG"
        return 0
    fi
    install -d -m 750 -o "$SERVICE_USER" -g "$SERVICE_USER" "$dir/tmp_uploads" "$dir/thumbnails" "$dir/backups"
    update_hdd_watts
    restart_app
    sleep 2
    if ! app_ok; then
        restore_sd_copy "$dir" "$old"
        msg "Die Cloud ist mit der Platte nicht gestartet. Alles wurde zurückgenommen – sie läuft wie vorher von der SD-Karte. Details: $LOG und journalctl -u himbeerepi" 12
        return 0
    fi
    log "Umzug auf $PART abgeschlossen, alte Kopie: $old"
    if ask_no "Umzug abgeschlossen!\n\nDie Cloud nutzt jetzt $PART unter $dir. Alle $files Datei(en) wurden kopiert und geprüft.\n\nDie alte Kopie auf der SD-Karte liegt noch unter\n  $old\n\nJetzt löschen, um Platz auf der SD-Karte freizugeben?\n(Empfehlung: erst in der Cloud nachsehen, ob alles da ist, und später löschen.)" 20; then
        case "$old" in
            "$dir".sd-kopie-*) rm -rf -- "$old" && log "Alte SD-Kopie geloescht" ;;
        esac
        msg "Die alte Kopie wurde gelöscht."
    else
        msg "Die alte Kopie bleibt erhalten. Später löschen mit:\n\n  sudo rm -rf $old" 12
    fi
}

# ---------------------------------------------------------------- Ausbau 2: Backup-Festplatte

step_backup() {
    need_install || return 0
    local dir
    dir="$(data_dir)"
    if mountpoint -q "$BACKUP_MOUNT"; then
        ask_no "Ausbau 2: Backup-Festplatte\n\nIst bereits eingerichtet: $(findmnt -no SOURCE "$BACKUP_MOUNT") unter $BACKUP_MOUNT, $(df -h --output=avail "$BACKUP_MOUNT" | tail -n 1 | tr -d ' ') frei.\n\nEine andere Platte einrichten?" 14 || return 0
    elif ! mountpoint -q "$dir"; then
        ask_no "Ausbau 2: Backup-Festplatte\n\nHinweis: Die Dateien liegen noch auf der SD-Karte (Ausbau 1 fehlt). Eine Backup-Platte schützt sie trotzdem – empfohlen ist aber, zuerst Ausbau 1 einzurichten.\n\nTrotzdem jetzt die Backup-Platte einrichten?" 14 || return 0
    fi
    choose_disk "$BACKUP_MOUNT" "himbeerepi-bak" "Ausbau 2: zweite Festplatte als Backup\n\nAuf diese Platte kommen jede Nacht eine Kopie der Datenbank und jeden Sonntag eine Spiegelung aller Dateien. Fällt die Speicher-Platte aus, ist nichts verloren." || return 0
    mountpoint -q "$BACKUP_MOUNT" && umount "$BACKUP_MOUNT" >> "$LOG" 2>&1
    if ! fstab_set "$PART" "$BACKUP_MOUNT" || ! mount_fstab "$BACKUP_MOUNT"; then
        fstab_remove "$BACKUP_MOUNT"
        msg "$PART konnte nicht eingehängt werden. Details: $LOG"
        return 0
    fi
    install -d -o "$SERVICE_USER" -g "$SERVICE_USER" "$BACKUP_MOUNT/db_backups"
    env_set HIMBEEREPI_BACKUP_MOUNT "$BACKUP_MOUNT"
    update_hdd_watts
    restart_app
    apt_install rsync
    printf '# HimbeerePi: woechentliche Spiegelung aller Dateien (angelegt vom Einrichtungsassistenten)\n0 2 * * 0 root %s/scripts/mirror_files.sh >> /var/log/himbeerepi-mirror.log 2>&1\n' \
        "$INSTALL_DIR" > "$MIRROR_CRON"
    log "Backup-Platte $PART, Spiegelung eingerichtet"
    if ask "Backup-Platte ist eingerichtet ($PART unter $BACKUP_MOUNT).\n\n  - Datenbank: jede Nacht um 3:10 Uhr\n  - alle Dateien: jeden Sonntag um 2 Uhr gespiegelt\n\nErste Spiegelung jetzt im Hintergrund starten?" 16; then
        nohup "$INSTALL_DIR/scripts/mirror_files.sh" >> /var/log/himbeerepi-mirror.log 2>&1 &
        msg "Die erste Spiegelung läuft im Hintergrund. Fortschritt/Fehler: /var/log/himbeerepi-mirror.log"
    fi
}

# ---------------------------------------------------------------- Uebersicht und Ablauf

summary_text() {
    local access domain addr dir storage backup users fw ntfy
    access="$(env_get HIMBEEREPI_ACCESS)"; domain="$(env_get HIMBEEREPI_DOMAIN)"
    case "$access" in
        internet) addr="https://$domain" ;;
        heimnetz) addr="http://$(lan_ip) (nur Heimnetz)" ;;
        *)        addr="noch nicht eingerichtet (Zugang)" ;;
    esac
    dir="$(data_dir)"
    if mountpoint -q "$dir"; then
        storage="Festplatte $(findmnt -no SOURCE "$dir"), $(df -h --output=avail "$dir" | tail -n 1 | tr -d ' ') frei"
    elif [ -d "$dir" ]; then
        storage="SD-Karte, $(df -h --output=avail "$dir" | tail -n 1 | tr -d ' ') frei (Ausbau 1 fehlt)"
    else
        storage="-"
    fi
    if mountpoint -q "$BACKUP_MOUNT"; then
        backup="Festplatte $(findmnt -no SOURCE "$BACKUP_MOUNT")"
        [ -f "$MIRROR_CRON" ] && backup+=", wöchentliche Spiegelung"
    else
        backup="nur auf dem Speicher selbst (Ausbau 2 fehlt)"
    fi
    if installed; then
        users="$(manage count-users 2>/dev/null | tail -n 1)"
        if systemctl is-active --quiet himbeerepi; then users+=" (Dienst läuft)"; else users+=" (Dienst läuft NICHT)"; fi
    else
        users="HimbeerePi noch nicht installiert"
    fi
    fw="$(ufw status 2>/dev/null | head -n 1 | sed 's/Status: //')"; fw="${fw:-nicht eingerichtet}"
    if [ -n "$(env_get NTFY_TOPIC)" ]; then ntfy="an"; else ntfy="aus"; fi
    printf 'Adresse:        %s\nSpeicher:       %s\nBackup:         %s\nBenutzer:       %s\nFirewall:       %s\nPush-Meldungen: %s\n' \
        "$addr" "$storage" "$backup" "$users" "$fw" "$ntfy"
}

step_summary() {
    msg "Übersicht\n\n$(summary_text)\n\nÄndern oder ausbauen: diesen Assistenten jederzeit erneut starten:\n   sudo $INSTALL_DIR/deploy/setup.sh" 20
}

run_basic() {
    step_install
    installed || return 0
    step_users
    step_access
    step_firewall
    step_extras
    step_summary
    if [ -n "$(disk_items)" ] && ! mountpoint -q "$(data_dir)"; then
        ask_no "Es ist eine Festplatte angeschlossen.\n\nJetzt Ausbau 1 einrichten (Festplatte als Speicher)?" 10 && step_storage
    fi
    return 0
}

main() {
    if [ "$(id -u)" -ne 0 ]; then
        echo "Bitte mit sudo starten: sudo $0" >&2
        exit 1
    fi
    if [ ! -f "$INSTALL_DIR/deploy/install.sh" ]; then
        echo "HimbeerePi muss unter $INSTALL_DIR liegen:" >&2
        echo "  sudo git clone https://github.com/GhostBeacon/himbeercloud.git $INSTALL_DIR" >&2
        exit 1
    fi
    touch "$LOG" && chmod 600 "$LOG"
    if ! command -v whiptail >/dev/null; then
        echo "Installiere whiptail ..."
        apt-get update -qq && apt-get install -y -qq whiptail >/dev/null || exit 1
    fi
    log "Assistent gestartet"

    msg "Willkommen bei HimbeerePi!\n\nDie Einrichtung geht in Stufen:\n\n  Grundeinrichtung   ohne externe Festplatte – die Dateien liegen\n                     auf der SD-Karte. Installation, Benutzer,\n                     Zugang, Firewall.\n  Ausbau 1           Festplatte als Speicher (Dateien ziehen um)\n  Ausbau 2           zweite Festplatte als Backup\n\nBedienung: Pfeiltasten, Tab und Enter. Esc bricht einen Schritt ab." 20

    local choice default="grund"
    installed && default="status"
    while true; do
        choice="$(wt --default-item "$default" --menu "Was möchtest du tun?" 22 76 11 \
            "grund"    "Grundeinrichtung (ohne externe Festplatte)" \
            "ausbau1"  "Ausbau 1: Festplatte als Speicher" \
            "ausbau2"  "Ausbau 2: zweite Festplatte als Backup" \
            "install"  "  HimbeerePi installieren / aktualisieren" \
            "benutzer" "  Benutzer anlegen" \
            "zugang"   "  Zugang (Domain & HTTPS oder Heimnetz)" \
            "firewall" "  Firewall" \
            "extras"   "  Push-Meldungen, Strompreis" \
            "status"   "Übersicht anzeigen" \
            "ende"     "Beenden")" || break
        case "$choice" in
            grund)    run_basic ;;
            ausbau1)  step_storage ;;
            ausbau2)  step_backup ;;
            install)  step_install ;;
            benutzer) step_users ;;
            zugang)   step_access ;;
            firewall) step_firewall ;;
            extras)   step_extras ;;
            status)   step_summary ;;
            *)        break ;;
        esac
        default="status"
    done
    clear
    echo "HimbeerePi – Stand der Einrichtung"
    echo
    summary_text
    echo
    echo "Assistent erneut starten: sudo $INSTALL_DIR/deploy/setup.sh"
    log "Assistent beendet"
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
    main "$@"
fi
