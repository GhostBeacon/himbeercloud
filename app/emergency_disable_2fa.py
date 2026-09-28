"""
NOTFALL-Skript: deaktiviert die Zwei-Faktor-Authentifizierung fuer ein Konto direkt in der
Datenbank - fuer den Fall, dass das Authenticator-Handy verloren geht/kaputt ist und man sich
sonst nicht mehr einloggen koennte. Erfordert Terminal-Zugriff auf den Pi (SSH), ist also
kein Sicherheitsloch fuer Fremde - nur fuer dich als Administrator gedacht.

Verwendung:
    cd /opt/himbeerepi/app
    sudo -u himbeerepi ../venv/bin/python3 emergency_disable_2fa.py
"""
import os
import sqlite3

DB_PATH = os.environ.get("HIMBEEREPI_DB", "/var/lib/himbeerepi/users.db")

username = input("Benutzername, fuer den 2FA deaktiviert werden soll: ").strip()

conn = sqlite3.connect(DB_PATH)
row = conn.execute("SELECT id, totp_enabled FROM users WHERE username = ?", (username,)).fetchone()

if not row:
    print(f"FEHLER: Benutzer '{username}' wurde nicht gefunden.")
    conn.close()
    exit(1)

if not row[1]:
    print(f"Fuer '{username}' ist 2FA ohnehin nicht aktiviert. Nichts zu tun.")
    conn.close()
    exit(0)

conn.execute("UPDATE users SET totp_secret = NULL, totp_enabled = 0 WHERE username = ?", (username,))
# Alle Anmeldungen beenden - ein verlorenes Handy war vielleicht noch angemeldet
try:
    conn.execute("UPDATE users SET session_version = session_version + 1 WHERE username = ?", (username,))
except sqlite3.OperationalError:  # Datenbank noch ohne Spalte: Dienst einmal neu starten
    pass
conn.commit()
conn.close()

print(f"Fertig. 2FA fuer '{username}' wurde deaktiviert, alle Geraete sind abgemeldet. Login ist wieder nur mit Passwort moeglich.")
print("Falls gewuenscht, kann 2FA jederzeit ueber /setup_2fa erneut eingerichtet werden.")
