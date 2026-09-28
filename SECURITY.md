# Sicherheit

## Lücke melden

Bitte Sicherheitslücken **nicht** als öffentliches Issue melden, sondern über *Security → Report a vulnerability* in diesem Repository (private Meldung). Bitte beschreiben, wie sich das Problem nachstellen lässt.

## Was nie ins Repository gehört

- `/etc/himbeerepi/himbeerepi.env` (enthält `SECRET_KEY` und ggf. das ntfy-Topic)
- Datenbanken (`*.db`) und Sicherungen
- echte Domains, IP-Adressen, Benutzernamen

`.gitignore` sperrt die üblichen Dateien, prüfe vor einem Push trotzdem `git status`.

## Empfehlungen für den Betrieb

- 2FA für alle Benutzer einrichten
- Nur Caddy (Port 80/443) nach außen freigeben, SSH nur im Heimnetz oder über ein VPN; zum Beispiel mit `ufw`
- System aktuell halten: `sudo apt update && sudo apt full-upgrade`
- Die hochgeladenen Dateien zusätzlich auf einer zweiten Platte sichern (siehe README)
- Nach Verdacht auf fremden Zugriff: Passwort neu setzen (`reset_password.py`) – das meldet alle Geräte ab. Ohne neues Passwort geht das auch in der Weboberfläche unter *2FA → Alle anderen Geräte abmelden*

## Bekannte Restrisiken

- **Externe Programme lesen hochgeladene Dateien.** Für Vorschaubilder und Bilddetails verarbeiten `exiftool`, `pdftoppm` (poppler) und `dcraw` die hochgeladenen Dateien direkt. Eine gezielt präparierte Datei könnte eine Schwachstelle in diesen Programmen ausnutzen. Sie laufen als eingeschränkter Dienstbenutzer ohne Schreibrechte auf den Programmcode, trotzdem gilt: **System regelmäßig aktualisieren** (`sudo apt update && sudo apt full-upgrade`, oder automatisch mit `unattended-upgrades`). `dcraw` wird nur noch als Rückfall für RAW-Vorschauen genutzt und wird nicht mehr weiterentwickelt.
- **Anmeldesperre pro IP-Adresse.** Nach 5 Fehlversuchen ist eine IP-Adresse 15 Minuten gesperrt. Wer über viele Adressen verfügt, kann weiter raten – deshalb 2FA für alle Benutzer einrichten und lange Passwörter verwenden.
- **Nur im Heimnetz ohne HTTPS** (Einrichtungsassistent: „Nur im Heimnetz“): Passwörter gehen unverschlüsselt durchs lokale Netz. Für den Zugang von unterwegs immer „Domain mit HTTPS“ verwenden.
