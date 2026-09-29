# Mitmachen

Danke, dass du HimbeerePi verbessern möchtest! Fehlerberichte, Ideen und Pull Requests sind willkommen.

## Fehler melden und Ideen vorschlagen

- **Fehler:** Issue mit der Vorlage „Fehler melden“ anlegen. Hilfreich sind Pi-Modell, Betriebssystem, HimbeerePi-Version (`cat /opt/himbeerepi/VERSION`) und die letzten Zeilen aus `sudo journalctl -u himbeerepi -n 50`.
- **Idee:** Issue mit der Vorlage „Idee / Wunsch“ anlegen. Beschreibe, was du erreichen möchtest, nicht nur die Lösung.
- **Sicherheitslücken nie als öffentliches Issue**, sondern privat über *Security → Report a vulnerability* (siehe [SECURITY.md](SECURITY.md)).

Bitte vor dem Posten Protokolle auf persönliche Daten prüfen: Domain, IP-Adressen, Benutzernamen, ntfy-Topic und `SECRET_KEY` unkenntlich machen.

## Entwickeln

Ohne Pi, auf jedem Linux- oder macOS-Rechner:

```bash
python3 -m venv venv && venv/bin/pip install -r app/requirements.txt
export SECRET_KEY=test HIMBEEREPI_INSECURE_COOKIE=1 HIMBEEREPI_DB=$PWD/test.db HIMBEEREPI_DATA_DIR=$PWD/testdata
venv/bin/python3 app/manage.py add-user
cd app && ../venv/bin/flask --app app run      # http://127.0.0.1:5000
```

Vor einem Pull Request bitte dieselben Prüfungen laufen lassen wie die automatische Prüfung auf GitHub:

```bash
python3 -m py_compile app/*.py
bash -n scripts/*.sh deploy/*.sh
shellcheck -S warning scripts/*.sh deploy/*.sh
HIMBEEREPI_INSECURE_COOKIE=1 venv/bin/python -m unittest discover -s tests
```

## Pull Requests

- Eine Änderung pro Pull Request, mit kurzer Beschreibung, was und warum.
- Neue Funktionen und Fehlerbehebungen möglichst mit Test in `tests/`.
- Stil wie im umgebenden Code: Oberfläche, Meldungen und Kommentare auf Deutsch; Kommentare meist ohne Umlaute (ae, oe, ue), Texte für Benutzer mit Umlauten.
- Jede Datenbankabfrage auf den angemeldeten Benutzer beschränken (`user_id = ?`), Formulare mit CSRF-Token.
- Änderungen am Einrichtungsassistenten (`deploy/setup.sh`) dürfen bestehende Installationen nicht beschädigen; Festplatten nur nach ausdrücklicher Bestätigung formatieren.
- Keine persönlichen Daten, Domains, Zugangsdaten oder Datenbanken committen.
- Grafiken werden mit `tools/make_icons.py` erzeugt, nicht von Hand bearbeitet.

## Neue Version veröffentlichen (Maintainer)

Die Nummer in der Datei `VERSION` erhöhen und auf `main` pushen: `1.1.1` für Fehlerkorrekturen, `1.2.0` für neue Funktionen, `2.0.0` für große Umstellungen, mit Zusatz wie `1.2.0-beta.1` für eine Vorabversion. Der Workflow `.github/workflows/release.yml` lässt dann die Tests laufen und legt bei Erfolg Tag und Release mit den Änderungen seit der letzten Version an. Die Release-Beschreibung lässt sich danach auf GitHub noch ergänzen.

Mit einem Pull Request stimmst du zu, dass dein Beitrag unter der [MIT-Lizenz](LICENSE) des Projekts veröffentlicht wird.
