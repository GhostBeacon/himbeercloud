"""Rauchtest: Datenbank anlegen, anmelden, hochladen, Papierkorb, Speicherlimit.

    HIMBEEREPI_INSECURE_COOKIE=1 python -m unittest discover -s tests
"""
import io
import os
import sqlite3
import sys
import tempfile
import time
import unittest
import zipfile

TMP = tempfile.mkdtemp(prefix='himbeerepi-test-')
os.environ.update({
    'HIMBEEREPI_DB': os.path.join(TMP, 'users.db'),
    'HIMBEEREPI_DATA_DIR': os.path.join(TMP, 'data'),
    'SECRET_KEY': 'test',
    'HIMBEEREPI_INSECURE_COOKIE': '1',
})
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'app'))

import manage  # noqa: E402

manage.DB_PATH = os.environ['HIMBEEREPI_DB']
manage.init_db()

from werkzeug.security import generate_password_hash  # noqa: E402

_conn = sqlite3.connect(os.environ['HIMBEEREPI_DB'])
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
        conn = sqlite3.connect(os.environ['HIMBEEREPI_DB'])
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
        conn = sqlite3.connect(os.environ['HIMBEEREPI_DB'])
        row = conn.execute("SELECT f.id FROM files f JOIN users u ON u.id = f.user_id WHERE u.username = 'anna'").fetchone()
        conn.close()
        if row:
            self.assertNotEqual(client.get(f'/download/{row[0]}').status_code, 200)


def login_client(username='anna', base_url='http://localhost'):
    client = cloud.app.test_client()
    client.environ_base['wsgi.url_scheme'] = base_url.split(':')[0]
    r = client.post('/login', data={'username': username, 'password': 'ein-langes-passwort'}, base_url=base_url)
    assert r.status_code == 302
    return client


class SessionTest(unittest.TestCase):
    def test_logout_others_keeps_own_session(self):
        laptop, handy = login_client(), login_client()
        self.assertEqual(handy.get('/').status_code, 200)
        r = laptop.post('/logout_others')
        self.assertEqual(r.status_code, 302)
        self.assertEqual(laptop.get('/').status_code, 200)          # dieses Geraet bleibt angemeldet
        self.assertEqual(handy.get('/').status_code, 302)           # das andere ist abgemeldet

    def test_password_reset_ends_all_sessions(self):
        client = login_client()
        conn = sqlite3.connect(os.environ['HIMBEEREPI_DB'])
        conn.execute("UPDATE users SET session_version = session_version + 1 WHERE username = 'anna'")  # wie reset_password.py
        conn.commit()
        conn.close()
        self.assertEqual(client.get('/').status_code, 302)

    def test_old_database_gets_column(self):
        old = os.path.join(TMP, 'alt.db')
        conn = sqlite3.connect(old)
        conn.execute('CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT, password_hash TEXT)')
        conn.close()
        manage._migrate(sqlite3.connect(old))
        columns = [r[1] for r in sqlite3.connect(old).execute('PRAGMA table_info(users)')]
        self.assertIn('session_version', columns)


class HeaderTest(unittest.TestCase):
    def test_security_headers(self):
        r = login_client().get('/')
        self.assertEqual(r.headers.get('X-Frame-Options'), 'DENY')
        self.assertIn("frame-ancestors 'none'", r.headers.get('Content-Security-Policy', ''))
        self.assertEqual(r.headers.get('Referrer-Policy'), 'same-origin')
        self.assertIsNone(r.headers.get('Strict-Transport-Security'))   # HTTP: kein HSTS

    def test_hsts_and_samesite_over_https(self):
        client = cloud.app.test_client()
        r = client.post('/login', data={'username': 'anna', 'password': 'ein-langes-passwort'}, base_url='https://localhost')
        self.assertIn('SameSite=Lax', r.headers.get('Set-Cookie', ''))
        self.assertIn('max-age=', r.headers.get('Strict-Transport-Security', ''))


