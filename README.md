# HimbeerePi

Eine schlanke, selbst gehostete Cloud für Dateien und Fotos auf dem **Raspberry Pi**: Weboberfläche für Computer und Handy, mit Zwei-Faktor-Anmeldung. Die Daten bleiben bei dir: zum Start auf der SD-Karte, später auf einer eigenen Festplatte am Pi.

Gebaut mit Flask, Gunicorn und SQLite. Kein Docker, keine Datenbank-Server, kein Konto bei Dritten nötig.

## Funktionen

- **Dateien und Ordner:** hochladen (auch sehr große Mengen, mit Warteschlange und Fortschritt), Ordner anlegen, umbenennen, verschieben, Mehrfachauswahl, ZIP-Download, ZIP-Import mit Ordnerstruktur
- **Suche** über alle eigenen Dateien und Ordner (Taste `/` oder `Strg/Cmd+K`), Sortierung nach Name, Datum, Typ und Größe, Listen- und Galerieansicht
- **Vorschaubilder und Bildbetrachter** für JPG, PNG, WEBP, GIF, **HEIC** (iPhone), **RAW** (Canon CR2/CR3) und **PDF**; Bilddetails (EXIF)
- **Papierkorb:** Wiederherstellen, endgültiges Leeren nur nach Eintippen von `LOESCHEN`
- **Mehrere Benutzer**, jeder sieht nur die eigenen Dateien; optional mit Speicherlimit
- **Zwei-Faktor-Anmeldung** (TOTP, jede Authenticator-App)
- **Mobile Ansicht** im Browser, speziell für das Handy
- **Pi-Status** in der Seitenleiste: CPU, RAM, Temperatur, Drosselung, Durchsatz, Plattenbelegung, geschätzte Stromkosten, Status des Datenbank-Backups
- **Tägliches Datenbank-Backup** mit Integritätsprüfung; mit Backup-Festplatte zusätzlich wöchentliche Spiegelung aller Dateien; optional Push-Meldung über [ntfy](https://ntfy.sh)
- Schlichte, moderne Oberfläche mit Hell- und Dunkelmodus

## Voraussetzungen

- Raspberry Pi 4 oder 5 mit **Raspberry Pi OS (Bookworm, 64 Bit)**. Die Leistung misst nur der Pi 5 direkt, auf dem Pi 4 wird sie geschätzt. Andere Debian-Systeme gehen auch, dann fehlen Leistungs- und Drosselwerte.
- **Keine Festplatte nötig zum Start:** In der Grundeinrichtung liegen die Dateien auf der SD-Karte. Später lässt sich ausbauen:

  | Stufe | Speicher | Sicherung der Dateien |
  |---|---|---|
  | Grundeinrichtung | SD-Karte | – |
  | Ausbau 1 | USB-Festplatte oder SSD (Dateien ziehen automatisch um) | – |
  | Ausbau 2 | wie Ausbau 1 | zweite Festplatte, wöchentliche Spiegelung |
- Für den Zugriff von unterwegs: eine **Domain** (auch DynDNS), Portfreigabe 80 und 443 im Router und [Caddy](https://caddyserver.com) als HTTPS-Proxy. Alternativ nur im Heimnetz oder über ein VPN betreiben.

## Einrichtung

**Am einfachsten mit dem Einrichtungsassistenten.** Er führt mit Menüs durch die Grundeinrichtung (Installation, Benutzer, Domain mit HTTPS oder Heimnetz, Firewall) und später durch die Ausbaustufen (Speicher-Festplatte mit automatischem Umzug der Dateien, Backup-Festplatte):

```bash
sudo apt install -y git
sudo git clone https://github.com/GhostBeacon/himbeercloud.git /opt/himbeerepi
sudo /opt/himbeerepi/deploy/setup.sh
```

**Ausführliche Schritt-für-Schritt-Anleitung** vom leeren Pi bis zum Zugriff von unterwegs, mit allen Stufen auch von Hand (Domain, Router, HTTPS, Firewall, Festplatten, Sicherung, Fehlerbehebung): **[INSTALL.md](INSTALL.md)**. Hier die Kurzfassung der Grundeinrichtung von Hand:

### 1. Installieren

```bash
sudo apt install -y git
sudo git clone https://github.com/GhostBeacon/himbeercloud.git /opt/himbeerepi
sudo /opt/himbeerepi/deploy/install.sh
```

Das Skript installiert die Pakete, legt den Dienstbenutzer `himbeerepi` an, erzeugt `/etc/himbeerepi/himbeerepi.env` mit einem zufälligen `SECRET_KEY`, richtet die Python-Umgebung, die Datenbank, den systemd-Dienst und die Cronjobs ein. Danach läuft die Cloud auf `127.0.0.1:5000`, die Dateien liegen unter `/srv/himbeerepi`.

### 2. Ersten Benutzer anlegen

```bash
cd /opt/himbeerepi/app
sudo -u himbeerepi ../venv/bin/python3 manage.py add-user
```

Benutzer ohne Speicherlimit sehen die Belegung des ganzen Speichers, Benutzer mit Limit nur ihr eigenes Kontingent. 2FA richtet jeder nach der Anmeldung selbst in der Weboberfläche ein.

### 3. HTTPS mit Caddy

```bash
sudo apt install -y caddy
sudo cp /opt/himbeerepi/deploy/Caddyfile.example /etc/caddy/Caddyfile
sudo nano /etc/caddy/Caddyfile               # cloud.example.org durch die eigene Domain ersetzen
sudo systemctl reload caddy
```

Caddy holt das Zertifikat bei Let's Encrypt automatisch. Die Cloud selbst lauscht nur auf `127.0.0.1` und ist nie direkt erreichbar.

Nur im Heimnetz ohne Domain und HTTPS: in `/etc/himbeerepi/himbeerepi.env` die Zeile `HIMBEEREPI_INSECURE_COOKIE=1` ergänzen, sonst funktioniert die Anmeldung über `http://` nicht. Von außen erreichbar sollte die Cloud so nicht sein.

### 4. Einstellungen

Alles steht in `/etc/himbeerepi/himbeerepi.env` (Vorlage: [`deploy/himbeerepi.env.example`](deploy/himbeerepi.env.example)). Nach einer Änderung: `sudo systemctl restart himbeerepi`.

| Einstellung | Bedeutung |
|---|---|
| `SECRET_KEY` | Pflicht, geheimer Schlüssel für die Sitzungen (erzeugt `install.sh`) |
| `HIMBEEREPI_DATA_DIR` | Ordner für die Dateien, Standard `/srv/himbeerepi` (mit Ausbau 1 ist dort die Festplatte eingehängt) |
| `HIMBEEREPI_DB` | Benutzer-Datenbank, Standard `/var/lib/himbeerepi/users.db` |
| `HIMBEEREPI_BACKUP_MOUNT` | Ausbau 2: Backup-Platte, Standard `/srv/himbeerepi-backup` |
| `HIMBEEREPI_POWER_PRICE` | Strompreis in EUR/kWh für die Kostenanzeige (Standard 0,35) |
| `HIMBEEREPI_HDD_WATTS` | pauschale Leistung externer Festplatten in Watt (0 ohne Platte, der Assistent setzt 7 je Platte) |
| `NTFY_TOPIC`, `NTFY_SERVER` | optional: Push-Meldung nach jedem Backup |

## Aktualisieren

```bash
cd /opt/himbeerepi && sudo git pull
sudo /opt/himbeerepi/deploy/install.sh
```

Einstellungen, Datenbank und Dateien bleiben dabei erhalten.

## Verwaltung auf dem Pi

Alle Befehle im Ordner `/opt/himbeerepi/app`:

| Befehl | Zweck |
|---|---|
| `sudo -u himbeerepi ../venv/bin/python3 manage.py add-user` | Benutzer anlegen |
| `sudo -u himbeerepi ../venv/bin/python3 manage.py list-users` | Benutzer auflisten |
| `sudo -u himbeerepi ../venv/bin/python3 reset_password.py` | Passwort neu setzen |
| `sudo -u himbeerepi ../venv/bin/python3 emergency_disable_2fa.py` | 2FA abschalten, wenn das Handy weg ist |
| `sudo -u himbeerepi ../venv/bin/python3 empty_trash_all.py` | Papierkorb aller Benutzer leeren |
| `bulk_import.py` | große Datenmengen direkt vom Pi importieren (Anleitung im Kopf der Datei) |
| `journalctl -u himbeerepi -f` | Log des Dienstes |

## Datensicherung

`scripts/backup_db.sh` sichert täglich um 3:10 Uhr die **Datenbank** (Benutzer, Ordner, Dateiliste) nach `/srv/himbeerepi/backups`, prüft die Kopie und hebt 14 Tage auf. Die **hochgeladenen Dateien** werden erst mit **Ausbau 2** gesichert: Dann landet die Datenbank-Kopie zusätzlich auf der Backup-Platte, und jeden Sonntag werden alle Dateien nach `/srv/himbeerepi-backup/dateien` gespiegelt. Wiederherstellen: [INSTALL.md, Abschnitt 14](INSTALL.md#14-datensicherung-und-wiederherstellung).

## Aufbau

```
app/              Flask-Anwendung (Weboberfläche) und Verwaltungsskripte
  app.py          Weboberfläche, Anmeldung, Upload, Vorschaubilder, Pi-Status
  manage.py       Datenbank anlegen, Benutzer verwalten
  templates/      Seiten (Desktop, Mobil, Anmeldung, 2FA)
  static/icons/   Symbole (Favicon, Startbildschirm)
scripts/          Backup, Gesundheitsprüfung, ntfy-Meldung
deploy/           setup.sh (Einrichtungsassistent), install.sh, systemd-Dienst, Cronjobs, Einstellungsvorlage, Caddy-Beispiel
tools/            make_icons.py erzeugt die Browser-Symbole
tests/            Rauchtest (python -m unittest discover -s tests)
```

## Sicherheit

- Anmeldung mit Passwort und optional TOTP; 5 Fehlversuche pro IP sperren 15 Minuten (falsche 2FA-Codes zählen mit)
- CSRF-Schutz, sichere Cookies, Abmeldung nach 12 Stunden Inaktivität
- Jede Datenbankabfrage ist auf den angemeldeten Benutzer beschränkt
- Hochgeladene HTML-, SVG- und ähnliche Dateien werden nur als Download ausgeliefert, nie im Browser ausgeführt
- Der Dienst läuft als eigener Benutzer ohne Login-Shell und darf nur in Datenbank- und Datenordner schreiben

Mehr in [SECURITY.md](SECURITY.md).

## Selbst ausprobieren (ohne Pi)

```bash
python3 -m venv venv && venv/bin/pip install -r app/requirements.txt
export SECRET_KEY=test HIMBEEREPI_INSECURE_COOKIE=1 HIMBEEREPI_DB=$PWD/test.db HIMBEEREPI_DATA_DIR=$PWD/testdata
venv/bin/python3 app/manage.py add-user
cd app && ../venv/bin/flask --app app run      # http://127.0.0.1:5000
```

## Verwendete Software

HimbeerePi enthält selbst keinen fremden Code. Die folgenden Programme und Pakete werden bei der Installation aus den offiziellen Quellen (PyPI bzw. den Paketquellen von Raspberry Pi OS) auf den Pi geladen und stehen unter ihren eigenen Lizenzen:

| Python-Paket | Zweck | Lizenz |
|---|---|---|
| Flask, Werkzeug, Jinja2, click, itsdangerous, MarkupSafe | Web-Anwendung | BSD-3-Clause |
| Flask-WTF, WTForms | Formulare, CSRF-Schutz | BSD-3-Clause |
| Flask-Login | Anmeldung und Sitzungen | MIT |
| gunicorn | Anwendungsserver | MIT |
| blinker | Ereignisse innerhalb von Flask | MIT |
| PyOTP | Zwei-Faktor-Codes (TOTP) | MIT |
| qrcode | QR-Code für die 2FA-Einrichtung | BSD |
| Pillow | Vorschaubilder | MIT-CMU |
| pillow-heif | iPhone-Fotos (HEIC); bringt libheif und libde265 (LGPL-3.0) sowie x265 (GPL-2.0) mit | BSD-3-Clause |
| psutil | Pi-Status (CPU, RAM, Durchsatz) | BSD-3-Clause |

| Systemprogramm | Zweck | Lizenz |
|---|---|---|
| Python, SQLite | Laufzeit, Datenbank | PSF, gemeinfrei |
| poppler-utils | PDF-Vorschau | GPL-2.0 |
| exiftool | Bilddetails, RAW-Vorschau | Artistic / GPL |
| dcraw | RAW-Vorschau (Rückfall) | frei, eigene Lizenz |
| Caddy | HTTPS und Reverse Proxy | Apache-2.0 |
| ufw, parted, rsync, whiptail | Firewall, Festplatten, Spiegelung, Einrichtungsassistent | GPL |

HimbeerePi ruft diese Programme nur auf und gibt sie nicht selbst weiter. Wer HimbeerePi zusammen mit ihnen verteilt (z. B. als fertiges SD-Karten-Image), muss deren Lizenzbedingungen beachten.

## Lizenz

[MIT](LICENSE)
