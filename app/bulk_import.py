"""
Eigenstaendiges Import-Skript fuer sehr grosse Datenmengen (z.B. Migration einer alten
Foto-Sammlung), das direkt auf Betriebssystem-Ebene arbeitet - ganz ohne Browser, HTTP,
Sitzungen oder CSRF-Tokens. Nutzt dieselben Funktionen wie die Cloud selbst (Thumbnail-
Erstellung, Ordnerstruktur-Erkennung), importiert diese direkt aus app.py.

Laeuft synchron, eine Datei nach der anderen - das begrenzt die Systemlast von selbst
(kein Aufstauen wie beim parallelen Browser-Upload) und macht den Fortschritt live im
Terminal sichtbar.

WICHTIG: Am besten in einer 'screen'-Sitzung ausfuehren, damit der Import auch bei einer
unterbrochenen SSH-Verbindung im Hintergrund weiterlaeuft:
    screen -S import
    cd /opt/raspicloud/app
    sudo -u raspicloud bash -c 'set -a; . /etc/raspicloud/raspicloud.env; set +a; ../venv/bin/python3 bulk_import.py <quellordner> <benutzername> [ziel-ordner-id]'
    (zum Verlassen der Sitzung, OHNE sie zu beenden: Strg+A, dann D)
    (zum spaeteren Wiedereinklinken: screen -r import)

Bereits importierte Dateien (gleicher Name, gleicher Zielordner) werden automatisch
uebersprungen - das Skript kann also nach einer Unterbrechung einfach erneut gestartet
werden, ohne Duplikate zu erzeugen.

Verwendung:
    python3 bulk_import.py <quellordner> <benutzername> [ziel-ordner-id]

Beispiel:
    python3 bulk_import.py /media/usb/Fotos benutzer1
    python3 bulk_import.py /media/usb/Fotos benutzer1 42
"""
import sys
import os
import uuid
import shutil

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import app as cloud_app


def import_directory(source_dir, target_folder_id, user_id):
    conn = cloud_app.get_db_connection()
    folder_cache = {}

    total_files = sum(len(files) for _, _, files in os.walk(source_dir))
    print(f"Gefunden: {total_files} Dateien in {source_dir}")
    print("Starte Import...")

    imported_count = 0
    skipped_count = 0
    error_count = 0

    for root, dirs, files in os.walk(source_dir):
        rel_path = os.path.relpath(root, source_dir)

        if rel_path == '.':
            current_folder_id = target_folder_id
        else:
            segments = rel_path.split(os.sep)
            walk_id = target_folder_id
            for seg in segments:
                cache_key = (walk_id, seg)
                if cache_key in folder_cache:
                    walk_id = folder_cache[cache_key]
                else:
                    walk_id = cloud_app.get_or_create_subfolder(conn, seg, walk_id, user_id)
                    folder_cache[cache_key] = walk_id
                    conn.commit()
            current_folder_id = walk_id

        for filename in files:
            # Versteckte System-Dateien (z.B. .DS_Store von macOS) ueberspringen
            if filename.startswith('.'):
                skipped_count += 1
                continue

            original_name = cloud_app.secure_filename(filename)
            if not original_name:
                skipped_count += 1
                continue

            # Duplikat-Schutz: Datei mit demselben Namen im selben Zielordner existiert
            # bereits (z.B. weil das Skript vorher unterbrochen wurde) -> ueberspringen
            existing = conn.execute(
                'SELECT id FROM files WHERE original_name = ? AND folder_id IS ? AND user_id = ? AND deleted_at IS NULL',
                (original_name, current_folder_id, user_id)
            ).fetchone()
            if existing:
                skipped_count += 1
                continue

            src_path = os.path.join(root, filename)
            ext = original_name.rsplit('.', 1)[-1].lower() if '.' in original_name else ''
            unique_filename = f"{uuid.uuid4().hex}_{original_name}"
            dest_path = os.path.join(cloud_app.app.config['UPLOAD_FOLDER'], unique_filename)

            try:
                shutil.copy2(src_path, dest_path)
            except Exception as e:
                print(f"  FEHLER beim Kopieren von {src_path}: {e}")
                error_count += 1
                continue

            size_mb = round(os.path.getsize(dest_path) / (1024 * 1024), 2)

            conn.execute(
                'INSERT INTO files (filename, original_name, size_mb, folder_id, user_id) VALUES (?, ?, ?, ?, ?)',
                (unique_filename, original_name, size_mb, current_folder_id, user_id)
            )

            # Thumbnail-Erstellung SYNCHRON (nicht ueber den Hintergrund-Pool) - das Skript
            # arbeitet ohnehin schon eine Datei nach der anderen ab, das bremst sich also
            # von selbst und stapelt keine parallelen Jobs auf.
            if ext in cloud_app.IMAGE_EXTENSIONS:
                cloud_app.create_thumbnail(unique_filename, ext)

            imported_count += 1
            if imported_count % 50 == 0:
                conn.commit()
                print(f"  ... {imported_count}/{total_files} importiert "
                      f"({skipped_count} uebersprungen, {error_count} Fehler)")

    conn.commit()
    conn.close()
    print("")
    print(f"Fertig. {imported_count} importiert, {skipped_count} uebersprungen "
          f"(bereits vorhanden), {error_count} Fehler.")


if __name__ == '__main__':
    if len(sys.argv) < 3:
        print("Verwendung: python3 bulk_import.py <quellordner> <benutzername> [ziel-ordner-id]")
        sys.exit(1)

    source_dir = sys.argv[1]
    username = sys.argv[2]
    target_folder_id = int(sys.argv[3]) if len(sys.argv) > 3 else None

    if not os.path.isdir(source_dir):
        print(f"FEHLER: Quellordner '{source_dir}' existiert nicht.")
        sys.exit(1)

    conn = cloud_app.get_db_connection()
    user_row = conn.execute('SELECT id FROM users WHERE username = ?', (username,)).fetchone()
    conn.close()
    if not user_row:
        print(f"FEHLER: Benutzer '{username}' nicht gefunden.")
        sys.exit(1)
    user_id = user_row['id']

    import_directory(source_dir, target_folder_id, user_id)