class DotDotTest(unittest.TestCase):
    def test_no_dot_folders_and_clean_zip_download(self):
        client = login_client()
        client.post('/create_folder', data={'folder_name': '..'})
        client.post('/upload', data={'file': (io.BytesIO(b'x'), 'datei.txt'), 'relative_path': '../../datei.txt'},
                    content_type='multipart/form-data')
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as zf:
            zf.writestr('../../boese.txt', 'x')
            zf.writestr('./punkt/datei2.txt', 'y')
        buf.seek(0)
        client.post('/upload', data={'file': (buf, 'paket.zip'), 'extract_zip': '1'}, content_type='multipart/form-data')
        conn = sqlite3.connect(os.environ['HIMBEEREPI_DB'])
        names = [r[0] for r in conn.execute("SELECT name FROM folders")]
        ids = [str(r[0]) for r in conn.execute("SELECT id FROM folders WHERE user_id = 1 AND parent_id IS NULL")]
        conn.close()
        self.assertNotIn('..', names)
        self.assertNotIn('.', names)
        r = client.post('/bulk_download', data={'folder_ids': ids})
        with zipfile.ZipFile(io.BytesIO(r.data)) as zf:
            for name in zf.namelist():
                self.assertNotIn('..', name.split('/'), name)


class RedirectLogoutTest(unittest.TestCase):
    def test_only_internal_redirects(self):
        client = login_client()
        for target in ('https://boese.example.com/', '//boese.example.com/', '/\\boese.example.com/', 'javascript:alert(1)'):
            r = client.post('/bulk_delete', data={'redirect_to': target})
            self.assertEqual(r.status_code, 204, target)
        r = client.post('/bulk_delete', data={'redirect_to': '/trash'})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r.headers['Location'].endswith('/trash'))

    def test_logout_only_by_post(self):
        client = login_client()
        self.assertEqual(client.get('/logout').status_code, 405)
        self.assertEqual(client.get('/').status_code, 200)
        self.assertEqual(client.post('/logout').status_code, 302)
        self.assertEqual(client.get('/').status_code, 302)


def folder_of(username, name):
    conn = sqlite3.connect(os.environ['HIMBEEREPI_DB'])
    row = conn.execute("SELECT f.id FROM folders f JOIN users u ON u.id = f.user_id "
                       "WHERE u.username = ? AND f.name = ?", (username, name)).fetchone()
    conn.close()
    return row[0]


class ForeignFolderTest(unittest.TestCase):
    def test_no_access_to_foreign_folders(self):
        ben, anna = login_client('ben'), login_client()
        ben.post('/create_folder', data={'folder_name': 'Bens Ordner'})
        fid = folder_of('ben', 'Bens Ordner')
        self.assertEqual(anna.get(f'/folder/{fid}').status_code, 404)
        self.assertEqual(anna.get('/folder/999999').status_code, 404)
        r = anna.post('/upload', data={'file': (io.BytesIO(b'x'), 'fremd.txt'), 'folder_id': str(fid)},
                      content_type='multipart/form-data')
        self.assertEqual(r.status_code, 404)
        r = anna.post('/upload', data={'file': (io.BytesIO(b'x'), 'fremd.txt'), 'folder_id': 'abc'},
                      content_type='multipart/form-data')
        self.assertEqual(r.status_code, 404)
        self.assertEqual(anna.post('/create_folder', data={'folder_name': 'unter', 'parent_id': str(fid)}).status_code, 404)
        conn = sqlite3.connect(os.environ['HIMBEEREPI_DB'])
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM files WHERE folder_id = ? AND user_id = 1", (fid,)).fetchone()[0], 0)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM folders WHERE parent_id = ?", (fid,)).fetchone()[0], 0)
        conn.close()

    def test_purge_keeps_other_users_files(self):
        ben = login_client('ben')
        ben.post('/create_folder', data={'folder_name': 'Wird geloescht'})
        fid = folder_of('ben', 'Wird geloescht')
        # Altbestand: Annas Datei haengt (noch aus der Zeit vor der Pruefung) in Bens Ordner
        conn = sqlite3.connect(os.environ['HIMBEEREPI_DB'])
        conn.execute("INSERT INTO files (filename, original_name, size_mb, folder_id, user_id) VALUES ('x_alt.txt', 'alt.txt', 0, ?, 1)", (fid,))
        conn.commit()
        ben.post(f'/delete_folder/{fid}')
        ben.post(f'/delete_permanently/folder/{fid}')
        self.assertIsNone(conn.execute("SELECT id FROM folders WHERE id = ?", (fid,)).fetchone())
        self.assertIsNotNone(conn.execute("SELECT id FROM files WHERE filename = 'x_alt.txt'").fetchone())
        conn.close()


