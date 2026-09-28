# Installationsanleitung

Diese Anleitung führt Schritt für Schritt von einem leeren Raspberry Pi zu einer laufenden RaspiCloud. Du brauchst keine Linux-Vorkenntnisse, nur die Bereitschaft, Befehle abzutippen.

Die Einrichtung geht **in Stufen**. Du kannst klein anfangen und später ausbauen, ohne neu zu installieren:

| Stufe | Was du brauchst | Wo liegen die Dateien? | Schutz bei Defekt |
|---|---|---|---|
| **Grundeinrichtung** | nur den Pi mit SD-Karte | auf der SD-Karte | nur die tägliche Datenbank-Sicherung auf derselben SD-Karte |
| **Ausbau 1: Speicher-Festplatte** | + eine USB-Festplatte oder SSD | auf der Festplatte (die Dateien ziehen automatisch um) | wie oben, aber viel mehr Platz, und die SD-Karte wird geschont |
| **Ausbau 2: Backup-Festplatte** | + eine zweite Festplatte | auf der Speicher-Festplatte | jede Nacht Datenbank-Kopie, jeden Sonntag Spiegelung **aller Dateien** auf die zweite Platte |

> Die Grundeinrichtung eignet sich zum Ausprobieren und für wenige Daten. SD-Karten sind klein und nutzen sich bei vielen Schreibvorgängen ab – für den Dauerbetrieb Ausbau 1 und 2 einplanen.

**Inhalt**

