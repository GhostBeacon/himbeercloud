import os
import uuid
import sqlite3
import subprocess
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
import time
import zipfile
import io
import re
import json
from datetime import datetime, timedelta
from flask import Flask, render_template, request, send_from_directory, send_file, redirect, url_for, flash, session, jsonify
from flask_wtf import CSRFProtect
from flask_wtf.csrf import generate_csrf
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash
import secrets
import pyotp
import qrcode
import io
import base64
from werkzeug.utils import secure_filename
from PIL import Image
import pillow_heif
pillow_heif.register_heif_opener()
import psutil

app = Flask(__name__)
# Hinter Caddy: echte Besucher-IP aus X-Forwarded-For uebernehmen (genau ein Proxy davor).
# Ohne das sieht der Server alle Besucher als 127.0.0.1 - dann sperren 5 Fehlversuche eines
# Fremden ALLE Nutzer fuer 15 Minuten aus. Sicher, weil Gunicorn nur an 127.0.0.1 lauscht und
# damit nur Caddy den Header setzen kann (Caddy ignoriert von Clients mitgeschickte Werte).
# x_proto: Caddy meldet per X-Forwarded-Proto, dass der Besucher HTTPS nutzt. Gunicorn
# uebernimmt das von 127.0.0.1 ohnehin schon (forwarded_allow_ips) - x_proto macht es
# unabhaengig von dieser Voreinstellung. Damit gilt die Referer-Pruefung von
# WTF_CSRF_SSL_STRICT fuer POSTs der Weboberflaeche.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
# Der Secret Key liegt bewusst NICHT mehr im Quellcode, sondern in einer separaten Datei
# mit Rechten 640 (nur fuer root und den Dienstbenutzer himbeerepi lesbar), die per systemd 'EnvironmentFile'
# eingebunden wird (siehe deploy/himbeerepi.env.example und deploy/himbeerepi.service). Stuende
# der Key hier im Klartext, koennte ihn jeder lokale Account auf dem Pi lesen (Session-Faelschung).
app.secret_key = os.environ['SECRET_KEY']
csrf = CSRFProtect(app)

# Der Login ist ueber die eigene Domain (per Caddy-Reverse-Proxy, immer HTTPS) erreichbar -
# das Session-Cookie soll deshalb nie unverschluesselt uebertragen werden.
# Nur fuer lokale Tests ohne HTTPS: HIMBEEREPI_INSECURE_COOKIE=1.
app.config['SESSION_COOKIE_SECURE'] = os.environ.get('HIMBEEREPI_INSECURE_COOKIE') != '1'
# Cookie bei Anfragen von fremden Seiten nicht mitschicken (zusaetzlich zum CSRF-Token)
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

# Begrenzter Arbeiter-Pool fuer die Thumbnail-Erstellung im Hintergrund. Ohne Begrenzung wuerde
# jede hochgeladene Datei einen komplett neuen, unbegrenzten Thread starten (siehe fruehere Version) -
# bei sehr grossen Batch-Uploads (mehrere tausend Dateien, viele davon RAW-Formate mit zusaetzlichem
# externen dcraw/exiftool-Prozess) konnte sich das zu hunderten gleichzeitigen Prozessen aufstauen und
# den Pi in Speichernot bringen. Der Pool laesst nur wenige Thumbnail-Jobs gleichzeitig laufen, der Rest
# wartet automatisch in einer Warteschlange.
thumbnail_executor = ThreadPoolExecutor(max_workers=1)

# Eigener Zaehler der noch wartenden/laufenden Thumbnail-Jobs (statt des privaten,
# nicht oeffentlich dokumentierten '_work_queue'-Attributs von ThreadPoolExecutor -
# das ist ein Implementierungsdetail, auf das man sich nicht verlassen sollte).
_thumbnail_pending = 0
_thumbnail_pending_lock = threading.Lock()


def submit_thumbnail_job(func, *args):
    """Reicht einen Thumbnail-Job beim Hintergrund-Pool ein und haelt dabei den eigenen
    Zaehler der wartenden/laufenden Jobs aktuell (siehe get_pending_thumbnail_count)."""
    global _thumbnail_pending
    with _thumbnail_pending_lock:
        _thumbnail_pending += 1

    def _on_done(_future):
        global _thumbnail_pending
        with _thumbnail_pending_lock:
            _thumbnail_pending -= 1

    future = thumbnail_executor.submit(func, *args)
    future.add_done_callback(_on_done)
    return future


def get_pending_thumbnail_count():
    with _thumbnail_pending_lock:
        return _thumbnail_pending


# Kurzzeit-Cache fuer Werte, die teuer zu berechnen sind (Dateisystem-Scan, mehrere SQL-
# Aggregat-Queries, externe Prozess-Aufrufe), sich aber viel seltener aendern, als sie
# abgefragt werden - z.B. der Backup-Status oder die Energie-Summe, die per /system_stats
# alle 5 Sekunden fuer die Live-Sidebar gepollt werden, obwohl sich die zugrunde liegenden
# Daten hoechstens einmal pro Minute (Energie) bzw. einmal pro Tag (Backups) aendern.
_value_cache = {}
_value_cache_lock = threading.Lock()


def get_cached_value(key, ttl_seconds, compute_func):
    with _value_cache_lock:
        entry = _value_cache.get(key)
        now = time.time()
        if entry is None or now - entry[1] > ttl_seconds:
            entry = (compute_func(), now)
            _value_cache[key] = entry
        return entry[0]

# Ordner fuer die hochgeladenen Dateien (Einstellung HIMBEEREPI_DATA_DIR). Ohne externe Festplatte
# liegt er auf der SD-Karte; mit Speicher-Festplatte ist diese genau hier eingehaengt.
UPLOAD_FOLDER = os.environ.get('HIMBEEREPI_DATA_DIR', '/srv/himbeerepi')
THUMBNAIL_FOLDER = os.path.join(UPLOAD_FOLDER, 'thumbnails')
BACKUP_FOLDER = os.path.join(UPLOAD_FOLDER, 'backups')  # Ziel von scripts/backup_db.sh

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['THUMBNAIL_FOLDER'] = THUMBNAIL_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024 * 1024  # Maximal 50 GB pro Upload-Vorgang
app.config['MAX_FORM_PARTS'] = 20000  # Standard waere nur 1000 - reicht nicht fuer Sammel-Aktionen
                                        # (Verschieben/Loeschen/Download) mit mehreren tausend
                                        # ausgewaehlten Dateien, da jede Datei-ID ein eigenes
                                        # Formularfeld ist
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=12)  # Automatischer Logout nach 12 Std. Inaktivitaet
                                                                    # (bewusst grosszuegig, wegen sehr langer
                                                                    # Batch-Uploads)
app.config['WTF_CSRF_TIME_LIMIT'] = 12 * 60 * 60  # CSRF-Token 12 Stunden gueltig (Standard waere nur 1 Stunde)

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(THUMBNAIL_FOLDER, exist_ok=True)

# Unterstuetzte Formate fuer Vorschaubilder
IMAGE_EXTENSIONS = {'jpg', 'jpeg', 'png', 'gif', 'webp', 'cr3', 'cr2', 'heic', 'heif', 'pdf'}

# Brute-Force-Schutz fuer den Login
MAX_LOGIN_ATTEMPTS = 5
LOCKOUT_WINDOW_MINUTES = 15
LOCKOUT_DURATION_MINUTES = 15
# Bei unbekanntem Benutzernamen wird trotzdem ein Hash geprueft - sonst verraet die kuerzere
# Antwortzeit, welche Benutzernamen es gibt.
DUMMY_PASSWORD_HASH = generate_password_hash(secrets.token_urlsafe(16))

# Nur diese Dateitypen zeigt der Browser direkt an (Vorschau). Alles andere - vor allem HTML,
# SVG, XML - wird als Download ausgeliefert: im Browser geoeffnet koennte es sonst Skripte mit
# der Anmeldung des Nutzers ausfuehren. Massgeblich ist die Endung der Datei AUF DER PLATTE,
# denn die bestimmt den Content-Type (Umbenennen aendert nur den Anzeigenamen).
INLINE_SAFE_EXTENSIONS = {'jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp', 'avif', 'pdf',
                          'mp4', 'webm', 'mov', 'm4v', 'mp3', 'm4a', 'wav', 'ogg', 'txt'}

# Flask-Login Konfiguration
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'


@app.before_request
def refresh_session_timeout():
    """Verlaengert die Sitzung bei jeder Aktivitaet um weitere 15 Minuten (sliding window)."""
    session.permanent = True
    session.modified = True


class User(UserMixin):
    def __init__(self, id, username, storage_quota_mb=None, display_name=None, show_system_stats=1):
        self.id = id
        self.username = username
        self.storage_quota_mb = storage_quota_mb
        self.display_name = display_name
        self.show_system_stats = bool(show_system_stats)


# Benutzer-Datenbank (Einstellung HIMBEEREPI_DB)
DB_PATH = os.environ.get('HIMBEEREPI_DB', '/var/lib/himbeerepi/users.db')


def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    conn.execute('PRAGMA journal_mode = WAL')
    conn.execute('PRAGMA busy_timeout = 5000')
    return conn


def get_user_total_usage_mb(conn, user_id):
    """Summiert die Groesse aller Dateien eines Benutzers (inkl. Papierkorb, da physisch noch belegt)."""
    row = conn.execute('SELECT COALESCE(SUM(size_mb), 0) as total FROM files WHERE user_id = ?', (user_id,)).fetchone()
    return row['total']


def _float_env(name, default):
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


ELECTRICITY_PRICE_EUR_PER_KWH = _float_env('HIMBEEREPI_POWER_PRICE', 0.35)  # eigener Strompreis in EUR/kWh
HDD_WATTS_ESTIMATE = _float_env('HIMBEEREPI_HDD_WATTS', 0.0)  # Pauschale fuer externe Festplatten (kein eigener Stromsensor)


def get_pi_power_watts():
    """Liest die echten Strom-/Spannungswerte des Raspberry Pi 5 ueber den eingebauten
    PMIC-Sensor aus ('vcgencmd pmic_read_adc') und berechnet daraus die aktuelle
    Gesamtleistung in Watt (Summe aus Spannung mal Strom ueber alle internen
    Versorgungsschienen). Wird sowohl fuer die Live-Anzeige in der Sidebar als auch
    konzeptionell fuer die Kostenrechnung in track_energy.py genutzt (dort als
    eigenstaendige Kopie, da track_energy.py als separates Cronjob-Skript laeuft).
    Gibt None zurueck, falls vcgencmd nicht verfuegbar ist."""
    try:
        result = subprocess.run(['vcgencmd', 'pmic_read_adc'], capture_output=True, text=True, timeout=3)
        output = result.stdout

        currents = {}
        voltages = {}
        pattern = re.compile(r'^\s*(\S+)_(A|V)\s+(?:current|volt)\(\d+\)=([\-0-9.]+)[AV]', re.MULTILINE)
        for match in pattern.finditer(output):
            rail, kind, value = match.group(1), match.group(2), float(match.group(3))
            if kind == 'A':
                currents[rail] = value
            else:
                voltages[rail] = value

        if not currents:
            return None  # kein PMIC-Sensor (z.B. Raspberry Pi 4)
        total_watts = sum(currents[rail] * voltages[rail] for rail in currents if rail in voltages)
        return round(total_watts, 2)
    except Exception:
        return None