class ZipImportTest(unittest.TestCase):
    def zip_upload(self, client, name, content):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(name, content)
        buf.seek(0)
        return client.post('/upload', data={'file': (buf, 'paket.zip'), 'extract_zip': '1'}, content_type='multipart/form-data')

    def test_zip_extracted_completely(self):
        content = os.urandom(3 * 1024 * 1024)
        self.assertEqual(self.zip_upload(login_client(), 'gross/zufall.bin', content).status_code, 302)
        conn = sqlite3.connect(os.environ['HIMBEEREPI_DB'])
        filename = conn.execute("SELECT filename FROM files WHERE original_name = 'zufall.bin'").fetchone()[0]
        conn.close()
        with open(os.path.join(os.environ['HIMBEEREPI_DATA_DIR'], filename), 'rb') as f:
            self.assertEqual(f.read(), content)

    def test_zip_rejected_without_free_space(self):
        from collections import namedtuple
        from unittest import mock
        usage = namedtuple('usage', 'total used free')
        before = set(os.listdir(os.environ['HIMBEEREPI_DATA_DIR']))
        with mock.patch.object(cloud.shutil, 'disk_usage', return_value=usage(0, 0, 100 * 1024 * 1024)):
            r = self.zip_upload(login_client(), 'nullen.bin', b'\0' * (1024 * 1024))
        self.assertEqual(r.status_code, 507)
        self.assertIn('Nicht genug freier Speicher'.encode(), r.data)
        self.assertEqual(set(os.listdir(os.environ['HIMBEEREPI_DATA_DIR'])), before)


class AccountLockTest(unittest.TestCase):
    def test_lock_per_account_across_addresses(self):
        conn = sqlite3.connect(os.environ['HIMBEEREPI_DB'])
        conn.execute("INSERT INTO users (username, password_hash) VALUES ('dora', ?)", (generate_password_hash('doras-passwort-123'),))
        conn.commit()
        conn.close()
        client = cloud.app.test_client()
        for i in range(cloud.MAX_ACCOUNT_ATTEMPTS):   # jede Adresse bleibt unter der Sperre pro IP
            client.post('/login', data={'username': 'dora', 'password': 'falsch'}, environ_base={'REMOTE_ADDR': f'10.0.0.{i}'})
            client.post('/login', data={'username': 'niemand', 'password': 'falsch'}, environ_base={'REMOTE_ADDR': f'10.0.1.{i}'})
        r = client.post('/login', data={'username': 'dora', 'password': 'doras-passwort-123'}, environ_base={'REMOTE_ADDR': '10.0.2.1'})
        self.assertEqual(r.status_code, 200)
        self.assertIn('Zu viele'.encode(), r.data)
        r = client.post('/login', data={'username': 'niemand', 'password': 'falsch'}, environ_base={'REMOTE_ADDR': '10.0.2.2'})
        self.assertIn('Zu viele'.encode(), r.data)   # unbekannte Namen verhalten sich gleich
        r = login_client().get('/')                   # andere Konten sind nicht betroffen
        self.assertEqual(r.status_code, 200)

    def test_old_database_gets_username_column(self):
        old = os.path.join(TMP, 'alt-login.db')
        conn = sqlite3.connect(old)
        conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT, password_hash TEXT)")
        conn.execute("CREATE TABLE login_attempts (id INTEGER PRIMARY KEY, ip_address TEXT NOT NULL, attempt_time TEXT NOT NULL)")
        manage._migrate(conn)
        self.assertIn('username', [r[1] for r in conn.execute("PRAGMA table_info(login_attempts)")])
        conn.close()


if __name__ == '__main__':
    unittest.main()
