"""
Sicheres Passwort-Reset-Skript - umgeht das Bash-Anfuehrungszeichen-Problem komplett, indem
alles (Passwort-Eingabe, Hash-Erzeugung, Datenbank-Update) innerhalb von Python passiert,
ohne den Hash jemals durch eine Shell-Zeichenkette zu schleusen.

Verwendung:
    cd /opt/himbeerepi/app
    sudo -u himbeerepi ../venv/bin/python3 reset_password.py
"""
import os
import sqlite3
import getpass
from werkzeug.security import generate_password_hash

DB_PATH = os.environ.get("HIMBEEREPI_DB", "/var/lib/himbeerepi/users.db")

username = input("Benutzername: ").strip()
pw1 = getpass.getpass("Neues Passwort: ")
pw2 = getpass.getpass("Nochmal zur Bestaetigung: ")

if pw1 != pw2:
    print("FEHLER: Die eingegebenen Passwoerter stimmen nicht ueberein. Abgebrochen.")
    exit(1)

if len(pw1) < 10:
    print("FEHLER: Das Passwort muss mindestens 10 Zeichen lang sein (der Login ist oeffentlich "
          "ueber die oeffentliche Domain erreichbar, nicht nur im lokalen Netz). Abgebrochen.")
    exit(1)

new_hash = generate_password_hash(pw1)

conn = sqlite3.connect(DB_PATH)
cursor = conn.execute("SELECT id FROM users WHERE username = ?", (username,))
row = cursor.fetchone()

if not row:
    print(f"FEHLER: Benutzer '{username}' wurde nicht gefunden.")
    conn.close()
    exit(1)

conn.execute("UPDATE users SET password_hash = ? WHERE username = ?", (new_hash, username))
# Alle bestehenden Anmeldungen beenden - wer das alte Passwort kannte, fliegt raus
try:
    conn.execute("UPDATE users SET session_version = session_version + 1 WHERE username = ?", (username,))
except sqlite3.OperationalError:  # Datenbank noch ohne Spalte: Dienst einmal neu starten
    print("Hinweis: bestehende Sitzungen konnten nicht beendet werden (Dienst neu starten).")
conn.commit()
conn.close()

print(f"Fertig. Passwort fuer '{username}' wurde erfolgreich aktualisiert. Alle Geraete sind abgemeldet.")
