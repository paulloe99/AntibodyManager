"""Transactional SQLite or PostgreSQL persistence, including result image blobs."""
import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from antibody_manager.species import species
from antibody_manager.review_images import prepare_image
from antibody_manager.config import database_urls

FIELDS = ('name', 'kind', 'host', 'reactivity', 'company', 'catalog', 'lot', 'location', 'color', 'notes', 'clone', 'isotype', 'excitation', 'emission', 'temperature', 'box', 'position')


class Store:
    def __init__(self, path=None, database_url=None, image_storage=None):
        self.image_storage = image_storage
        pooled, direct = database_urls() if path is None and not os.environ.get('ANTIBODY_DB') else (None, None)
        self.database_url = database_url if database_url is not None else pooled
        self.schema_database_url = direct if self.database_url == pooled else self.database_url
        if self.database_url is not None and not self.database_url.startswith(('postgresql://', 'postgres://')):
            raise ValueError('Configure a valid PostgreSQL database URL.')
        self.path = None if self.database_url else Path(path or os.environ.get('ANTIBODY_DB', Path(__file__).resolve().parents[1] / 'data' / 'antibodies.sqlite3'))
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection(schema=True) as db:
            if self.database_url:
                db.execute('SELECT pg_advisory_xact_lock(714203861)')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS antibodies (
                    id INTEGER PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL,
                    host TEXT NOT NULL, reactivity TEXT NOT NULL, company TEXT NOT NULL,
                    catalog TEXT NOT NULL, lot TEXT NOT NULL, location TEXT NOT NULL,
                    color TEXT NOT NULL, notes TEXT NOT NULL, dilutions TEXT NOT NULL,
                    archived INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS reviews (
                    id INTEGER PRIMARY KEY, antibody_id INTEGER NOT NULL REFERENCES antibodies(id),
                    author TEXT NOT NULL, experiment TEXT NOT NULL, rating INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5),
                    comment TEXT NOT NULL, created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS images (
                    id INTEGER PRIMARY KEY, review_id INTEGER NOT NULL REFERENCES reviews(id),
                    name TEXT NOT NULL, data BLOB NOT NULL);
                CREATE TABLE IF NOT EXISTS image_cleanup (
                    storage_key TEXT PRIMARY KEY, storage_provider TEXT NOT NULL);
                PRAGMA user_version = 1;
            ''')

            column_query = ("SELECT column_name AS name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='antibodies'"
                            if self.database_url else 'PRAGMA table_info(antibodies)')
            columns = {row['name'] for row in db.execute(column_query)}
            for field in FIELDS:
                if field not in columns:
                    db.execute(f"ALTER TABLE antibodies ADD COLUMN {field} TEXT NOT NULL DEFAULT ''")
            image_columns = {row['name'] for row in db.execute(
                "SELECT column_name AS name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='images'"
                if self.database_url else 'PRAGMA table_info(images)')}
            for field, default in [('storage_provider', 'database'), ('storage_key', '')]:
                if field not in image_columns:
                    db.execute(f"ALTER TABLE images ADD COLUMN {field} TEXT NOT NULL DEFAULT '{default}'")
            # Older imports kept these workbook fields in notes. Recover them without a reimport.
            aliases = {'clone': ('clone',), 'isotype': ('isotype',),
                       'excitation': ('excitation',), 'emission': ('emission',)}
            recover = any(field not in columns for field in aliases)
            for row in db.execute('SELECT * FROM antibodies').fetchall() if recover else []:
                details = dict(line.split(':', 1) for line in row['notes'].splitlines() if ':' in line)
                for field, names in aliases.items():
                    value = next((v.strip() for k, v in details.items() if any(n in k.casefold() for n in names)), '')
                    if value and field not in columns:
                        db.execute(f'UPDATE antibodies SET {field}=? WHERE id=?', (value, row['id']))

    @contextmanager
    def connection(self, *, schema=False):
        if self.database_url:
            import psycopg
            from psycopg.rows import dict_row
            from antibody_manager.postgres import PostgresConnection
            with psycopg.connect(self.schema_database_url if schema else self.database_url,
                                 row_factory=dict_row, connect_timeout=15, prepare_threshold=None) as connection:
                yield PostgresConnection(connection)
            return
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys = ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    def antibodies(self, include_archived=False):
        with self.connection() as db:
            rows = db.execute('''SELECT a.*, AVG(r.rating) AS rating, COUNT(r.id) AS reviews
                FROM antibodies a LEFT JOIN reviews r ON r.antibody_id=a.id
                WHERE (?=1 OR a.archived=0) GROUP BY a.id ORDER BY a.name, a.id''', (int(include_archived),)).fetchall()
        return [dict(row, dilutions=json.loads(row['dilutions']), rating=float(row['rating']) if row['rating'] is not None else None) for row in rows]

    def save(self, antibody, antibody_id=None):
        data = {key: str(antibody.get(key, '')).strip() for key in FIELDS}
        data['reactivity'] = species(data['reactivity'])
        if any(not data[key] for key in ('name', 'host', 'reactivity', 'location')):
            raise ValueError('Name, host, reactivity and storage location are required.')
        if data['kind'] not in ('Primary', 'Secondary'):
            raise ValueError('Invalid antibody type.')
        dilutions = antibody.get('dilutions', {})
        if any(not key.strip() or not isinstance(value, int) or value < 1 for key, value in dilutions.items()):
            raise ValueError('Dilutions must map experiments to positive integer denominators.')
        data['dilutions'] = json.dumps(dilutions)
        with self.connection() as db:
            if antibody_id is None:
                return db.execute(f"INSERT INTO antibodies ({','.join(data)}) VALUES ({','.join('?' for _ in data)})", tuple(data.values())).lastrowid
            cursor = db.execute(f"UPDATE antibodies SET {','.join(key+'=?' for key in data)} WHERE id=?", (*data.values(), antibody_id))
            if not cursor.rowcount:
                raise ValueError('Antibody no longer exists.')
            return antibody_id

    def archive(self, antibody_id, archived=True):
        with self.connection() as db:
            db.execute('UPDATE antibodies SET archived=? WHERE id=?', (int(archived), antibody_id))

    def add_review(self, antibody_id, author, experiment, rating, comment, uploads=()):
        if not author.strip() or not experiment.strip() or rating not in range(1, 6):
            raise ValueError('Reviewer, experiment and a rating from 1 to 5 are required.')
        validated = []
        if len(uploads) > 5:
            raise ValueError('Upload at most five images per review.')
        for name, data in uploads:
            validated.append(prepare_image(name, data))
        uploaded, pictures = [], []
        try:
            for name, data in validated:
                if self.image_storage:
                    reference = self.image_storage.upload(name, data)
                    uploaded.append(reference)
                    pictures.append((name, b'', 'onedrive', reference))
                else:
                    pictures.append((name, data, 'database', ''))
            with self.connection() as db:
                review_id = db.execute('INSERT INTO reviews (antibody_id,author,experiment,rating,comment) VALUES (?,?,?,?,?)',
                                       (antibody_id, author.strip(), experiment.strip(), rating, comment.strip())).lastrowid
                db.executemany('INSERT INTO images (review_id,name,data,storage_provider,storage_key) VALUES (?,?,?,?,?)',
                               [(review_id, *item) for item in pictures])
        except Exception as exc:
            ambiguous = getattr(exc, 'cleanup_reference', None)
            if ambiguous:
                uploaded.append(ambiguous)
            if uploaded:
                self._remove_unpublished_images(uploaded)
            raise

    def _remove_unpublished_images(self, references):
        pending = []
        for reference in references:
            try:
                self.image_storage.delete(reference)
            except ValueError:
                pending.append(reference)
        if pending:
            with self.connection() as db:
                db.executemany('INSERT INTO image_cleanup (storage_key,storage_provider) VALUES (?,?) ON CONFLICT(storage_key) DO NOTHING',
                               [(reference, 'onedrive') for reference in pending])

    def image_data(self, image):
        if image.get('storage_provider', 'database') == 'database':
            return bytes(image['data'])
        if image['storage_provider'] != 'onedrive' or self.image_storage is None:
            raise ValueError('Connect OneDrive to view this picture.')
        return self.image_storage.download(image['storage_key'])

    def cleanup_images(self, limit=5):
        if self.image_storage is None:
            return
        with self.connection() as db:
            pending = db.execute("SELECT storage_key FROM image_cleanup WHERE storage_provider='onedrive' LIMIT ?", (limit,)).fetchall()
        for item in pending:
            try:
                self.image_storage.delete(item['storage_key'])
            except ValueError:
                continue
            with self.connection() as db:
                db.execute('DELETE FROM image_cleanup WHERE storage_key=?', (item['storage_key'],))

    def update_review(self, review_id, author, experiment, rating, comment):
        if not experiment.strip() or rating not in range(1, 6):
            raise ValueError('Experiment and a rating from 1 to 5 are required.')
        with self.connection() as db:
            result = db.execute('UPDATE reviews SET experiment=?, rating=?, comment=? WHERE id=? AND author=?',
                                (experiment.strip(), rating, comment.strip(), review_id, author))
            if not result.rowcount:
                raise ValueError('Only the original reviewer can update this review.')

    def delete_review(self, review_id, author):
        with self.connection() as db:
            review = db.execute('SELECT author FROM reviews WHERE id=?', (review_id,)).fetchone()
            if review is None or review['author'] != author:
                raise ValueError('Only the original reviewer can delete this review.')
            images = db.execute("SELECT storage_key FROM images WHERE review_id=? AND storage_provider='onedrive'", (review_id,)).fetchall()
            db.executemany('INSERT INTO image_cleanup (storage_key,storage_provider) VALUES (?,?) ON CONFLICT(storage_key) DO NOTHING',
                           [(image['storage_key'], 'onedrive') for image in images])
            db.execute('DELETE FROM images WHERE review_id=?', (review_id,))
            db.execute('DELETE FROM reviews WHERE id=?', (review_id,))
        self.cleanup_images()

    def reviews(self, antibody_id):
        with self.connection() as db:
            rows = db.execute('SELECT * FROM reviews WHERE antibody_id=? ORDER BY created DESC,id DESC', (antibody_id,)).fetchall()
            return [dict(row, images=[dict(i) for i in db.execute('SELECT * FROM images WHERE review_id=?', (row['id'],))]) for row in rows]

    def seed_demo(self):
        if self.antibodies(include_archived=True):
            raise ValueError('Demo data can only be loaded into an empty inventory.')
        for name, kind, host, reactivity, color in [
            ('DEMO: Target A', 'Primary', 'Rabbit', 'Human, Mouse', ''),
            ('DEMO: Target B', 'Primary', 'Mouse', 'Human', ''),
            ('DEMO: Anti-Rabbit', 'Secondary', 'Goat', 'Rabbit', 'Green / 488'),
            ('DEMO: Anti-Mouse', 'Secondary', 'Goat', 'Mouse', 'Red / 594')]:
            self.save(dict(name=name, kind=kind, host=host, reactivity=reactivity, color=color,
                           company='Fictional demo supplier', location='Demo freezer / box 1',
                           notes='Fictional example. Dilutions are for interface demonstration only.', dilutions={'IF': 500, 'WB': 1000}))