def get_energy_summary(conn):
    """Liest die per Cronjob (track_energy.py) erfassten, geschaetzten Verbrauchswerte aus
    und rechnet sie in Kosten fuer diesen Monat, dieses Jahr und insgesamt um."""
    now = datetime.now()
    month_prefix = now.strftime('%Y-%m')
    year_prefix = now.strftime('%Y')

    month_kwh = conn.execute(
        "SELECT COALESCE(SUM(kwh), 0) as total FROM energy_usage WHERE date LIKE ?",
        (month_prefix + '-%',)).fetchone()['total']
    year_kwh = conn.execute(
        "SELECT COALESCE(SUM(kwh), 0) as total FROM energy_usage WHERE date LIKE ?",
        (year_prefix + '-%',)).fetchone()['total']
    total_kwh = conn.execute(
        "SELECT COALESCE(SUM(kwh), 0) as total FROM energy_usage").fetchone()['total']

    return {
        'month_kwh': round(month_kwh, 2),
        'year_kwh': round(year_kwh, 2),
        'total_kwh': round(total_kwh, 2),
        'month_cost': round(month_kwh * ELECTRICITY_PRICE_EUR_PER_KWH, 2),
        'year_cost': round(year_kwh * ELECTRICITY_PRICE_EUR_PER_KWH, 2),
        'total_cost': round(total_kwh * ELECTRICITY_PRICE_EUR_PER_KWH, 2)
    }


def next_daily_run(hour, minute):
    """Naechster Zeitpunkt eines taeglichen Cronjobs mit gegebener Uhrzeit."""
    now = datetime.now()
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


def get_backup_status():
    """Status des taeglichen Datenbank-Backups (scripts/backup_db.sh, per Cron um 3:10 Uhr):
    wann es zuletzt lief, ob es UEBERFAELLIG ist (aelter als 30 Stunden - z.B. weil der
    Cronjob fehlt) und wann der naechste Lauf ansteht. Der Zeitstempel kommt aus der
    Datei-Aenderungszeit der neuesten Sicherung, nicht aus Log-Text."""
    now = datetime.now()
    next_run = next_daily_run(3, 10).strftime('%d.%m.%Y %H:%M')
    db_files = []
    if os.path.isdir(BACKUP_FOLDER):
        db_files = [f for f in os.listdir(BACKUP_FOLDER) if f.startswith('users_db_') and f.endswith('.db')]
    if not db_files:
        return {'db_backup': {'last_run': None, 'ok': False, 'stale': True, 'next_run': next_run}}
    newest_path = max((os.path.join(BACKUP_FOLDER, f) for f in db_files), key=os.path.getmtime)
    last_run_dt = datetime.fromtimestamp(os.path.getmtime(newest_path))
    return {'db_backup': {
        'last_run': last_run_dt.strftime('%d.%m.%Y %H:%M'),
        'ok': True,
        'stale': (now - last_run_dt).total_seconds() > 30 * 3600,
        'next_run': next_run,
    }}


def get_disk_info(conn):
    """Speicherplatzanzeige: Benutzer mit persoenlichem Limit (storage_quota_mb) sehen nur ihr
    eigenes Kontingent. Benutzer ohne Limit (storage_quota_mb ist NULL) sehen zusaetzlich eine
    Aufschluesselung der gesamten Festplatte nach Cloud-Benutzer.
    Wird sowohl von der Ordneransicht als auch vom Papierkorb genutzt (gleicher Header)."""

    if current_user.storage_quota_mb:
        used_mb = get_user_total_usage_mb(conn, current_user.id)
        quota_mb = current_user.storage_quota_mb
        return {
            'mode': 'quota',
            'total_gb': round(quota_mb / 1024, 1),
            'used_gb': round(used_mb / 1024, 1),
            'free_gb': round(max(quota_mb - used_mb, 0) / 1024, 1),
            'percent_used': round(min((used_mb / quota_mb) * 100, 100), 1) if quota_mb > 0 else 0
        }
    else:
        total, used, free = shutil.disk_usage(app.config['UPLOAD_FOLDER'])
        total_gb_raw = total / (1024 ** 3)
        used_gb_raw = used / (1024 ** 3)
        free_gb_raw = free / (1024 ** 3)

        percent_used = round(min((used_gb_raw / total_gb_raw) * 100, 100), 1) if total_gb_raw > 0 else 100.0

        disk_data = {
            'mode': 'overview',
            'total_gb': round(total_gb_raw, 1),
            'used_gb': round(used_gb_raw, 1),
            'free_gb': round(free_gb_raw, 1),
            'percent_used': percent_used
        }

        breakdown = []
        users = conn.execute('SELECT id, username, display_name FROM users').fetchall()
        for u in users:
            usage_mb = get_user_total_usage_mb(conn, u['id'])
            label = u['display_name'] or (u['username'].capitalize() + 's Cloud')
            breakdown.append({'label': label, 'gb': round(usage_mb / 1024, 2)})

        disk_data['breakdown'] = breakdown
        return disk_data


def ensure_session_version_column():
    """Spalte session_version nachruesten (Datenbanken aus aelteren Versionen). Mehrere
    Gunicorn-Worker laufen hier gleichzeitig durch - eine schon angelegte Spalte ist kein Fehler."""
    conn = get_db_connection()
    try:
        columns = [r[1] for r in conn.execute('PRAGMA table_info(users)').fetchall()]
        if columns and 'session_version' not in columns:
            try:
                conn.execute('ALTER TABLE users ADD COLUMN session_version INTEGER NOT NULL DEFAULT 1')
                conn.commit()
            except sqlite3.OperationalError as e:
                if 'duplicate column name' not in str(e):
                    raise
    finally:
        conn.close()


ensure_session_version_column()


def start_session(user):
    """Meldet den Benutzer an und merkt sich seine Sitzungsnummer. Wird die Nummer in der
    Datenbank erhoeht (Passwort zurueckgesetzt, 2FA eingeschaltet, "andere Geraete abmelden"),
    sind alle Sitzungen mit der alten Nummer ungueltig - siehe load_user()."""
    login_user(User(user['id'], user['username'], user['storage_quota_mb'], user['display_name'], user['show_system_stats']))
    session['sv'] = user['session_version']


def bump_session_version(conn, user_id):
    """Beendet alle Sitzungen des Benutzers. Gibt die neue Nummer zurueck."""
    conn.execute('UPDATE users SET session_version = session_version + 1 WHERE id = ?', (user_id,))
    conn.commit()
    return conn.execute('SELECT session_version FROM users WHERE id = ?', (user_id,)).fetchone()[0]


@login_manager.user_loader
def load_user(user_id):
    conn = get_db_connection()
    user = conn.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
    conn.close()
    # Sitzung nur gueltig, solange ihre Nummer zur aktuellen des Benutzers passt
    if user and session.get('sv') == user['session_version']:
        return User(user['id'], user['username'], user['storage_quota_mb'], user['display_name'], user['show_system_stats'])
    return None


def compute_all_folder_sizes(conn, user_id):
    """Berechnet die Groesse ALLER Ordner eines Benutzers (inkl. aller Unterordner) in einem
    Durchgang: 2 Datenbank-Abfragen insgesamt, statt vorher einer rekursiven Abfrage-Kette
    pro einzelnem angezeigten Ordner (bei tief verschachtelten Strukturen mit vielen
    Unterordnern ein klassisches N+1-Problem, das bei jedem Seitenaufruf erneut anfiel).
    Gibt ein Dict {folder_id: size_mb} zurueck."""
    all_folders = conn.execute(
        'SELECT id, parent_id FROM folders WHERE user_id = ? AND deleted_at IS NULL', (user_id,)).fetchall()
    all_files = conn.execute(
        'SELECT folder_id, size_mb FROM files WHERE user_id = ? AND deleted_at IS NULL', (user_id,)).fetchall()

    own_size = {f['id']: 0 for f in all_folders}
    for f in all_files:
        if f['folder_id'] in own_size:
            own_size[f['folder_id']] += f['size_mb']

    children_by_parent = {}
    for f in all_folders:
        children_by_parent.setdefault(f['parent_id'], []).append(f['id'])

    total_size = {}

    def compute(folder_id):
        if folder_id not in total_size:
            total = own_size.get(folder_id, 0)
            for child_id in children_by_parent.get(folder_id, []):
                total += compute(child_id)
            total_size[folder_id] = round(total, 2)
        return total_size[folder_id]

    for f in all_folders:
        compute(f['id'])

    return total_size


def get_breadcrumb_path(conn, folder, user_id):
    """Baut die komplette Ordner-Kette vom aktuellen Ordner bis zur Wurzel auf (fuer den
    klickbaren Brotkrumen-Pfad, damit man direkt zu einer beliebigen Zwischenebene springen kann)."""
    path = []
    current = folder
    seen = set()
    while current is not None:
        if current['id'] in seen:
            break
        seen.add(current['id'])
        path.append({'id': current['id'], 'name': current['name']})
        if current['parent_id'] is None:
            break
        current = conn.execute('SELECT * FROM folders WHERE id = ? AND user_id = ?',
                               (current['parent_id'], user_id)).fetchone()
    path.reverse()
    return path


def build_folder_paths(folder_rows):
    """Liefert {ordner_id: "A / B / C"} fuer alle (nicht geloeschten) Ordner eines Benutzers -
    in einem Durchgang aus einer einzigen Abfrage, fuer die Ortsangabe bei Suchtreffern.
    Ordner, deren Elternkette auf einen geloeschten Ordner zeigt, fehlen im Ergebnis."""
    by_id = {f['id']: f for f in folder_rows}
    paths = {}

    def resolve(fid, seen):
        if fid in paths:
            return paths[fid]
        f = by_id.get(fid)
        if f is None or fid in seen:
            return None
        seen.add(fid)
        if f['parent_id'] is None:
            paths[fid] = f['name']
        else:
            parent = resolve(f['parent_id'], seen)
            if parent is None:
                return None
            paths[fid] = parent + ' / ' + f['name']
        return paths[fid]

    for fid in by_id:
        resolve(fid, set())
    return paths


def is_mobile_device():
    """Erkennt anhand des User-Agent-Headers, ob vom Handy zugegriffen wird - dann wird
    eine eigene, bewusst einfach gehaltene mobile Ansicht ausgeliefert (klassische
    Formular-Navigation statt AJAX/JavaScript-SPA, robuster auf mobilen Browsern)."""
    ua = request.headers.get('User-Agent', '')
    return bool(re.search(r'Mobi|Android|iPhone|iPod', ua, re.IGNORECASE))


