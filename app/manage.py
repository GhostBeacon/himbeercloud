"""
Verwaltung der Benutzer-Datenbank: Tabellen anlegen und Benutzer hinzufuegen.

Verwendung (auf dem Pi, im Ordner /opt/himbeerepi/app):
    sudo -u himbeerepi ../venv/bin/python3 manage.py init
        Legt alle Tabellen an (idempotent - vorhandene Daten bleiben unberuehrt).
    sudo -u himbeerepi ../venv/bin/python3 manage.py add-user
        Fragt Benutzername, Passwort und optionales Speicherlimit ab.
    sudo -u himbeerepi ../venv/bin/python3 manage.py list-users
    sudo -u himbeerepi ../venv/bin/python3 manage.py count-users

Ohne Rueckfragen (Passwort kommt als erste Zeile ueber stdin, nie als Argument):
    ... manage.py add-user --username anna --password-stdin [--quota-gb 500] [--no-stats]

Passwort zuruecksetzen: reset_password.py. 2FA im Notfall abschalten: emergency_disable_2fa.py.
Die Datenbank liegt unter HIMBEEREPI_DB (Standard /var/lib/himbeerepi/users.db).
"""
import argparse
import getpass
import os
import re
import sqlite3
import sys

from werkzeug.security import generate_password_hash

DB_PATH = os.environ.get("HIMBEEREPI_DB", "/var/lib/himbeerepi/users.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    storage_quota_mb INTEGER,              -- NULL = kein Limit, sieht die ganze Platte
    display_name TEXT,                     -- Anzeigename in der Kopfzeile (optional)
    show_system_stats INTEGER NOT NULL DEFAULT 1,  -- Pi-Status-Seitenleiste sichtbar
    totp_secret TEXT,
    totp_enabled INTEGER NOT NULL DEFAULT 0
);

-- Keine Fremdschluessel auf parent_id/folder_id: Papierkorb-Leeren loescht Ordner in
-- beliebiger Reihenfolge; die Zuordnung prueft die Anwendung selbst (immer mit user_id).
CREATE TABLE IF NOT EXISTS folders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    parent_id INTEGER,
    user_id INTEGER NOT NULL,
    deleted_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_folders_user_parent ON folders(user_id, parent_id);

CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT NOT NULL,                -- Name auf der Platte (uuid_originalname)
    original_name TEXT NOT NULL,           -- angezeigter Name
    size_mb REAL NOT NULL DEFAULT 0,
    folder_id INTEGER,
    user_id INTEGER NOT NULL,
    deleted_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_files_user_folder ON files(user_id, folder_id);

CREATE TABLE IF NOT EXISTS login_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ip_address TEXT NOT NULL,
    attempt_time TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_login_attempts_ip ON login_attempts(ip_address, attempt_time);

CREATE TABLE IF NOT EXISTS energy_usage (
    date TEXT PRIMARY KEY,                 -- JJJJ-MM-TT, befuellt von track_energy.py
    kwh REAL NOT NULL DEFAULT 0
);
"""


def connect():
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db():
    conn = connect()
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()
    print(f"Datenbank bereit: {DB_PATH}")


USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,32}$")


def add_user(args=None):
    """Legt einen Benutzer an. Ohne Optionen wird alles abgefragt; mit --username und
    --password-stdin laeuft es ohne Rueckfragen (fuer den Einrichtungsassistenten)."""
    opts = _parse_add_user(args or [])
    init_db()
    interactive = opts.username is None
    username = opts.username if not interactive else input("Benutzername: ").strip()
    if not USERNAME_RE.match(username or ""):
        sys.exit("Abbruch: Benutzername fehlt oder enthaelt ungueltige Zeichen (erlaubt: A-Z a-z 0-9 . _ -, max. 32).")
    if opts.password_stdin:
        pw1 = pw2 = sys.stdin.readline().rstrip("\n")
    else:
        pw1 = getpass.getpass("Passwort (mind. 12 Zeichen): ")
        pw2 = getpass.getpass("Nochmal zur Bestaetigung: ")
    if pw1 != pw2:
        sys.exit("Abbruch: Passwoerter stimmen nicht ueberein.")
    if len(pw1) < 12:
        sys.exit("Abbruch: Passwort zu kurz (mindestens 12 Zeichen).")
    if interactive:
        quota = input("Speicherlimit in GB (leer = kein Limit, sieht die ganze Platte): ").strip()
        display_name = input("Anzeigename (leer = Benutzername): ").strip() or None
        stats = input("Pi-Status-Seitenleiste anzeigen? [J/n]: ").strip().lower() not in ("n", "nein")
    else:
        quota, display_name, stats = opts.quota_gb or "", opts.display_name, not opts.no_stats
    try:
        quota_mb = int(float(quota.replace(",", ".")) * 1024) if quota else None
    except ValueError:
        sys.exit("Abbruch: Speicherlimit muss eine Zahl sein.")

    conn = connect()
    try:
        conn.execute(
            "INSERT INTO users (username, password_hash, storage_quota_mb, display_name, show_system_stats) "
            "VALUES (?, ?, ?, ?, ?)",
            (username, generate_password_hash(pw1), quota_mb, display_name, 1 if stats else 0))
        conn.commit()
    except sqlite3.IntegrityError:
        sys.exit(f"Abbruch: Benutzer '{username}' gibt es schon.")
    finally:
        conn.close()
    print(f"Benutzer '{username}' angelegt. 2FA laesst sich nach der Anmeldung in der Weboberflaeche einrichten.")


def _parse_add_user(args):
    p = argparse.ArgumentParser(prog="manage.py add-user")
    p.add_argument("--username")
    p.add_argument("--password-stdin", action="store_true", help="Passwort als erste Zeile von stdin")
    p.add_argument("--quota-gb", help="Speicherlimit in GB (weglassen = kein Limit)")
    p.add_argument("--display-name")
    p.add_argument("--no-stats", action="store_true", help="Pi-Status-Seitenleiste ausblenden")
    opts = p.parse_args(args)
    if opts.password_stdin and not opts.username:
        p.error("--password-stdin braucht --username")
    return opts


def count_users():
    """Gibt die Zahl der Benutzer aus (fuer Skripte)."""
    init_db_quiet()
    conn = connect()
    print(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0])
    conn.close()


def init_db_quiet():
    conn = connect()
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()


def list_users():
    conn = connect()
    for u in conn.execute("SELECT id, username, storage_quota_mb, totp_enabled FROM users ORDER BY id"):
        limit = f"{u['storage_quota_mb'] / 1024:.4g} GB" if u["storage_quota_mb"] else "kein Limit"
        print(f"{u['id']:>3}  {u['username']:<20} {limit:<12} 2FA: {'ja' if u['totp_enabled'] else 'nein'}")
    conn.close()


if __name__ == "__main__":
    commands = {"init": init_db, "add-user": add_user, "list-users": list_users, "count-users": count_users}
    if len(sys.argv) < 2 or sys.argv[1] not in commands:
        sys.exit(__doc__)
    if sys.argv[1] == "add-user":
        add_user(sys.argv[2:])
    elif len(sys.argv) == 2:
        commands[sys.argv[1]]()
    else:
        sys.exit(__doc__)
