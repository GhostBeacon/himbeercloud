import sqlite3
import os

DB_PATH = os.environ.get("RASPICLOUD_DB", "/var/lib/raspicloud/users.db")
UPLOAD_FOLDER = os.environ.get("RASPICLOUD_DATA_DIR", "/srv/raspicloud")
THUMBNAIL_FOLDER = os.path.join(UPLOAD_FOLDER, "thumbnails")


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    trashed_files = conn.execute(
        "SELECT * FROM files WHERE deleted_at IS NOT NULL").fetchall()

    print(f"Gefunden: {len(trashed_files)} Dateien im Papierkorb (alle Benutzer).")

    removed_count = 0
    for f in trashed_files:
        filepath = os.path.join(UPLOAD_FOLDER, f["filename"])
        if os.path.exists(filepath):
            os.remove(filepath)
            removed_count += 1
        thumbpath = os.path.join(THUMBNAIL_FOLDER, f["filename"] + ".jpg")
        if os.path.exists(thumbpath):
            os.remove(thumbpath)

    conn.execute("DELETE FROM files WHERE deleted_at IS NOT NULL")
    conn.execute("DELETE FROM folders WHERE deleted_at IS NOT NULL")
    conn.commit()
    conn.close()

    print(f"Fertig. {removed_count} physische Dateien entfernt, Datenbank-Eintraege geloescht.")


if __name__ == "__main__":
    main()