def build_folder_tree(all_folders):
    """Wandelt die flache Ordnerliste (mit parent_id) in eine hierarchisch sortierte Liste um,
    inkl. Verschachtelungstiefe - Eltern erscheinen immer vor ihren Kindern, Geschwister
    alphabetisch sortiert. Wird fuer die eingerueckte, auf-/zuklappbare Anzeige im
    Verschieben-Dialog genutzt, damit die Ordnerstruktur auf einen Blick erkennbar ist statt
    einer unsortierten, flachen Liste."""
    children_by_parent = {}
    for f in all_folders:
        children_by_parent.setdefault(f['parent_id'], []).append(f)
    for parent_id in children_by_parent:
        children_by_parent[parent_id].sort(key=lambda f: f['name'].lower())

    result = []

    def walk(parent_id, depth):
        for f in children_by_parent.get(parent_id, []):
            result.append({
                'id': f['id'],
                'name': f['name'],
                'depth': depth,
                'parent_id': f['parent_id'],
                'has_children': f['id'] in children_by_parent
            })
            walk(f['id'], depth + 1)

    walk(None, 0)
    return result


def get_or_create_subfolder(conn, name, parent_id, user_id):
    """Sucht einen Unterordner mit gegebenem Namen unter parent_id, legt ihn bei Bedarf neu an.
    Der Name kommt aus Ordner- und ZIP-Uploads und wird deshalb bereinigt."""
    name = sanitize_display_name(name) or '_'
    if parent_id is None:
        row = conn.execute(
            'SELECT id FROM folders WHERE name = ? AND parent_id IS NULL AND user_id = ? AND deleted_at IS NULL',
            (name, user_id)).fetchone()
    else:
        row = conn.execute(
            'SELECT id FROM folders WHERE name = ? AND parent_id = ? AND user_id = ? AND deleted_at IS NULL',
            (name, parent_id, user_id)).fetchone()

    if row:
        return row['id']

    cur = conn.execute('INSERT INTO folders (name, parent_id, user_id) VALUES (?, ?, ?)',
                        (name, parent_id, user_id))
    return cur.lastrowid


def import_zip_with_structure(conn, zip_path, base_folder_id, user_id, upload_folder, thumbnail_folder, image_extensions, create_thumbnail_func):
    """Packt eine hochgeladene ZIP-Datei aus und baut die enthaltene Ordnerstruktur
    (Unterordner inklusive) unter base_folder_id originalgetreu nach. Nur aktiv, wenn der
    Benutzer die Checkbox 'ZIP-Dateien automatisch entpacken' beim Upload aktiviert hat -
    standardmaessig werden ZIP-Dateien wie jede andere Datei ganz normal gespeichert."""
    folder_cache = {}

    with zipfile.ZipFile(zip_path, 'r') as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue

            parts = [p for p in info.filename.split('/') if p]
            if not parts:
                continue
            # Typischen macOS-Zip-Muell ueberspringen
            if '__MACOSX' in parts or parts[-1] == '.DS_Store':
                continue

            filename_part = parts[-1]
            folder_parts = parts[:-1]

            walk_id = base_folder_id
            for seg in folder_parts:
                cache_key = (walk_id, seg)
                if cache_key in folder_cache:
                    walk_id = folder_cache[cache_key]
                else:
                    walk_id = get_or_create_subfolder(conn, seg, walk_id, user_id)
                    folder_cache[cache_key] = walk_id

            original_name = secure_filename(filename_part)
            if not original_name:
                continue

            data = zf.read(info)
            unique_filename = f"{uuid.uuid4().hex}_{original_name}"
            out_path = os.path.join(upload_folder, unique_filename)
            try:
                with open(out_path, 'wb') as out_f:
                    out_f.write(data)
            except OSError as e:
                print(f"Fehler beim Schreiben von {original_name} aus ZIP: {e}")
                continue
            size_mb = round(len(data) / (1024 * 1024), 2)

            ext = original_name.rsplit('.', 1)[-1].lower() if '.' in original_name else ''
            if ext in image_extensions:
                submit_thumbnail_job(create_thumbnail_func, unique_filename, ext)

            conn.execute('INSERT INTO files (filename, original_name, size_mb, folder_id, user_id) VALUES (?, ?, ?, ?, ?)',
                         (unique_filename, original_name, size_mb, walk_id, user_id))


def get_cpu_temperature():
    """Liest die CPU-Temperatur des Pi direkt aus dem Linux-Sensor-Dateisystem aus."""
    try:
        with open('/sys/class/thermal/thermal_zone0/temp') as f:
            return round(int(f.read().strip()) / 1000, 1)
    except Exception:
        return None


def get_throttle_status():
    """Liest den Raspberry-Pi-spezifischen Drossel-/Unterspannungs-Status via vcgencmd aus.
    Gibt None zurueck, falls vcgencmd nicht verfuegbar ist (z.B. auf einem Nicht-Pi-System)."""
    try:
        result = subprocess.run(['vcgencmd', 'get_throttled'], capture_output=True, text=True, timeout=2)
        output = result.stdout.strip()
        if '=' not in output:
            return None
        value = int(output.split('=')[1], 16)
        return {
            'under_voltage_now': bool(value & 0x1),
            'freq_capped_now': bool(value & 0x2),
            'throttled_now': bool(value & 0x4),
            'temp_limit_now': bool(value & 0x8),
            'under_voltage_occurred': bool(value & 0x10000),
            'freq_capped_occurred': bool(value & 0x20000),
            'throttled_occurred': bool(value & 0x40000),
            'temp_limit_occurred': bool(value & 0x80000),
        }
    except Exception:
        return None


def get_disk_io_bytes():
    """Liest die kumulierten Lese-/Schreib-Bytes der Cloud-HDD (sda) aus, faellt sonst auf alle Datentraeger zusammen zurueck."""
    try:
        per_disk = psutil.disk_io_counters(perdisk=True)
        io = per_disk.get('sda')
        if io is None:
            io = psutil.disk_io_counters()
        return io.read_bytes, io.write_bytes
    except Exception:
        return 0, 0


def get_network_io_bytes():
    """Liest die kumulierten gesendeten/empfangenen Bytes ueber alle Netzwerkschnittstellen."""
    try:
        net = psutil.net_io_counters()
        return net.bytes_recv, net.bytes_sent
    except Exception:
        return 0, 0


THUMBNAIL_SIZE = (120, 120)
PREVIEW_SIZE = (1600, 1600)


def _save_resized_jpeg(img, path, max_size):
    """Speichert eine verkleinerte Kopie eines Bildes als JPEG. JPEG kennt keine Transparenz -
    Bilder mit Alpha-Kanal (z.B. PNGs im RGBA-Modus) werden dafuer auf einem weissen Hintergrund
    'plattgedrueckt', statt dass Pillow mit 'cannot write mode RGBA as JPEG' abbricht.
    Arbeitet auf einer Kopie, damit dasselbe geoeffnete Bild fuer mehrere Zielgroessen
    (z.B. kleines Listen-Thumbnail + grosse Vorschau) wiederverwendet werden kann."""
    img = img.copy()
    img.thumbnail(max_size)
    if img.mode in ('RGBA', 'LA') or (img.mode == 'P' and 'transparency' in img.info):
        img = img.convert('RGBA')
        background = Image.new('RGB', img.size, (255, 255, 255))
        background.paste(img, mask=img.split()[-1])
        img = background
    elif img.mode != 'RGB':
        img = img.convert('RGB')
    img.save(path, "JPEG", quality=90)


def create_thumbnail(filename, ext):
    """Erstellt kleine Listen-Thumbnails (120x120) fuer alle Bildtypen, sowie zusaetzlich
    groessere Vorschaubilder (max. 1600x1600, Endung '_preview.jpg') fuer Formate, die Browser
    nicht nativ darstellen koennen (CR3/CR2-RAWs, HEIC/HEIF, PDFs) - fuer die Grossansicht beim
    Anklicken. Bei Standardbildern (JPG/PNG/WEBP/GIF) wird fuer die Grossansicht stattdessen
    einfach die Originaldatei direkt ausgeliefert, da Browser die nativ darstellen koennen.

    Wird im Upload-Request nicht direkt aufgerufen, sondern ueber einen Hintergrund-Thread
    (siehe upload_file), damit langsame externe Prozesse (pdftoppm, exiftool, dcraw) den
    Gunicorn-Worker nicht blockieren."""
    orig_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    thumb_path = os.path.join(app.config['THUMBNAIL_FOLDER'], filename + '.jpg')
    preview_path = os.path.join(app.config['THUMBNAIL_FOLDER'], filename + '_preview.jpg')

    try:
        # --- 1. PDF HANDLING ---
        if ext == 'pdf':
            temp_prefix = os.path.join(app.config['THUMBNAIL_FOLDER'], f"temp_{filename}")
            subprocess.run(['pdftoppm', '-png', '-f', '1', '-l', '1', '-scale-to', '1600', orig_path, temp_prefix], check=True)

            generated_png = f"{temp_prefix}-1.png"
            if os.path.exists(generated_png):
                with Image.open(generated_png) as img:
                    _save_resized_jpeg(img, preview_path, PREVIEW_SIZE)
                    _save_resized_jpeg(img, thumb_path, THUMBNAIL_SIZE)
                os.remove(generated_png)

        # --- 2. CANON CR3/CR2 RAW HANDLING VIA EXIFTOOL ---
        elif ext in ('cr3', 'cr2'):
            extracted_jpg = os.path.join(app.config['THUMBNAIL_FOLDER'], f"temp_{filename}.jpg")

            tags = ['-PreviewImage', '-JpgFromRaw', '-ThumbnailImage']
            for tag in tags:
                with open(extracted_jpg, 'wb') as out_f:
                    subprocess.run(['exiftool', '-b', tag, orig_path], stdout=out_f, stderr=subprocess.DEVNULL)
                if os.path.exists(extracted_jpg) and os.path.getsize(extracted_jpg) > 0:
                    break

            if os.path.exists(extracted_jpg) and os.path.getsize(extracted_jpg) > 0:
                with Image.open(extracted_jpg) as img:
                    _save_resized_jpeg(img, preview_path, PREVIEW_SIZE)
                    _save_resized_jpeg(img, thumb_path, THUMBNAIL_SIZE)
                os.remove(extracted_jpg)
            else:
                # Fallback auf dcraw (funktioniert fuer CR3 und CR2 gleichermassen)
                subprocess.run(['dcraw', '-e', orig_path], check=False)
                base_no_ext = orig_path.rsplit('.', 1)[0]
                extracted_thumb = base_no_ext + '.thumb.jpg'
                if os.path.exists(extracted_thumb):
                    with Image.open(extracted_thumb) as img:
                        _save_resized_jpeg(img, preview_path, PREVIEW_SIZE)
                        _save_resized_jpeg(img, thumb_path, THUMBNAIL_SIZE)
                    os.remove(extracted_thumb)

        # --- 3. HEIC/HEIF (Apple-Fotoformat, z.B. von iPhones) ---
        elif ext in ('heic', 'heif'):
            with Image.open(orig_path) as img:
                _save_resized_jpeg(img, preview_path, PREVIEW_SIZE)
                _save_resized_jpeg(img, thumb_path, THUMBNAIL_SIZE)

        # --- 4. STANDARD BILDER (JPG, PNG, WEBP, GIF) ---
        else:
            with Image.open(orig_path) as img:
                _save_resized_jpeg(img, thumb_path, THUMBNAIL_SIZE)

    except Exception as e:
        print(f"Fehler bei Thumbnail-Erstellung für {filename}: {e}")


def get_client_ip():
    return request.remote_addr