- **Vorbereitung:** [1. Was du brauchst](#1-was-du-brauchst) · [2. Raspberry Pi OS installieren](#2-raspberry-pi-os-installieren) · [3. Per SSH verbinden](#3-per-ssh-verbinden)
- **[Einrichtungsassistent (empfohlen)](#einrichtungsassistent-empfohlen)** – führt mit Menüs durch alle Stufen
- **Grundeinrichtung von Hand:** [4. Installieren](#4-raspicloud-installieren) · [5. Erster Benutzer](#5-ersten-benutzer-anlegen) · [6. Im Heimnetz testen](#6-im-heimnetz-testen-optional) · [7. Domain](#7-domain-einrichten) · [8. Router](#8-router-ports-freigeben) · [9. HTTPS mit Caddy](#9-https-mit-caddy) · [10. Firewall](#10-firewall) · [11. Zwei-Faktor-Anmeldung](#11-zwei-faktor-anmeldung-einrichten)
- **Ausbau von Hand:** [12. Ausbau 1: Speicher-Festplatte](#12-ausbau-1-speicher-festplatte) · [13. Ausbau 2: Backup-Festplatte](#13-ausbau-2-backup-festplatte)
- **Betrieb:** [14. Datensicherung und Wiederherstellung](#14-datensicherung-und-wiederherstellung) · [15. Aktualisieren](#15-aktualisieren) · [16. Fehlerbehebung](#16-fehlerbehebung)

In den Befehlen steht `<so etwas>` für einen Wert, den du selbst einsetzt, ohne die spitzen Klammern.

---

## 1. Was du brauchst

| Teil | Hinweis |
|---|---|
| Raspberry Pi 5 (empfohlen) oder Pi 4, mindestens 4 GB RAM | Der Pi 5 misst auch die eigene Stromaufnahme |
| Offizielles Netzteil | Zu schwache Netzteile verursachen Abstürze und Datenfehler |
| microSD-Karte, mindestens 32 GB | Am besten eine „High Endurance“-Karte. In der Grundeinrichtung liegen hier auch deine Dateien |
| Netzwerkkabel | WLAN geht, Kabel ist bei großen Uploads deutlich schneller und stabiler |
| Ein zweiter Rechner | Zum Einrichten (Windows, macOS oder Linux) |
| Optional: eigene Domain oder DynDNS-Adresse | Nur nötig, wenn die Cloud auch von unterwegs erreichbar sein soll |
| Für Ausbau 1: USB-Festplatte oder SSD | Große 2,5-Zoll-Platten brauchen evtl. einen USB-Hub mit eigenem Netzteil |
| Für Ausbau 2: eine zweite Festplatte | Mindestens so groß wie die Speicher-Platte |

---

## 2. Raspberry Pi OS installieren

1. Den **Raspberry Pi Imager** von [raspberrypi.com/software](https://www.raspberrypi.com/software/) auf deinem Rechner installieren und starten.
2. Auswählen:
   - *Raspberry-Pi-Modell*: dein Modell
   - *Betriebssystem*: **Raspberry Pi OS (other) → Raspberry Pi OS Lite (64-bit)** (ohne Desktop, spart Speicher)
   - *SD-Karte*: deine microSD-Karte
3. Auf **Weiter → Einstellungen bearbeiten** klicken und festlegen:
   - *Hostname*: z. B. `raspicloud`
   - *Benutzername und Passwort*: frei wählen, gut merken
   - *WLAN*: nur falls kein Kabel
   - *Gebietsschema*: Zeitzone `Europe/Berlin`, Tastatur `de`
   - Reiter *Dienste*: **SSH aktivieren** (Passwort-Anmeldung)
4. Schreiben lassen, SD-Karte in den Pi stecken, Netzwerkkabel anschließen, Strom an.

Nach 1–2 Minuten ist der Pi im Netz.

---

## 3. Per SSH verbinden

Auf deinem Rechner ein Terminal öffnen (Windows: *PowerShell*, macOS: *Terminal*):

```bash
ssh <benutzername>@raspicloud.local
```

Die Frage nach dem Fingerabdruck mit `yes` bestätigen, dann das Passwort eingeben. Klappt `raspicloud.local` nicht, im Router (Liste der verbundenen Geräte) die IP-Adresse des Pi nachsehen und `ssh <benutzername>@<ip-adresse>` verwenden.

Zuerst das System aktualisieren:

```bash
sudo apt update && sudo apt full-upgrade -y
sudo reboot
```

Nach dem Neustart wieder per SSH verbinden.

> **Tipp:** Gib dem Pi im Router eine **feste IP-Adresse** (oft „DHCP-Reservierung“ oder „immer dieselbe IPv4-Adresse zuweisen“). Das brauchst du später für die Portfreigabe.

---

## Einrichtungsassistent (empfohlen)

Ein Assistent führt dich mit Menüs durch alle Stufen:

```bash
sudo apt install -y git
sudo git clone https://github.com/GhostBeacon/raspicloud.git /opt/raspicloud
sudo /opt/raspicloud/deploy/setup.sh
```

Bedienung mit Pfeiltasten, Tab und Enter; Esc bricht den aktuellen Schritt ab.

| Menüpunkt | Was passiert |
|---|---|
| **Grundeinrichtung** | installiert RaspiCloud (Dateien auf der SD-Karte), legt dein Konto und auf Wunsch weitere an, richtet den **Zugang** ein – **Domain mit HTTPS** (prüft den DNS-Eintrag, erinnert an die Portfreigabe, richtet Caddy ein und wartet auf das Zertifikat) oder **nur im Heimnetz** unter `http://<ip-des-pi>` – sowie die **Firewall** (SSH nur aus dem Heimnetz und von deiner aktuellen Verbindung, damit du dich nicht aussperrst). Optional: Push-Meldungen und Strompreis |
| **Ausbau 1: Festplatte als Speicher** | zeigt die angeschlossenen Platten (nie die SD-Karte), formatiert auf Wunsch – nur nach Eintippen des Plattennamens – oder nimmt eine vorhandene ext4-Platte. Dann **zieht er alle Dateien um**: Cloud kurz anhalten, auf die Platte kopieren, mit Prüfsummen vergleichen, Platte an derselben Stelle einhängen, Cloud starten. Geht dabei etwas schief, wird alles automatisch zurückgenommen. Die alte Kopie auf der SD-Karte bleibt, bis du sie löschst |
| **Ausbau 2: zweite Festplatte als Backup** | richtet die Backup-Platte ein: jede Nacht eine Kopie der Datenbank, jeden Sonntag eine Spiegelung aller Dateien. Die Speicher-Platte kann dabei nicht versehentlich ausgewählt werden |

Zwei Dinge musst du für den Zugang von unterwegs weiterhin selbst erledigen; der Assistent sagt dir, wann, und prüft sie: den **DNS-Eintrag** bei deinem Domain- oder DynDNS-Anbieter ([Schritt 7](#7-domain-einrichten)) und die **Portfreigabe im Router** ([Schritt 8](#8-router-ports-freigeben)).

Du kannst den Assistenten **jederzeit erneut starten**, um auszubauen oder etwas zu ändern (z. B. von „Heimnetz“ auf „Domain“ umzustellen oder Benutzer hinzuzufügen). Unter „Übersicht“ zeigt er den aktuellen Stand. Protokoll: `/var/log/raspicloud-setup.log`.

Danach nur noch: [Zwei-Faktor-Anmeldung einrichten](#11-zwei-faktor-anmeldung-einrichten). Die folgenden Abschnitte beschreiben alles noch einmal von Hand und helfen bei der [Fehlerbehebung](#16-fehlerbehebung).

---

## 4. RaspiCloud installieren

```bash
sudo apt install -y git
sudo git clone https://github.com/GhostBeacon/raspicloud.git /opt/raspicloud
sudo /opt/raspicloud/deploy/install.sh
```

Das Skript läuft ein paar Minuten und erledigt:

- benötigte Pakete (Python, SQLite, Werkzeuge für PDF-, RAW- und EXIF-Vorschauen)
- den Dienstbenutzer `raspicloud` ohne Login-Shell
- die Einstellungsdatei `/etc/raspicloud/raspicloud.env` mit einem zufälligen geheimen Schlüssel
- die Python-Umgebung unter `/opt/raspicloud/venv`
- die Datenbank unter `/var/lib/raspicloud/users.db`
- den Datenordner `/srv/raspicloud` (zunächst auf der SD-Karte)
- den systemd-Dienst `raspicloud` (startet automatisch beim Hochfahren)
- die Cronjobs: tägliches Datenbank-Backup, Stromschätzung, Gesundheitsprüfung

Am Ende sollte stehen: `Fertig: RaspiCloud laeuft auf 127.0.0.1:5000`.

---

## 5. Ersten Benutzer anlegen

```bash
cd /opt/raspicloud/app
sudo -u raspicloud ../venv/bin/python3 manage.py add-user
```

Abgefragt werden:

| Frage | Empfehlung |
|---|---|
| Benutzername | z. B. dein Vorname in Kleinbuchstaben |
| Passwort | mindestens 12 Zeichen, am besten aus einem Passwortmanager |
| Speicherlimit in GB | für dich selbst leer lassen (kein Limit, du siehst den ganzen Speicher); für weitere Personen z. B. `500` |
| Anzeigename | optional |
| Pi-Status anzeigen | für Administratoren `J`, für andere `n` |

Weitere Benutzer legst du genauso an. Übersicht: `manage.py list-users`.

---

## 6. Im Heimnetz testen (optional)

Die Cloud hört aus Sicherheitsgründen nur auf den Pi selbst (`127.0.0.1`). Zum kurzen Testen ohne Domain kannst du sie von deinem Rechner aus über SSH „durchreichen“:

```bash
ssh -L 5000:127.0.0.1:5000 <benutzername>@raspicloud.local
```

Solange diese Verbindung offen ist, öffnet `http://localhost:5000` die Cloud. Springt die Anmeldung dabei immer wieder zum Login zurück (weil das kein HTTPS ist), hilft vorübergehend ein Test-Schalter:

```bash
echo 'RASPICLOUD_INSECURE_COOKIE=1' | sudo tee -a /etc/raspicloud/raspicloud.env
sudo systemctl restart raspicloud
```

> **Wichtig:** Diese Zeile nach dem Test wieder aus `/etc/raspicloud/raspicloud.env` löschen (`sudo nano /etc/raspicloud/raspicloud.env`) und den Dienst neu starten, bevor die Cloud von außen erreichbar ist.

Dauerhaft nur im Heimnetz (ohne Domain, ohne Verschlüsselung) richtet der [Einrichtungsassistent](#einrichtungsassistent-empfohlen) unter „Zugang → Nur im Heimnetz“ ein.

---

## 7. Domain einrichten

Damit du die Cloud von unterwegs erreichst, braucht dein Anschluss einen Namen, z. B. `cloud.meinname.de`.

- **Eigene Domain:** beim Anbieter einen **A-Eintrag** für `cloud` auf deine öffentliche IPv4-Adresse setzen (herausfinden z. B. mit `curl -4 ifconfig.me` auf dem Pi).
- **Wechselnde IP-Adresse** (bei den meisten Privatanschlüssen): einen DynDNS-Dienst nutzen, z. B. [deSEC](https://desec.io), [DuckDNS](https://www.duckdns.org) oder die DynDNS-Funktion deines Routers (bei der FRITZ!Box unter *Internet → Freigaben → DynDNS*). Der Router meldet die neue IP dann selbst.

Prüfen (auf deinem Rechner):

```bash
nslookup cloud.<deine-domain>
```

Die angezeigte Adresse muss deine öffentliche IP sein.

> **DS-Lite / nur IPv6:** Manche Anschlüsse (oft Kabel oder Glasfaser) haben keine eigene öffentliche IPv4-Adresse. Dann ist der Pi von außen per IPv4 nicht erreichbar. Frag beim Anbieter nach einer echten IPv4-Adresse („Dual Stack“) oder nutze ein VPN wie WireGuard statt einer Portfreigabe.

---

## 8. Router: Ports freigeben

Im Router eine **Portfreigabe** (Portweiterleitung) für den Pi anlegen:

| Port | Protokoll | Ziel |
|---|---|---|
| 80 | TCP | Pi, Port 80 (nur für das Zertifikat von Let's Encrypt) |
| 443 | TCP | Pi, Port 443 (HTTPS) |

Bei der FRITZ!Box: *Internet → Freigaben → Portfreigaben → Gerät für Freigaben hinzufügen*, den Pi auswählen und die beiden Ports eintragen.

> Gib **nur** diese beiden Ports frei, niemals Port 22 (SSH) oder 5000.

---

## 9. HTTPS mit Caddy

[Caddy](https://caddyserver.com) nimmt die Anfragen von außen an, holt automatisch ein kostenloses Zertifikat bei Let's Encrypt und reicht sie an die Cloud weiter.

```bash
sudo apt install -y caddy
sudo cp /opt/raspicloud/deploy/Caddyfile.example /etc/caddy/Caddyfile
sudo nano /etc/caddy/Caddyfile
```

`cloud.example.org` durch deine Adresse ersetzen, speichern (`Strg+O`, `Enter`) und schließen (`Strg+X`). Dann:

```bash
sudo systemctl reload caddy
sudo journalctl -u caddy -n 30 --no-pager      # sollte "certificate obtained successfully" zeigen
```

Jetzt `https://cloud.<deine-domain>` im Browser öffnen und anmelden.

---

## 10. Firewall

Die Firewall lässt nur SSH aus dem Heimnetz und HTTP/HTTPS herein. **Zuerst** SSH erlauben, sonst sperrst du dich aus:

```bash
sudo apt install -y ufw
sudo ufw allow from 192.168.0.0/16 to any port 22 proto tcp   # SSH nur aus dem Heimnetz
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
sudo ufw status
```

Nutzt dein Heimnetz einen anderen Bereich (z. B. `10.0.0.x`), die erste Regel entsprechend anpassen.

---

## 11. Zwei-Faktor-Anmeldung einrichten

Jeder Benutzer sollte das direkt nach der ersten Anmeldung tun:

1. Oben auf **2FA** klicken.
2. Den QR-Code mit einer Authenticator-App scannen (z. B. Aegis, Google Authenticator, Microsoft Authenticator, 2FAS oder den Passwortmanager).
3. Den angezeigten 6-stelligen Code eingeben.

Ab jetzt fragt die Cloud nach dem Passwort zusätzlich nach dem Code.

**Handy verloren?** Auf dem Pi:

```bash
cd /opt/raspicloud/app
sudo -u raspicloud ../venv/bin/python3 emergency_disable_2fa.py
```

Damit ist die **Grundeinrichtung** fertig.

---

## 12. Ausbau 1: Speicher-Festplatte

Die Dateien ziehen von der SD-Karte auf eine Festplatte um. Der Pfad `/srv/raspicloud` bleibt gleich – die Platte wird genau dort eingehängt, sodass keine Einstellung geändert werden muss. **Am einfachsten und sichersten mit dem Assistenten** (`sudo /opt/raspicloud/deploy/setup.sh` → „Ausbau 1“): Er prüft Platz und Kopie und nimmt bei Problemen alles automatisch zurück.

Von Hand:

> ⚠️ **Achtung:** Das Formatieren löscht **alles** auf der Platte. Prüfe genau, welches Gerät du erwischst, damit du nicht aus Versehen die SD-Karte formatierst.

**1. Platte finden und formatieren.** Die SD-Karte heißt `mmcblk0`, die USB-Platte meist `sda` (bei dir kann es anders sein):

```bash
lsblk -o NAME,SIZE,TYPE,MOUNTPOINT,MODEL
sudo apt install -y parted rsync
sudo parted /dev/sda --script mklabel gpt mkpart raspicloud ext4 0% 100%
sudo mkfs.ext4 -L raspicloud /dev/sda1
```

**2. Dateien kopieren und prüfen** (Cloud dafür anhalten):

```bash
sudo systemctl stop raspicloud
sudo mkdir -p /mnt/raspicloud-umzug
sudo mount /dev/sda1 /mnt/raspicloud-umzug
sudo rsync -aHAX /srv/raspicloud/ /mnt/raspicloud-umzug/
sudo rsync -a --checksum --dry-run --itemize-changes /srv/raspicloud/ /mnt/raspicloud-umzug/   # darf nichts ausgeben
sudo umount /mnt/raspicloud-umzug
```

**3. Umschalten:** alten Ordner beiseitelegen, Platte an seiner Stelle dauerhaft einhängen:

```bash
sudo mv /srv/raspicloud /srv/raspicloud.sd-kopie
sudo mkdir /srv/raspicloud
sudo blkid /dev/sda1                 # zeigt UUID="…", diese Zeichenkette kopieren
echo 'UUID=<die-uuid>  /srv/raspicloud  ext4  defaults,nofail  0  2' | sudo tee -a /etc/fstab
sudo systemctl daemon-reload
sudo mount /srv/raspicloud
sudo chown raspicloud:raspicloud /srv/raspicloud
echo 'RASPICLOUD_HDD_WATTS=7' | sudo tee -a /etc/raspicloud/raspicloud.env     # für die Stromschätzung
sudo systemctl start raspicloud
```

`nofail` sorgt dafür, dass der Pi auch ohne angeschlossene Platte noch startet.

**4. Prüfen** (Dateien in der Cloud sichtbar, Speicherplatz zeigt die Platte). Wenn alles passt, die alte Kopie löschen: `sudo rm -rf /srv/raspicloud.sd-kopie`.

> Eine Platte mit NTFS oder exFAT (von Windows/macOS) kann keine Linux-Rechte speichern und ist langsamer. ext4 verwenden.

---

## 13. Ausbau 2: Backup-Festplatte

Eine zweite Platte bekommt jede Nacht eine Kopie der Datenbank und jeden Sonntag eine Spiegelung aller Dateien. **Mit dem Assistenten:** `sudo /opt/raspicloud/deploy/setup.sh` → „Ausbau 2“.

Von Hand (Platte wie in Schritt 12 formatieren, hier `sdb` mit Label `raspicloud-bak`):

```bash
sudo mkdir -p /srv/raspicloud-backup
sudo blkid /dev/sdb1
echo 'UUID=<die-uuid>  /srv/raspicloud-backup  ext4  defaults,nofail  0  2' | sudo tee -a /etc/fstab
sudo systemctl daemon-reload
sudo mount /srv/raspicloud-backup
sudo install -d -o raspicloud -g raspicloud /srv/raspicloud-backup/db_backups
sudo nano /etc/raspicloud/raspicloud.env
#   RASPICLOUD_BACKUP_MOUNT=/srv/raspicloud-backup   (Raute davor entfernen)
#   RASPICLOUD_HDD_WATTS=14                          (zwei Platten)
sudo systemctl restart raspicloud
```

Wöchentliche Spiegelung aller Dateien (sonntags 2 Uhr):

```bash
echo '0 2 * * 0 root mountpoint -q /srv/raspicloud-backup && rsync -a --delete --exclude=/lost+found --exclude=/tmp_uploads /srv/raspicloud/ /srv/raspicloud-backup/dateien/' | sudo tee /etc/cron.d/raspicloud-mirror
```

Am besten liegt eine weitere Kopie außer Haus, zum Beispiel eine Platte, die du ab und zu tauschst.

---

## 14. Datensicherung und Wiederherstellung

| | Grundeinrichtung | + Ausbau 1 | + Ausbau 2 |
|---|---|---|---|
| Datenbank (Benutzer, Ordner, Dateiliste) | täglich 3:10 Uhr nach `/srv/raspicloud/backups`, 14 Tage | ebenso, auf der Speicher-Platte | zusätzlich auf der Backup-Platte |
| Hochgeladene Dateien | **keine Sicherung** | **keine Sicherung** | sonntags gespiegelt nach `/srv/raspicloud-backup/dateien` |

Jede Datenbank-Sicherung wird auf Fehler geprüft. Den Status siehst du in der Weboberfläche unter *Pi-Status → Backup-Status*.

**Push-Meldung zum Backup (optional):** im Assistenten unter „Push-Meldungen“, oder von Hand: App [ntfy](https://ntfy.sh) installieren, einen zufälligen Kanalnamen abonnieren (z. B. Ausgabe von `openssl rand -hex 16`) und diesen als `NTFY_TOPIC=` in `/etc/raspicloud/raspicloud.env` eintragen.

**Datenbank wiederherstellen:**

```bash
sudo systemctl stop raspicloud
sudo cp /srv/raspicloud/backups/users_db_<datum>.db /var/lib/raspicloud/users.db
sudo rm -f /var/lib/raspicloud/users.db-wal /var/lib/raspicloud/users.db-shm
sudo chown raspicloud:raspicloud /var/lib/raspicloud/users.db
sudo systemctl start raspicloud
```

**Speicher-Platte defekt (mit Ausbau 2):** neue Platte über den Assistenten als Ausbau 1 einrichten, dann die Spiegelung zurückkopieren:

```bash
sudo systemctl stop raspicloud
sudo rsync -a /srv/raspicloud-backup/dateien/ /srv/raspicloud/
sudo cp "$(ls -t /srv/raspicloud-backup/db_backups/users_db_*.db | head -n 1)" /var/lib/raspicloud/users.db
sudo rm -f /var/lib/raspicloud/users.db-wal /var/lib/raspicloud/users.db-shm
sudo chown -R raspicloud:raspicloud /srv/raspicloud /var/lib/raspicloud
sudo systemctl start raspicloud
```

Dateien, die nach der letzten Spiegelung hochgeladen wurden, fehlen dann.

---

## 15. Aktualisieren

```bash
cd /opt/raspicloud && sudo git pull
sudo /opt/raspicloud/deploy/install.sh
```

Einstellungen, Datenbank und Dateien bleiben erhalten (geht auch über den Assistenten: „RaspiCloud installieren / aktualisieren“). Das Betriebssystem hältst du mit `sudo apt update && sudo apt full-upgrade` aktuell. Das geht auch automatisch mit dem Paket `unattended-upgrades`.

---

## 16. Fehlerbehebung

| Problem | Lösung |
|---|---|
| Seite nicht erreichbar (von außen) | Im Heimnetz testen (Schritt 6). Geht es dort: DNS (Schritt 7), Portfreigabe (Schritt 8), `sudo journalctl -u caddy -n 50` prüfen |
| Caddy bekommt kein Zertifikat | Port 80 muss frei und freigegeben sein; die Domain muss auf deine aktuelle IP zeigen; bei DS-Lite siehe Hinweis in Schritt 7 |
| „502 Bad Gateway“ | Die Cloud läuft nicht: `sudo systemctl status raspicloud` und `sudo journalctl -u raspicloud -n 50` |
| Anmeldung springt immer zurück zum Login | Aufruf über `http://` statt `https://`: HTTPS verwenden (bzw. nur zum Testen Schritt 6) |
| „Zu viele fehlgeschlagene Anmeldeversuche“ | 15 Minuten warten; Passwort vergessen: `reset_password.py` (siehe README) |
| Nach Ausbau 1 zeigt der Speicherplatz die SD-Karte, Dateien fehlen | Die Platte ist nicht eingehängt (z. B. nicht angeschlossen beim Start): `findmnt /srv/raspicloud`, dann `sudo systemctl stop raspicloud && sudo mount /srv/raspicloud && sudo systemctl start raspicloud` |
| SD-Karte voll (Grundeinrichtung) | Ausbau 1 einrichten, oder alte Dateien und den Papierkorb leeren |
| Dienst startet nicht: `KeyError: 'SECRET_KEY'` | `/etc/raspicloud/raspicloud.env` fehlt oder `SECRET_KEY=` ist leer: `install.sh` erneut ausführen |
| Keine Vorschaubilder für PDF/RAW | `sudo apt install poppler-utils libimage-exiftool-perl dcraw` (macht `install.sh` normalerweise selbst) |
| Upload bricht bei großen Dateien ab | Prüfen, ob der Speicher voll ist (`df -h /srv/raspicloud`); über WLAN wenn möglich per Kabel verbinden |
| Temperatur / Leistung „n/a“ | Auf anderen Rechnern als dem Raspberry Pi fehlt `vcgencmd`; auf dem Pi: `sudo usermod -aG video raspicloud` und Dienst neu starten |

Nützliche Befehle:

```bash
sudo systemctl status raspicloud        # läuft der Dienst?
sudo systemctl restart raspicloud       # neu starten
sudo journalctl -u raspicloud -f        # Log live mitlesen (Strg+C beendet)
tail /srv/raspicloud/backups/backup_db.log
tail /var/log/raspicloud-setup.log      # Protokoll des Einrichtungsassistenten
```
