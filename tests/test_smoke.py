"""Rauchtest: Datenbank anlegen, anmelden, hochladen, Papierkorb, Speicherlimit.

    RASPICLOUD_INSECURE_COOKIE=1 python -m unittest discover -s tests
"""
import io
import os
import sqlite3
import sys
import tempfile
import time
import unittest

TMP = tempfile.mkdtemp(prefix='raspicloud-test-')
os.environ.update({
    'RASPICLOUD_DB': os.path.join(TMP, 'users.db'),
    'RASPICLOUD_DATA_DIR': os.path.join(TMP, 'data'),
    'SECRET_KEY': 'test',
    'RASPICLOUD_INSECURE_COOKIE': '1',
})
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'app'))

import manage  # noqa: E402

manage.DB_PATH = os.environ['RASPICLOUD_DB']
manage.init_db()

from werkzeug.security import generate_password_hash  # noqa: E402

_conn = sqlite3.connect(os.environ['RASPICLOUD_DB'])
_conn.execute("INSERT INTO users (username, password_hash) VALUES ('anna', ?)",
              (generate_password_hash('ein-langes-passwort'),))
_conn.execute("INSERT INTO users (username, password_hash, storage_quota_mb, show_system_stats) VALUES ('ben', ?, 1024, 0)",
              (generate_password_hash('ein-langes-passwort'),))
_conn.commit()
_conn.close()

import app as cloud  # noqa: E402

cloud.app.config['WTF_CSRF_ENABLED'] = False


def jpeg():
    from PIL import Image
    buf = io.BytesIO()
    Image.new('RGB', (300, 200), 'red').save(buf, 'JPEG')
    buf.seek(0)
    return buf


class WebTest(unittest.TestCase):
    def setUp(self):
        self.client = cloud.app.test_client()
        r = self.client.post('/login', data={'username': 'anna', 'password': 'ein-langes-passwort'})
        self.assertEqual(r.status_code, 302)

    def test_wrong_password(self):
        r = cloud.app.test_client().post('/login', data={'username': 'anna', 'password': 'falsch'})
        self.assertIn('Ungültiger'.encode(), r.data)

    def test_index_and_stats(self):
        self.assertEqual(self.client.get('/').status_code, 200)
        stats = self.client.get('/system_stats').get_json()
        self.assertIn('db_backup', stats['backup_status'])
        self.assertIn('cpu_percent', stats)

    def test_upload_thumbnail_trash(self):
        r = self.client.post('/upload', data={'file': (jpeg(), 'bild.jpg')}, content_type='multipart/form-data')
        self.assertIn(r.status_code, (200, 302))
        conn = sqlite3.connect(os.environ['RASPICLOUD_DB'])
        file_id = conn.execute("SELECT id FROM files WHERE original_name = 'bild.jpg'").fetchone()[0]
        conn.close()
        for _ in range(20):
            if self.client.get(f'/thumbnail/{file_id}').status_code == 200:
                break
            time.sleep(0.2)
        self.assertEqual(self.client.get(f'/download/{file_id}').status_code, 200)
        self.client.post(f'/delete_file/{file_id}')
        self.assertEqual(self.client.get('/trash').status_code, 200)


class QuotaUserTest(unittest.TestCase):
    def test_no_system_stats_for_limited_user(self):
        client = cloud.app.test_client()
        client.post('/login', data={'username': 'ben', 'password': 'ein-langes-passwort'})
        self.assertEqual(client.get('/').status_code, 200)
        stats = client.get('/system_stats').get_json()
        self.assertIsNone(stats['backup_status'])
        # fremde Datei nicht abrufbar
        conn = sqlite3.connect(os.environ['RASPICLOUD_DB'])
        row = conn.execute("SELECT f.id FROM files f JOIN users u ON u.id = f.user_id WHERE u.username = 'anna'").fetchone()
        conn.close()
        if row:
            self.assertNotEqual(client.get(f'/download/{row[0]}').status_code, 200)


if __name__ == '__main__':
    unittest.main()