def send_upload(file_record):
    """Liefert eine hochgeladene Originaldatei aus: sichere Typen zur Anzeige im Browser,
    alles andere als Download (siehe INLINE_SAFE_EXTENSIONS)."""
    filename = file_record['filename']
    disk_ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    if disk_ext in INLINE_SAFE_EXTENSIONS:
        return send_from_directory(app.config['UPLOAD_FOLDER'], filename, as_attachment=False)
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename,
                               download_name=file_record['original_name'], as_attachment=True)


def count_recent_failed_attempts(conn, ip_address):
    cutoff = (datetime.now() - timedelta(minutes=LOCKOUT_WINDOW_MINUTES)).isoformat()
    row = conn.execute(
        'SELECT COUNT(*) as cnt FROM login_attempts WHERE ip_address = ? AND attempt_time > ?',
        (ip_address, cutoff)).fetchone()
    return row['cnt']


def record_failed_attempt(conn, ip_address):
    conn.execute('INSERT INTO login_attempts (ip_address, attempt_time) VALUES (?, ?)',
                 (ip_address, datetime.now().isoformat()))
    conn.commit()


def clear_failed_attempts(conn, ip_address):
    conn.execute('DELETE FROM login_attempts WHERE ip_address = ?', (ip_address,))
    conn.commit()


# --- ROUTEN ---

@app.after_request
def add_no_cache_headers(response):
    """Verhindert, dass der Browser HTML-Seiten zwischenspeichert. Jede Seite enthaelt ein
    sitzungsgebundenes CSRF-Token im <meta>-Tag - eine gecachte, veraltete Seite wuerde ein
    Token verwenden, das der Server nicht mehr akzeptiert ('CSRF token is missing'/'expired'),
    obwohl die eigentliche Anfrage (z.B. ein Upload) technisch korrekt waere.
    Betrifft nur HTML-Antworten, nicht statische Dateien/Thumbnails/Downloads."""
    # Browser sollen den Content-Type nicht "erraten" (eine als .jpg hochgeladene HTML-Datei
    # bleibt so ein kaputtes Bild statt einer Webseite).
    response.headers['X-Content-Type-Options'] = 'nosniff'
    # Nicht in fremde Seiten einbetten lassen (Schutz gegen untergeschobene Klicks)
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Content-Security-Policy'] = "frame-ancestors 'none'"
    # Adressen der Cloud (Ordner, Dateinamen in der Suche) nicht an fremde Seiten weitergeben
    response.headers['Referrer-Policy'] = 'same-origin'
    # Nur bei Zugang ueber HTTPS: Browser merkt sich ein Jahr lang, die Cloud nie per HTTP aufzurufen
    if request.is_secure:
        response.headers['Strict-Transport-Security'] = 'max-age=31536000'
    if response.content_type and response.content_type.startswith('text/html'):
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        response.headers['Pragma'] = 'no-cache'
    return response


@app.route('/csrf_token')
@login_required
def get_csrf_token():
    """Liefert ein frisches CSRF-Token, ohne dass die Seite neu geladen werden muss.
    Wird vom Upload-JavaScript automatisch genutzt, falls eine sehr lange laufende Upload-Sitzung
    (z.B. 100+ GB ueber eine langsame Verbindung, mehrere Stunden Dauer) das urspruenglich beim
    Laden der Seite eingebettete Token ueberlebt - verhindert, dass ein abgelaufenes Token den
    kompletten restlichen Batch abbrechen laesst."""
    return jsonify({'csrf_token': generate_csrf()})


@app.route('/folder_tree_json')
@login_required
def folder_tree_json():
    """Liefert die komplette Ordnerstruktur des Benutzers als hierarchisch sortierte,
    eingerueckte Liste (JSON). Wird vom gemeinsamen Verschieben-Dialog bei jedem Oeffnen frisch
    abgerufen - dadurch braucht die eigentliche Seite die Ordnerliste nicht mehr redundant pro
    einzelner Datei einzubetten (das machte grosse Ordner mit tausenden Dateien vorher sehr
    schwerfaellig), und die Liste ist trotzdem immer aktuell, auch nach neu angelegten Ordnern."""
    conn = get_db_connection()
    all_folders = conn.execute('SELECT * FROM folders WHERE user_id = ? AND deleted_at IS NULL',
                                (current_user.id,)).fetchall()
    tree = build_folder_tree(all_folders)
    conn.close()
    return jsonify(tree)


@app.route('/login', methods=['GET', 'POST'])
def login():
    ip_address = get_client_ip()
    conn = get_db_connection()

    if request.method == 'POST':
        failed_count = count_recent_failed_attempts(conn, ip_address)

        if failed_count >= MAX_LOGIN_ATTEMPTS:
            flash(f'Zu viele fehlgeschlagene Anmeldeversuche. Bitte warte {LOCKOUT_DURATION_MINUTES} Minuten und versuche es erneut.')
            conn.close()
            return render_template('login.html')

        username = request.form['username']
        password = request.form['password']

        user = conn.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()

        password_ok = check_password_hash(user['password_hash'] if user else DUMMY_PASSWORD_HASH, password)
        if user and password_ok:
            if user['totp_enabled']:
                # Zwischenschritt: Passwort war korrekt, aber die eigentliche Anmeldung
                # (login_user) erfolgt erst NACH erfolgreicher Code-Pruefung in /verify_2fa.
                # Die Fehlversuche werden erst dort geloescht - sonst koennte man die Sperre
                # fuer falsche 2FA-Codes mit dem (bekannten) Passwort immer wieder aufheben.
                conn.close()
                session['pending_2fa_user_id'] = user['id']
                return redirect(url_for('verify_2fa'))

            clear_failed_attempts(conn, ip_address)
            conn.close()
            start_session(user)
            return redirect(url_for('index'))
        else:
            record_failed_attempt(conn, ip_address)
            remaining = MAX_LOGIN_ATTEMPTS - (failed_count + 1)
            if remaining > 0:
                flash(f'Ungültiger Benutzername oder Passwort! Noch {remaining} Versuch(e) übrig.')
            else:
                flash(f'Zu viele fehlgeschlagene Anmeldeversuche. Bitte warte {LOCKOUT_DURATION_MINUTES} Minuten und versuche es erneut.')

    conn.close()
    return render_template('login.html')


@app.route('/verify_2fa', methods=['GET', 'POST'])
def verify_2fa():
    """Zweiter Anmeldeschritt: wird nur erreicht, nachdem das Passwort bereits korrekt war
    (siehe login()). Erst nach erfolgreicher Code-Pruefung wird login_user() tatsaechlich
    aufgerufen - vorher besteht keine eingeloggte Sitzung."""
    pending_user_id = session.get('pending_2fa_user_id')
    if not pending_user_id:
        return redirect(url_for('login'))

    if request.method == 'POST':
        code = request.form.get('code', '').strip()
        ip_address = get_client_ip()
        conn = get_db_connection()
        # Falsche Codes zaehlen wie falsche Passwoerter - ohne Sperre liessen sich die
        # 6-stelligen Codes mit bekanntem Passwort einfach durchprobieren.
        if count_recent_failed_attempts(conn, ip_address) >= MAX_LOGIN_ATTEMPTS:
            conn.close()
            session.pop('pending_2fa_user_id', None)
            flash(f'Zu viele fehlgeschlagene Anmeldeversuche. Bitte warte {LOCKOUT_DURATION_MINUTES} Minuten und versuche es erneut.')
            return redirect(url_for('login'))
        user = conn.execute('SELECT * FROM users WHERE id = ?', (pending_user_id,)).fetchone()

        if user and user['totp_secret']:
            totp = pyotp.TOTP(user['totp_secret'])
            # valid_window=1 erlaubt eine kleine Zeitabweichung zwischen Handy-Uhr und
            # Server-Uhr (plus/minus 30 Sekunden), ohne die Sicherheit nennenswert zu schwaechen.
            if totp.verify(code, valid_window=1):
                clear_failed_attempts(conn, ip_address)
                conn.close()
                session.pop('pending_2fa_user_id', None)
                start_session(user)
                return redirect(url_for('index'))

        record_failed_attempt(conn, ip_address)
        conn.close()
        flash('Ungültiger Code. Bitte erneut versuchen.')

    return render_template('verify_2fa.html')


@app.route('/setup_2fa', methods=['GET', 'POST'])
@login_required
def setup_2fa():
    """Einrichtung der Zwei-Faktor-Authentifizierung. Der neue Geheimschluessel wird erst in
    der Datenbank gespeichert (und 2FA damit aktiv), nachdem der Benutzer einen echten Code
    aus seiner Authenticator-App erfolgreich eingegeben hat - verhindert, dass man sich durch
    einen Tippfehler beim Scannen selbst aussperrt."""
    conn = get_db_connection()
    user = conn.execute('SELECT * FROM users WHERE id = ?', (current_user.id,)).fetchone()

    if user['totp_enabled']:
        conn.close()
        return render_template('setup_2fa.html', already_enabled=True)

    if request.method == 'POST':
        secret = session.get('pending_totp_secret')
        code = request.form.get('code', '').strip()

        if secret and pyotp.TOTP(secret).verify(code, valid_window=1):
            conn.execute('UPDATE users SET totp_secret = ?, totp_enabled = 1 WHERE id = ?',
                         (secret, current_user.id))
            conn.commit()
            # Andere Sitzungen abmelden: Wer nur das Passwort kannte, soll nicht ueber eine
            # bestehende Sitzung an der neuen 2FA vorbeikommen. Diese Sitzung bleibt angemeldet.
            session['sv'] = bump_session_version(conn, current_user.id)
            conn.close()
            session.pop('pending_totp_secret', None)
            flash('Zwei-Faktor-Authentifizierung erfolgreich aktiviert.')
            return redirect(url_for('index'))
        else:
            conn.close()
            flash('Ungültiger Code. Bitte erneut versuchen.')
            return redirect(url_for('setup_2fa'))

    conn.close()

    # Neuen Geheimschluessel erzeugen (nur temporaer in der Sitzung, noch nicht gespeichert)
    secret = pyotp.random_base32()
    session['pending_totp_secret'] = secret

    provisioning_uri = pyotp.TOTP(secret).provisioning_uri(
        name=current_user.username, issuer_name="HimbeerePi")

    qr_img = qrcode.make(provisioning_uri)
    buf = io.BytesIO()
    qr_img.save(buf, format='PNG')
    qr_base64 = base64.b64encode(buf.getvalue()).decode('ascii')

    return render_template('setup_2fa.html', already_enabled=False,
                           qr_base64=qr_base64, secret=secret)


@app.route('/disable_2fa', methods=['POST'])
@login_required
def disable_2fa():
    """Deaktiviert 2FA wieder - erfordert das aktuelle Passwort zur Bestaetigung, damit nicht
    z.B. ueber ein kurz unbeaufsichtigtes, eingeloggtes Geraet einfach abgeschaltet wird."""
    password = request.form.get('password', '')
    conn = get_db_connection()
    user = conn.execute('SELECT * FROM users WHERE id = ?', (current_user.id,)).fetchone()

    if user and check_password_hash(user['password_hash'], password):
        conn.execute('UPDATE users SET totp_secret = NULL, totp_enabled = 0 WHERE id = ?',
                     (current_user.id,))
        conn.commit()
        flash('Zwei-Faktor-Authentifizierung wurde deaktiviert.')
    else:
        flash('Falsches Passwort - 2FA wurde nicht deaktiviert.')

    conn.close()
    return redirect(url_for('index'))


