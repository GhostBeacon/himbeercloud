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