@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))


@app.route('/logout_others', methods=['POST'])
@login_required
def logout_others():
    """Meldet alle anderen Browser und Geraete ab (z.B. nach einem verlorenen Handy).
    Diese Sitzung bekommt die neue Nummer und bleibt angemeldet."""
    conn = get_db_connection()
    session['sv'] = bump_session_version(conn, current_user.id)
    conn.close()
    flash('Alle anderen Geräte wurden abgemeldet.')
    return redirect(url_for('setup_2fa'))


@app.route('/')
@app.route('/folder/<int:folder_id>')
@login_required
def index(folder_id=None):
    conn = get_db_connection()

    current_folder = None
    if folder_id:
        current_folder = conn.execute('SELECT * FROM folders WHERE id = ? AND user_id = ?',
                                       (folder_id, current_user.id)).fetchone()

    if folder_id:
        folders = conn.execute('SELECT * FROM folders WHERE parent_id = ? AND user_id = ? AND deleted_at IS NULL',
                                (folder_id, current_user.id)).fetchall()
        files = conn.execute('SELECT * FROM files WHERE folder_id = ? AND user_id = ? AND deleted_at IS NULL',
                              (folder_id, current_user.id)).fetchall()
    else:
        folders = conn.execute('SELECT * FROM folders WHERE parent_id IS NULL AND user_id = ? AND deleted_at IS NULL',
                                (current_user.id,)).fetchall()
        files = conn.execute('SELECT * FROM files WHERE folder_id IS NULL AND user_id = ? AND deleted_at IS NULL',
                              (current_user.id,)).fetchall()

    # --- Live-Suche: ?q=... durchsucht ALLE eigenen Dateien und Ordner (nicht nur den aktuellen
    # Ordner) nach dem Namen. Gross-/Kleinschreibung egal, auch bei Umlauten (casefold in Python,
    # weil SQLite-LIKE Umlaute nicht zuverlaessig vergleicht). Eine Abfrage je Tabelle.
    search_query = (request.args.get('q') or '').strip()[:100]
    search = None
    if search_query:
        needle = search_query.casefold()
        folder_rows = conn.execute('SELECT id, name, parent_id FROM folders WHERE user_id = ? AND deleted_at IS NULL',
                                   (current_user.id,)).fetchall()
        folder_paths = build_folder_paths(folder_rows)
        folders = [dict(f, location=folder_paths.get(f['parent_id'], 'Hauptverzeichnis') if f['parent_id'] else 'Hauptverzeichnis',
                        location_id=f['parent_id'])
                   for f in conn.execute('SELECT * FROM folders WHERE user_id = ? AND deleted_at IS NULL', (current_user.id,))
                   if needle in f['name'].casefold() and f['id'] in folder_paths][:50]
        files = [dict(f, location=folder_paths[f['folder_id']] if f['folder_id'] else 'Hauptverzeichnis',
                      location_id=f['folder_id'])
                 for f in conn.execute('SELECT * FROM files WHERE user_id = ? AND deleted_at IS NULL', (current_user.id,))
                 if needle in f['original_name'].casefold() and (f['folder_id'] is None or f['folder_id'] in folder_paths)]
        search = {'q': search_query, 'total_files': len(files), 'total_folders': len(folders)}

    # Groesse jedes angezeigten Ordners berechnen (inkl. Unterordner) - in einem Durchgang fuer
    # alle Ordner des Benutzers (siehe compute_all_folder_sizes), statt einer rekursiven
    # Abfrage-Kette pro einzelnem angezeigten Ordner.
    folder_sizes = compute_all_folder_sizes(conn, current_user.id)
    folders = [dict(f, size_mb=folder_sizes.get(f['id'], 0)) for f in folders]

    breadcrumb_path = get_breadcrumb_path(conn, current_folder, current_user.id) if current_folder else []

    # Bei AJAX-Ordnernavigation wird nur der Inhaltsbereich (_content.html) zurueckgeliefert,
    # der die Speicherplatz-Kopfzeile gar nicht enthaelt (die lebt ausschliesslich in index.html) -
    # die Berechnung (Aufschluesselung nach allen Benutzern) sparen wir uns in diesem Fall
    # komplett, statt sie bei jedem einzelnen Ordnerklick umsonst durchzufuehren.
    is_partial = request.headers.get('X-Partial-Request') == 'true'
    disk_info = get_disk_info(conn) if not is_partial else None
    conn.close()

    # --- Sortierung ---
    files = [dict(f) for f in files]
    sort = request.args.get('sort', 'name_asc')

    def sort_key_type(f):
        name = f['original_name']
        ext = name.rsplit('.', 1)[-1].lower() if '.' in name else ''
        return (ext, name.lower())

    if sort == 'name_desc':
        files.sort(key=lambda f: f['original_name'].lower(), reverse=True)
    elif sort == 'date_asc':
        files.sort(key=lambda f: f['id'])
    elif sort == 'date_desc':
        files.sort(key=lambda f: f['id'], reverse=True)
    elif sort == 'size_desc':
        files.sort(key=lambda f: (f['size_mb'] or 0, f['original_name'].lower()), reverse=True)
    elif sort == 'size_asc':
        files.sort(key=lambda f: (f['size_mb'] or 0, f['original_name'].lower()))
    elif sort == 'type_asc':
        files.sort(key=sort_key_type)
    elif sort == 'type_desc':
        files.sort(key=sort_key_type, reverse=True)
    else:
        sort = 'name_asc'
        files.sort(key=lambda f: f['original_name'].lower())

    # --- Seitenauswahl ---
    # Eine feste Anzahl Dateien pro Seite haelt die "Inhalte"-Karte in einer vorhersehbaren
    # Groesse, statt bei sehr grossen Ordnern (mehrere tausend Dateien) beliebig lang zu werden.
    PAGE_SIZE = 30
    total_files = len(files)
    total_pages = max(1, (total_files + PAGE_SIZE - 1) // PAGE_SIZE)
    page = request.args.get('page', 1, type=int) or 1
    page = max(1, min(page, total_pages))
    start = (page - 1) * PAGE_SIZE
    files_page = files[start:start + PAGE_SIZE]

    pagination = {
        'page': page,
        'total_pages': total_pages,
        'total_files': total_files,
        'sort': sort,
        'has_prev': page > 1,
        'has_next': page < total_pages
    }

    # Bei AJAX-Ordnernavigation (siehe JS: navigateTo()) wird nur der Inhaltsbereich
    # zurueckgeliefert, nicht die komplette Seite - dadurch bleibt der Rest der Seite
    # (inkl. laufender Uploads im schwebenden Panel) beim Ordnerwechsel erhalten.
    if is_mobile_device():
        template = 'mobile_index.html'
    else:
        template = '_content.html' if is_partial else 'index.html'

    return render_template(template,
                           view_mode='folder',
                           files=files_page,
                           folders=folders,
                           current_folder=current_folder,
                           breadcrumb_path=breadcrumb_path,
                           username=current_user.username,
                           disk=disk_info,
                           pagination=pagination,
                           search=search)


# Angezeigte Version (Datei VERSION im Repo-Stamm bzw. neben app.py)
def _read_app_version():
    here = os.path.dirname(os.path.abspath(__file__))
    for path in (os.path.join(here, 'VERSION'), os.path.join(here, '..', 'VERSION')):
        try:
            with open(path, encoding='utf-8') as f:
                value = f.read().strip()[:40]
                if value:
                    return 'HimbeerePi ' + value
        except OSError:
            pass
    return None


APP_VERSION = _read_app_version()


@app.context_processor
def inject_app_version():
    return {'app_version': APP_VERSION}


def collect_system_stats(include_backup):
    """Aktuelle Auslastungswerte des Pi fuer die Web-Sidebar (/system_stats).

    Lese-/Schreib- und Netzwerkwerte sind kumulierte Zaehler seit Systemstart - die eigentliche
    Rate (Bytes/Sekunde) berechnet der Browser aus der Differenz zweier Abfragen.
    include_backup: Backup-Status nur fuer Benutzer mit sichtbarer Pi-Status-Sidebar."""
    cpu_percent = psutil.cpu_percent(interval=None)
    cpu_percent_per_core = psutil.cpu_percent(interval=0, percpu=True)
    mem = psutil.virtual_memory()
    swap = psutil.swap_memory()
    temp_c = get_cpu_temperature()

    try:
        load1, load5, load15 = os.getloadavg()
    except (OSError, AttributeError):
        load1 = load5 = load15 = None

    process_count = len(psutil.pids())

    try:
        freq = psutil.cpu_freq()
        cpu_freq_mhz = round(freq.current) if freq else None
    except Exception:
        cpu_freq_mhz = None

    # vcgencmd-Werte aendern sich nicht schlagartig - ein kurzer Cache erspart einen Teil der
    # externen Prozess-Aufrufe, die sonst bei jedem 5-Sekunden-Poll der Live-Sidebar neu
    # ausgefuehrt wuerden.
    throttled = get_cached_value('throttle_status', 10, get_throttle_status)

    disk_read_bytes, disk_write_bytes = get_disk_io_bytes()
    net_recv_bytes, net_sent_bytes = get_network_io_bytes()

    uptime_seconds = int(time.time() - psutil.boot_time())
    days, rem = divmod(uptime_seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    if days > 0:
        uptime_str = f"{days}d {hours}h {minutes}m"
    else:
        uptime_str = f"{hours}h {minutes}m"

    conn = get_db_connection()
    # Dateizahl und Energie-Summe aendern sich nur bei Upload/Loeschung bzw. einmal pro Minute
    # (track_energy.py-Cronjob) - trotzdem wurden sie bisher bei jedem 5-Sekunden-Poll komplett
    # neu berechnet. Ein kurzer Cache vermeidet die wiederholten Abfragen, ohne dass die Anzeige
    # spuerbar veralteter wirkt.
    total_files = get_cached_value(
        'total_files', 30,
        lambda: conn.execute('SELECT COUNT(*) as cnt FROM files WHERE deleted_at IS NULL').fetchone()['cnt'])
    energy = get_cached_value('energy_summary', 30, lambda: get_energy_summary(conn))
    conn.close()

    # Backup-Status nur fuer Benutzer mit sichtbarer Pi-Status-Sidebar (show_system_stats)
    # berechnen - andere Benutzer bekommen diese Daten gar nicht erst mitgeliefert,
    # nicht nur in der Oberflaeche versteckt. Backups laufen hoechstens einmal taeglich - die bisherige
    # Neuberechnung (Verzeichnislisting + komplettes Einlesen der Log-Dateien) bei jedem
    # 5-Sekunden-Poll war unnoetig, ein kurzer Cache reicht voellig.
    backup_status = get_cached_value('backup_status', 30, get_backup_status) if include_backup else None

    pi_power_watts = get_cached_value('pi_power_watts', 10, get_pi_power_watts)
    total_power_watts = round(pi_power_watts + HDD_WATTS_ESTIMATE, 2) if pi_power_watts is not None else None

    return {
        'cpu_percent': round(cpu_percent, 1),
        'cpu_percent_per_core': [round(c, 1) for c in cpu_percent_per_core],
        'cpu_freq_mhz': cpu_freq_mhz,
        'ram_used_mb': round(mem.used / (1024 * 1024)),
        'ram_total_mb': round(mem.total / (1024 * 1024)),
        'ram_percent': round(mem.percent, 1),
        'swap_used_mb': round(swap.used / (1024 * 1024)),
        'swap_total_mb': round(swap.total / (1024 * 1024)),
        'swap_percent': round(swap.percent, 1),
        'temp_c': temp_c,
        'uptime': uptime_str,
        'load1': round(load1, 2) if load1 is not None else None,
        'load5': round(load5, 2) if load5 is not None else None,
        'load15': round(load15, 2) if load15 is not None else None,
        'process_count': process_count,
        'throttled': throttled,
        'total_files': total_files,
        'energy': energy,
        'backup_status': backup_status,
        'version': APP_VERSION,
        'pi_power_watts': pi_power_watts,
        'total_power_watts': total_power_watts,
        'disk_read_bytes': disk_read_bytes,
        'disk_write_bytes': disk_write_bytes,
        'net_recv_bytes': net_recv_bytes,
        'net_sent_bytes': net_sent_bytes,
        'timestamp': time.time()
    }


@app.route('/system_stats')
@login_required
def system_stats():
    """Auslastungswerte fuer die Live-Sidebar, wird per JS alle 5 s abgefragt."""
    return jsonify(collect_system_stats(current_user.show_system_stats))


@app.route('/create_folder', methods=['POST'])
@login_required
def create_folder():
    folder_name = sanitize_display_name(request.form.get('folder_name'))
    parent_id = request.form.get('parent_id') or None

    if folder_name:
        conn = get_db_connection()
        conn.execute('INSERT INTO folders (name, parent_id, user_id) VALUES (?, ?, ?)',
                     (folder_name, parent_id, current_user.id))
        conn.commit()
        conn.close()

    if parent_id:
        return redirect(url_for('index', folder_id=parent_id))
    return redirect(url_for('index'))


@app.route('/upload', methods=['POST'])
@login_required
def upload_file():
    folder_id = request.form.get('folder_id') or None
    folder_id = int(folder_id) if folder_id else None

    files = request.files.getlist('file')
    relative_paths = request.form.getlist('relative_path')
    extract_zip = request.form.get('extract_zip') == '1'

    if not files or all(f.filename == '' for f in files):
        return redirect(request.url)

    conn = get_db_connection()
    # Cache, damit derselbe Unterordner-Pfad innerhalb eines Upload-Vorgangs nicht mehrfach angelegt wird
    folder_cache = {}

    try:
        for i, file in enumerate(files):
            if file.filename == '':
                continue

            rel_path = relative_paths[i] if i < len(relative_paths) else ''
            target_folder_id = folder_id

            # Falls die Datei aus einem Ordner-Upload/Drag&Drop stammt, enthaelt rel_path z.B.
            # "Urlaub2026/Tag1/foto.jpg" - wir bilden die Ordnerstruktur entsprechend nach.
            if rel_path and '/' in rel_path:
                segments = [s for s in rel_path.split('/')[:-1] if s]
                walk_id = folder_id
                for seg in segments:
                    cache_key = (walk_id, seg)
                    if cache_key in folder_cache:
                        walk_id = folder_cache[cache_key]
                    else:
                        walk_id = get_or_create_subfolder(conn, seg, walk_id, current_user.id)
                        folder_cache[cache_key] = walk_id
                target_folder_id = walk_id

            original_name = secure_filename(file.filename)
            ext = original_name.rsplit('.', 1)[-1].lower() if '.' in original_name else ''

            if ext == 'zip' and extract_zip:
                temp_zip_path = os.path.join(app.config['UPLOAD_FOLDER'], f"temp_zip_{uuid.uuid4().hex}.zip")
                try:
                    file.save(temp_zip_path)
                except OSError as e:
                    print(f"Fehler beim Speichern von ZIP {original_name}: {e}")
                    continue
                try:
                    if current_user.storage_quota_mb:
                        with zipfile.ZipFile(temp_zip_path, 'r') as zf_check:
                            total_uncompressed_mb = sum(i.file_size for i in zf_check.infolist() if not i.is_dir()) / (1024 * 1024)
                        used_mb = get_user_total_usage_mb(conn, current_user.id)
                        if used_mb + total_uncompressed_mb > current_user.storage_quota_mb:
                            os.remove(temp_zip_path)
                            return "Speicherlimit erreicht - ZIP wurde nicht hochgeladen.", 413
                    import_zip_with_structure(conn, temp_zip_path, target_folder_id, current_user.id,
                                               app.config['UPLOAD_FOLDER'], app.config['THUMBNAIL_FOLDER'],
                                               IMAGE_EXTENSIONS, create_thumbnail)
                except zipfile.BadZipFile:
                    pass
                except OSError as e:
                    print(f"Fehler beim Verarbeiten von ZIP {original_name}: {e}")
                finally:
                    if os.path.exists(temp_zip_path):
                        os.remove(temp_zip_path)
                continue

            unique_filename = f"{uuid.uuid4().hex}_{original_name}"
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)

            try:
                file.save(filepath)
                size_mb = round(os.path.getsize(filepath) / (1024 * 1024), 2)
            except OSError as e:
                print(f"Fehler beim Speichern von {original_name}: {e}")
                continue

            # Speicherlimit pruefen (nur bei Benutzern mit gesetztem Kontingent, z.B. storage_quota_mb)
            if current_user.storage_quota_mb:
                used_mb = get_user_total_usage_mb(conn, current_user.id)
                if used_mb + size_mb > current_user.storage_quota_mb:
                    os.remove(filepath)
                    return "Speicherlimit erreicht - Datei wurde nicht hochgeladen.", 413

            if ext in IMAGE_EXTENSIONS:
                # Thumbnail-Erstellung laeuft im Hintergrund-Thread, damit der Request
                # nicht auf pdftoppm/exiftool/dcraw warten muss und der Worker sofort
                # wieder fuer andere Anfragen frei ist.
                submit_thumbnail_job(create_thumbnail, unique_filename, ext)

            conn.execute('INSERT INTO files (filename, original_name, size_mb, folder_id, user_id) VALUES (?, ?, ?, ?, ?)',
                         (unique_filename, original_name, size_mb, target_folder_id, current_user.id))

            # Drosselung bei sehr grossen Batch-Uploads: Falls sich in der Thumbnail-Warteschlange
            # bereits mehrere Dateien stauen (der Hintergrund-Pool mit nur 1 gleichzeitigem Job kommt
            # nicht hinterher), wird der naechste Datei-Empfang bewusst leicht verzoegert. Das gibt
            # dem System Zeit, RAM/Swap-Druck abzubauen, bevor noch mehr Dateien nachkommen - verhindert
            # ein Aufschaukeln des Speicherverbrauchs bei mehreren tausend Dateien in kurzer Folge.
            # Bei normalen, kleinen Uploads (Warteschlange leer) greift das gar nicht.
            pending_thumbnails = get_pending_thumbnail_count()
            if pending_thumbnails > 5:
                time.sleep(min(pending_thumbnails * 0.05, 2.0))

        conn.commit()
    finally:
        conn.close()

    if folder_id:
        return redirect(url_for('index', folder_id=folder_id))
    return redirect(url_for('index'))


@app.route('/download/<int:file_id>')
@login_required
def download_file(file_id):
    conn = get_db_connection()
    file_record = conn.execute('SELECT * FROM files WHERE id = ? AND user_id = ?',
                                (file_id, current_user.id)).fetchone()
    conn.close()

    if file_record:
        return send_from_directory(app.config['UPLOAD_FOLDER'],
                                    file_record['filename'],
                                    download_name=file_record['original_name'],
                                    as_attachment=True)
    return "Datei nicht gefunden", 404


@app.route('/preview/<int:file_id>')
@login_required
def preview_file(file_id):
    conn = get_db_connection()
    file_record = conn.execute('SELECT * FROM files WHERE id = ? AND user_id = ?',
                                (file_id, current_user.id)).fetchone()
    conn.close()

    if file_record:
        ext = file_record['original_name'].rsplit('.', 1)[-1].lower() if '.' in file_record['original_name'] else ''

        # Fuer Formate, die Browser nicht nativ darstellen koennen (CR3/CR2-RAWs, HEIC/HEIF),
        # wird die groessere extrahierte Vorschau ausgeliefert (nicht das kleine Listen-Thumbnail,
        # das waere fuer die Grossansicht viel zu klein und unscharf).
        if ext in ('cr3', 'cr2', 'heic', 'heif'):
            preview_filename = file_record['filename'] + '_preview.jpg'
            preview_path = os.path.join(app.config['THUMBNAIL_FOLDER'], preview_filename)
            if os.path.exists(preview_path):
                return send_from_directory(app.config['THUMBNAIL_FOLDER'], preview_filename, as_attachment=False)
            # Fallback auf das kleine Thumbnail, falls die grosse Vorschau noch nicht fertig ist
            thumb_filename = file_record['filename'] + '.jpg'
            thumb_path = os.path.join(app.config['THUMBNAIL_FOLDER'], thumb_filename)
            if os.path.exists(thumb_path):
                return send_from_directory(app.config['THUMBNAIL_FOLDER'], thumb_filename, as_attachment=False)

        return send_upload(file_record)
    return "Datei nicht gefunden", 404


@app.route('/thumbnail/<int:file_id>')
@login_required
def get_thumbnail(file_id):
    conn = get_db_connection()
    file_record = conn.execute('SELECT * FROM files WHERE id = ? AND user_id = ?',
                                (file_id, current_user.id)).fetchone()
    conn.close()

    if file_record:
        thumb_filename = file_record['filename'] + '.jpg'
        thumb_path = os.path.join(app.config['THUMBNAIL_FOLDER'], thumb_filename)
        if os.path.exists(thumb_path):
            return send_from_directory(app.config['THUMBNAIL_FOLDER'], thumb_filename)
        # Fallback auf Originaldatei, falls das Thumbnail noch nicht fertig ist
        # (Hintergrund-Erstellung laeuft ggf. noch) oder gar nicht existiert
        return send_upload(file_record)
    return "Datei nicht gefunden", 404


# --- EXIF-/Bilddaten fuer die Grossansicht ---------------------------------------------------
# Wird erst beim Klick auf "Details" im Bildbetrachter geladen (nicht beim Auflisten), damit
# grosse Ordner nicht pro Bild einen exiftool-Prozess starten.
EXIF_EXTENSIONS = {'jpg', 'jpeg', 'png', 'webp', 'tif', 'tiff', 'heic', 'heif', 'cr2', 'cr3'}
EXIF_TAGS = ['DateTimeOriginal', 'CreateDate', 'Make', 'Model', 'LensModel', 'ExposureTime',
             'FNumber', 'ISO', 'FocalLength', 'ImageWidth', 'ImageHeight', 'GPSLatitude', 'GPSLongitude']


def _read_exif_exiftool(path):
    """exiftool kann auch RAW (CR2/CR3) und HEIC. -n liefert Zahlen statt Text (GPS dezimal)."""
    if not shutil.which('exiftool'):
        return None
    try:
        out = subprocess.run(['exiftool', '-j', '-n'] + ['-' + tag for tag in EXIF_TAGS] + [path],
                             capture_output=True, text=True, timeout=15)
        data = json.loads(out.stdout or '[]')
        return data[0] if data else None
    except (subprocess.SubprocessError, ValueError, OSError):
        return None


def _read_exif_pillow(path):
    """Rueckfallebene ohne exiftool (z.B. lokale Tests) - reicht fuer JPEG/PNG/WEBP/HEIC."""
    def num(v):
        try:
            return float(v)
        except (TypeError, ValueError, ZeroDivisionError):
            return None

    def dms(values, ref):
        try:
            d, m, s = (float(x) for x in values)
            val = d + m / 60 + s / 3600
            return -val if ref in ('S', 'W') else val
        except (TypeError, ValueError, ZeroDivisionError):
            return None
    try:
        with Image.open(path) as img:
            exif = img.getexif()
            sub = exif.get_ifd(0x8769)
            gps = exif.get_ifd(0x8825)
            data = {'ImageWidth': img.size[0], 'ImageHeight': img.size[1],
                    'Make': exif.get(271), 'Model': exif.get(272),
                    'DateTimeOriginal': sub.get(36867) or exif.get(306),
                    'ExposureTime': num(sub.get(33434)), 'FNumber': num(sub.get(33437)),
                    'ISO': sub.get(34855), 'FocalLength': num(sub.get(37386)),
                    'LensModel': sub.get(42036)}
            if gps.get(2) and gps.get(4):
                data['GPSLatitude'] = dms(gps.get(2), gps.get(1))
                data['GPSLongitude'] = dms(gps.get(4), gps.get(3))
            return data
    except Exception:
        return None


def _format_exif(raw):
    """Macht aus den Rohwerten eine Liste [(Bezeichnung, Wert)] in lesbarem Deutsch."""
    felder = []
    if not raw:
        return felder, None

    def text(v):
        return str(v).strip().strip('\x00') if v not in (None, '') else ''

    datum = text(raw.get('DateTimeOriginal') or raw.get('CreateDate'))
    if datum:
        try:
            dt = datetime.strptime(datum[:19], '%Y:%m:%d %H:%M:%S')
            datum = dt.strftime('%d.%m.%Y, %H:%M Uhr')
        except ValueError:
            pass
        felder.append(('Aufnahme', datum))
    make, model = text(raw.get('Make')), text(raw.get('Model'))
    kamera = model if model.lower().startswith(make.lower()) else (make + ' ' + model).strip()
    if kamera:
        felder.append(('Kamera', kamera))
    if text(raw.get('LensModel')):
        felder.append(('Objektiv', text(raw.get('LensModel'))))
    einstellungen = []
    try:
        belichtung = float(raw.get('ExposureTime'))
        einstellungen.append('1/%d s' % round(1 / belichtung) if 0 < belichtung < 1 else ('%g s' % belichtung))
    except (TypeError, ValueError, ZeroDivisionError):
        pass
    try:
        einstellungen.append('f/%g' % round(float(raw.get('FNumber')), 1))
    except (TypeError, ValueError):
        pass
    if text(raw.get('ISO')):
        einstellungen.append('ISO ' + text(raw.get('ISO')))
    try:
        einstellungen.append('%g mm' % round(float(raw.get('FocalLength')), 1))
    except (TypeError, ValueError):
        pass
    if einstellungen:
        felder.append(('Einstellungen', ' \u00b7 '.join(einstellungen)))
    try:
        w, h = int(raw.get('ImageWidth')), int(raw.get('ImageHeight'))
        felder.append(('Aufl\u00f6sung', '%d \u00d7 %d px (%s MP)' % (w, h, ('%.1f' % (w * h / 1e6)).replace('.', ','))))
    except (TypeError, ValueError):
        pass
    gps = None
    try:
        lat, lon = float(raw.get('GPSLatitude')), float(raw.get('GPSLongitude'))
        if lat or lon:
            gps = {'lat': round(lat, 6), 'lon': round(lon, 6)}
            felder.append(('Ort', '%.5f\u00b0 %s, %.5f\u00b0 %s' % (abs(lat), 'N' if lat >= 0 else 'S',
                                                          abs(lon), 'O' if lon >= 0 else 'W')))
    except (TypeError, ValueError):
        pass
    return felder, gps


@app.route('/exif/<int:file_id>')
@login_required
def file_exif(file_id):
    conn = get_db_connection()
    record = conn.execute('SELECT filename, original_name FROM files WHERE id = ? AND user_id = ? AND deleted_at IS NULL',
                          (file_id, current_user.id)).fetchone()
    conn.close()
    if not record:
        return jsonify({'error': 'Datei nicht gefunden'}), 404
    name = record['original_name']
    ext = name.rsplit('.', 1)[-1].lower() if '.' in name else ''
    if ext not in EXIF_EXTENSIONS:
        return jsonify({'name': name, 'felder': [], 'gps': None,
                        'hinweis': 'F\u00fcr dieses Dateiformat gibt es keine Bilddaten.'})
    path = os.path.join(app.config['UPLOAD_FOLDER'], record['filename'])
    if not os.path.isfile(path):
        return jsonify({'error': 'Originaldatei fehlt auf der Festplatte'}), 404
    felder, gps = _format_exif(_read_exif_exiftool(path) or _read_exif_pillow(path))
    hat_aufnahmedaten = any(label != 'Aufl\u00f6sung' for label, _ in felder)
    return jsonify({'name': name, 'felder': felder, 'gps': gps,
                    'hinweis': '' if hat_aufnahmedaten else 'Keine Aufnahmedaten (EXIF) gespeichert - z.B. bei Screenshots oder bearbeiteten Bildern.'})


@app.route('/delete_file/<int:file_id>', methods=['POST'])
@login_required
def delete_file(file_id):
    """Verschiebt eine Datei in den Papierkorb (Soft Delete)."""
    conn = get_db_connection()
    now = datetime.now().isoformat()
    conn.execute('UPDATE files SET deleted_at = ? WHERE id = ? AND user_id = ?',
                 (now, file_id, current_user.id))
    conn.commit()

    file_record = conn.execute('SELECT * FROM files WHERE id = ?', (file_id,)).fetchone()
    conn.close()

    folder_id = file_record['folder_id'] if file_record else None
    if folder_id:
        return redirect(url_for('index', folder_id=folder_id))
    return redirect(url_for('index'))


def trash_folder_recursive(conn, folder_id, user_id, timestamp):
    """Verschiebt rekursiv einen Ordner und seinen gesamten Inhalt in den Papierkorb."""
    conn.execute('UPDATE files SET deleted_at = ? WHERE folder_id = ? AND user_id = ? AND deleted_at IS NULL',
                 (timestamp, folder_id, user_id))

    subfolders = conn.execute('SELECT * FROM folders WHERE parent_id = ? AND user_id = ? AND deleted_at IS NULL',
                               (folder_id, user_id)).fetchall()
    for sub in subfolders:
        trash_folder_recursive(conn, sub['id'], user_id, timestamp)

    conn.execute('UPDATE folders SET deleted_at = ? WHERE id = ? AND user_id = ?',
                 (timestamp, folder_id, user_id))


@app.route('/delete_folder/<int:folder_id>', methods=['POST'])
@login_required
def delete_folder(folder_id):
    """Verschiebt einen Ordner inkl. Inhalt in den Papierkorb (Soft Delete)."""
    conn = get_db_connection()
    folder = conn.execute('SELECT * FROM folders WHERE id = ? AND user_id = ?',
                           (folder_id, current_user.id)).fetchone()

    if folder:
        parent_id = folder['parent_id']
        now = datetime.now().isoformat()
        trash_folder_recursive(conn, folder_id, current_user.id, now)
        conn.commit()
        conn.close()

        if parent_id:
            return redirect(url_for('index', folder_id=parent_id))
        return redirect(url_for('index'))

    conn.close()
    return redirect(url_for('index'))


@app.route('/move_file/<int:file_id>', methods=['POST'])
@login_required
def move_file(file_id):
    target_folder_id = request.form.get('target_folder_id')
    target_folder_id = None if target_folder_id in ('root', '', None) else int(target_folder_id)

    conn = get_db_connection()

    # Zielordner muss existieren und dem aktuellen Benutzer gehoeren - sonst wuerde die
    # Datei in eine fremde/nicht existierende folder_id verschoben und waere danach in der
    # eigenen Ordneransicht nicht mehr auffindbar (die Ordneransicht filtert Ordner ebenfalls
    # nach user_id).
    if target_folder_id is not None:
        target_folder = conn.execute('SELECT id FROM folders WHERE id = ? AND user_id = ?',
                                      (target_folder_id, current_user.id)).fetchone()
        if not target_folder:
            conn.close()
            return "Zielordner nicht gefunden", 404

    file_record = conn.execute('SELECT * FROM files WHERE id = ? AND user_id = ?',
                                (file_id, current_user.id)).fetchone()

    if file_record:
        conn.execute('UPDATE files SET folder_id = ? WHERE id = ?', (target_folder_id, file_id))
        conn.commit()

    current_folder = file_record['folder_id'] if file_record else None
    conn.close()

    if current_folder:
        return redirect(url_for('index', folder_id=current_folder))
    return redirect(url_for('index'))


def sanitize_display_name(name):
    """Bereinigt einen Anzeigenamen (Umbenennen, neue Ordner, Ordner aus ZIP- und Ordner-Uploads).
    Wird NICHT als Dateisystempfad genutzt (physische Dateien behalten ihren UUID-Namen), daher
    reicht eine leichte Bereinigung statt des strengen secure_filename(). "." und ".." werden
    ersetzt - sie landen sonst als Pfadteil im ZIP-Download ("../..") und koennten beim
    Entpacken mit alten Programmen ausserhalb des Zielordners schreiben."""
    name = (name or '').strip()
    name = name.replace('/', '-').replace('\\', '-')
    if name in ('.', '..'):
        name = name.replace('.', '_')
    return name[:255]


@app.route('/rename_file/<int:file_id>', methods=['POST'])
@login_required
def rename_file(file_id):
    new_name = sanitize_display_name(request.form.get('new_name'))
    if not new_name:
        return "Name darf nicht leer sein", 400

    conn = get_db_connection()
    file_record = conn.execute('SELECT * FROM files WHERE id = ? AND user_id = ?',
                                (file_id, current_user.id)).fetchone()
    if file_record:
        conn.execute('UPDATE files SET original_name = ? WHERE id = ? AND user_id = ?',
                     (new_name, file_id, current_user.id))
        conn.commit()
    conn.close()
    return ('', 204)


@app.route('/rename_folder/<int:folder_id>', methods=['POST'])
@login_required
def rename_folder(folder_id):
    new_name = sanitize_display_name(request.form.get('new_name'))
    if not new_name:
        return "Name darf nicht leer sein", 400

    conn = get_db_connection()
    folder = conn.execute('SELECT * FROM folders WHERE id = ? AND user_id = ?',
                           (folder_id, current_user.id)).fetchone()
    if folder:
        conn.execute('UPDATE folders SET name = ? WHERE id = ? AND user_id = ?',
                     (new_name, folder_id, current_user.id))
        conn.commit()
    conn.close()
    return ('', 204)


def is_same_or_descendant(conn, folder_id, ancestor_id, user_id):
    """Prueft, ob folder_id identisch mit ancestor_id ist oder irgendwo darunter liegt.
    Verhindert, dass ein Ordner in sich selbst oder einen seiner eigenen Unterordner
    verschoben wird (das wuerde eine Endlos-Schleife in der Ordnerstruktur erzeugen)."""
    current = folder_id
    seen = set()
    while current is not None:
        if current == ancestor_id:
            return True
        if current in seen:
            break
        seen.add(current)
        row = conn.execute('SELECT parent_id FROM folders WHERE id = ? AND user_id = ?',
                           (current, user_id)).fetchone()
        current = row['parent_id'] if row else None
    return False


@app.route('/bulk_move', methods=['POST'])
@login_required
def bulk_move():
    file_ids = request.form.getlist('file_ids')
    folder_ids = request.form.getlist('folder_ids')
    target_folder_id = request.form.get('target_folder_id')
    target_folder_id = None if target_folder_id in ('root', '', None) else int(target_folder_id)

    conn = get_db_connection()

    # Zielordner muss existieren und dem aktuellen Benutzer gehoeren (siehe move_file())
    if target_folder_id is not None:
        target_folder = conn.execute('SELECT id FROM folders WHERE id = ? AND user_id = ?',
                                      (target_folder_id, current_user.id)).fetchone()
        if not target_folder:
            conn.close()
            return "Zielordner nicht gefunden", 404

    for fid_str in folder_ids:
        fid = int(fid_str)
        # Ordner ueberspringen, die nicht in sich selbst / einen eigenen Unterordner verschoben werden koennten
        if target_folder_id is not None and is_same_or_descendant(conn, target_folder_id, fid, current_user.id):
            continue
        conn.execute('UPDATE folders SET parent_id = ? WHERE id = ? AND user_id = ?',
                     (target_folder_id, fid, current_user.id))

    for file_id_str in file_ids:
        conn.execute('UPDATE files SET folder_id = ? WHERE id = ? AND user_id = ?',
                     (target_folder_id, int(file_id_str), current_user.id))

    conn.commit()
    conn.close()
    redirect_to = request.form.get('redirect_to')
    if redirect_to:
        return redirect(redirect_to)
    return ('', 204)


@app.route('/bulk_delete', methods=['POST'])
@login_required
def bulk_delete():
    file_ids = request.form.getlist('file_ids')
    folder_ids = request.form.getlist('folder_ids')
    conn = get_db_connection()
    now = datetime.now().isoformat()

    for file_id_str in file_ids:
        conn.execute('UPDATE files SET deleted_at = ? WHERE id = ? AND user_id = ? AND deleted_at IS NULL',
                     (now, int(file_id_str), current_user.id))

    for folder_id_str in folder_ids:
        trash_folder_recursive(conn, int(folder_id_str), current_user.id, now)

    conn.commit()
    conn.close()
    redirect_to = request.form.get('redirect_to')
    if redirect_to:
        return redirect(redirect_to)
    return ('', 204)


@app.route('/bulk_download', methods=['POST'])
@login_required
def bulk_download():
    file_ids = request.form.getlist('file_ids')
    folder_ids = request.form.getlist('folder_ids')

    conn = get_db_connection()
    zip_buffer = io.BytesIO()

    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        for file_id_str in file_ids:
            f = conn.execute('SELECT * FROM files WHERE id = ? AND user_id = ? AND deleted_at IS NULL',
                             (int(file_id_str), current_user.id)).fetchone()
            if f:
                filepath = os.path.join(app.config['UPLOAD_FOLDER'], f['filename'])
                if os.path.exists(filepath):
                    zf.write(filepath, arcname=sanitize_display_name(f['original_name']) or '_')

        def add_folder_to_zip(folder_id, arc_prefix):
            files_here = conn.execute('SELECT * FROM files WHERE folder_id = ? AND user_id = ? AND deleted_at IS NULL',
                                      (folder_id, current_user.id)).fetchall()
            for f in files_here:
                filepath = os.path.join(app.config['UPLOAD_FOLDER'], f['filename'])
                if os.path.exists(filepath):
                    zf.write(filepath, arcname=arc_prefix + '/' + (sanitize_display_name(f['original_name']) or '_'))
            subfolders = conn.execute('SELECT * FROM folders WHERE parent_id = ? AND user_id = ? AND deleted_at IS NULL',
                                      (folder_id, current_user.id)).fetchall()
            for sub in subfolders:
                add_folder_to_zip(sub['id'], arc_prefix + '/' + (sanitize_display_name(sub['name']) or '_'))

        for folder_id_str in folder_ids:
            fid = int(folder_id_str)
            folder = conn.execute('SELECT * FROM folders WHERE id = ? AND user_id = ?',
                                  (fid, current_user.id)).fetchone()
            if folder:
                add_folder_to_zip(fid, sanitize_display_name(folder['name']) or '_')

    conn.close()
    zip_buffer.seek(0)

    return send_file(zip_buffer, mimetype='application/zip', as_attachment=True,
                     download_name='Download.zip')


# --- PAPIERKORB ---

@app.route('/trash')
@login_required
def trash():
    conn = get_db_connection()

    trashed_files = conn.execute(
        'SELECT * FROM files WHERE user_id = ? AND deleted_at IS NOT NULL ORDER BY deleted_at DESC',
        (current_user.id,)).fetchall()
    trashed_folders = conn.execute(
        'SELECT * FROM folders WHERE user_id = ? AND deleted_at IS NOT NULL ORDER BY deleted_at DESC',
        (current_user.id,)).fetchall()

    def days_left(deleted_at_str):
        deleted_at = datetime.fromisoformat(deleted_at_str)
        expires_at = deleted_at + timedelta(days=90)
        return max((expires_at - datetime.now()).days, 0)

    files_with_days = [dict(f, days_left=days_left(f['deleted_at'])) for f in trashed_files]
    folders_with_days = [dict(f, days_left=days_left(f['deleted_at'])) for f in trashed_folders]

    disk_info = get_disk_info(conn)
    conn.close()

    # Der Papierkorb ist jetzt Teil derselben Seiten-Huelle wie die Ordneransicht (gleicher
    # Header, gleiche Sidebar, gleiches schwebendes Upload-Panel) - nur der Inhaltsbereich
    # wechselt. Das verhindert, dass ein Klick auf "Papierkorb" einen laufenden Upload abbricht.
    template = '_trash_content.html' if request.headers.get('X-Partial-Request') == 'true' else 'index.html'

    return render_template(template,
                           view_mode='trash',
                           files=files_with_days,
                           folders=folders_with_days,
                           current_folder=None,
                           username=current_user.username,
                           disk=disk_info)


@app.route('/restore_file/<int:file_id>', methods=['POST'])
@login_required
def restore_file(file_id):
    conn = get_db_connection()
    conn.execute('UPDATE files SET deleted_at = NULL WHERE id = ? AND user_id = ?',
                 (file_id, current_user.id))
    conn.commit()
    conn.close()
    return redirect(url_for('trash'))


@app.route('/restore_folder/<int:folder_id>', methods=['POST'])
@login_required
def restore_folder(folder_id):
    conn = get_db_connection()

    def restore_recursive(fid):
        conn.execute('UPDATE folders SET deleted_at = NULL WHERE id = ? AND user_id = ?', (fid, current_user.id))
        conn.execute('UPDATE files SET deleted_at = NULL WHERE folder_id = ? AND user_id = ?', (fid, current_user.id))
        subfolders = conn.execute('SELECT id FROM folders WHERE parent_id = ? AND user_id = ?',
                                   (fid, current_user.id)).fetchall()
        for sub in subfolders:
            restore_recursive(sub['id'])

    restore_recursive(folder_id)
    conn.commit()
    conn.close()
    return redirect(url_for('trash'))


@app.route('/delete_permanently/file/<int:file_id>', methods=['POST'])
@login_required
def delete_permanently_file(file_id):
    conn = get_db_connection()
    file_record = conn.execute('SELECT * FROM files WHERE id = ? AND user_id = ?',
                                (file_id, current_user.id)).fetchone()

    if file_record:
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], file_record['filename'])
        if os.path.exists(filepath):
            os.remove(filepath)
        thumbpath = os.path.join(app.config['THUMBNAIL_FOLDER'], file_record['filename'] + '.jpg')
        if os.path.exists(thumbpath):
            os.remove(thumbpath)
        previewpath = os.path.join(app.config['THUMBNAIL_FOLDER'], file_record['filename'] + '_preview.jpg')
        if os.path.exists(previewpath):
            os.remove(previewpath)
        conn.execute('DELETE FROM files WHERE id = ?', (file_id,))
        conn.commit()

    conn.close()
    return redirect(url_for('trash'))


@app.route('/delete_permanently/folder/<int:folder_id>', methods=['POST'])
@login_required
def delete_permanently_folder(folder_id):
    conn = get_db_connection()

    def purge_recursive(fid):
        files = conn.execute('SELECT * FROM files WHERE folder_id = ?', (fid,)).fetchall()
        for f in files:
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], f['filename'])
            if os.path.exists(filepath):
                os.remove(filepath)
            thumbpath = os.path.join(app.config['THUMBNAIL_FOLDER'], f['filename'] + '.jpg')
            if os.path.exists(thumbpath):
                os.remove(thumbpath)
            previewpath = os.path.join(app.config['THUMBNAIL_FOLDER'], f['filename'] + '_preview.jpg')
            if os.path.exists(previewpath):
                os.remove(previewpath)
        conn.execute('DELETE FROM files WHERE folder_id = ?', (fid,))

        subfolders = conn.execute('SELECT id FROM folders WHERE parent_id = ?', (fid,)).fetchall()
        for sub in subfolders:
            purge_recursive(sub['id'])

        conn.execute('DELETE FROM folders WHERE id = ?', (fid,))

    folder = conn.execute('SELECT * FROM folders WHERE id = ? AND user_id = ?',
                           (folder_id, current_user.id)).fetchone()
    if folder:
        purge_recursive(folder_id)
        conn.commit()

    conn.close()
    return redirect(url_for('trash'))


@app.route('/trash/empty', methods=['POST'])
@login_required
def empty_trash():
    """Loescht alle Dateien und Ordner im Papierkorb des Benutzers endgueltig auf einmal."""
    conn = get_db_connection()

    trashed_files = conn.execute(
        'SELECT * FROM files WHERE user_id = ? AND deleted_at IS NOT NULL', (current_user.id,)).fetchall()

    for f in trashed_files:
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], f['filename'])
        if os.path.exists(filepath):
            os.remove(filepath)
        thumbpath = os.path.join(app.config['THUMBNAIL_FOLDER'], f['filename'] + '.jpg')
        if os.path.exists(thumbpath):
            os.remove(thumbpath)
        previewpath = os.path.join(app.config['THUMBNAIL_FOLDER'], f['filename'] + '_preview.jpg')
        if os.path.exists(previewpath):
            os.remove(previewpath)

    conn.execute('DELETE FROM files WHERE user_id = ? AND deleted_at IS NOT NULL', (current_user.id,))
    conn.execute('DELETE FROM folders WHERE user_id = ? AND deleted_at IS NOT NULL', (current_user.id,))
    conn.commit()
    conn.close()

    return redirect(url_for('trash'))


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
