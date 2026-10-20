"""Transactional SQLite persistence, including validated result image blobs."""
import io
import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from PIL import Image, UnidentifiedImageError
from antibody_manager.species import species

FIELDS = ('name', 'kind', 'host', 'reactivity', 'company', 'catalog', 'lot', 'location', 'color', 'notes', 'clone', 'isotype', 'excitation', 'emission', 'temperature', 'box', 'position')


class Store:
    def __init__(self, path=None):
        self.path = Path(path or os.environ.get('ANTIBODY_DB', Path(__file__).resolve().parents[1] / 'data' / 'antibodies.sqlite3'))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
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
                PRAGMA user_version = 1;
            ''')

            columns = {row['name'] for row in db.execute('PRAGMA table_info(antibodies)')}
            for field in FIELDS:
                if field not in columns:
                    db.execute(f"ALTER TABLE antibodies ADD COLUMN {field} TEXT NOT NULL DEFAULT ''")
            # Older imports kept these workbook fields in notes. Recover them without a reimport.
            aliases = {'clone': ('clone',), 'isotype': ('isotype',),
                       'excitation': ('excitation',), 'emission': ('emission',)}
            for row in db.execute('SELECT * FROM antibodies').fetchall():
                details = dict(line.split(':', 1) for line in row['notes'].splitlines() if ':' in line)
                for field, names in aliases.items():
                    value = next((v.strip() for k, v in details.items() if any(n in k.casefold() for n in names)), '')
                    if value and field not in columns:
                        db.execute(f'UPDATE antibodies SET {field}=? WHERE id=?', (value, row['id']))

    @contextmanager
    def connection(self):
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
                WHERE (? OR a.archived=0) GROUP BY a.id ORDER BY a.name, a.id''', (include_archived,)).fetchall()
        return [dict(row, dilutions=json.loads(row['dilutions'])) for row in rows]

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
            db.execute('UPDATE antibodies SET archived=? WHERE id=?', (archived, antibody_id))

    def add_review(self, antibody_id, author, experiment, rating, comment, uploads=()):
        if not author.strip() or not experiment.strip() or rating not in range(1, 6):
            raise ValueError('Reviewer, experiment and a rating from 1 to 5 are required.')
        validated = []
        if len(uploads) > 5:
            raise ValueError('Upload at most five images per review.')
        for name, data in uploads:
            if len(data) > 10 * 1024 * 1024:
                raise ValueError('Each image must be at most 10 MB.')
            try:
                with Image.open(io.BytesIO(data)) as image:
                    if image.format not in ('PNG', 'JPEG', 'TIFF') or image.width * image.height > 25_000_000:
                        raise ValueError('Use PNG/JPEG/TIFF images up to 25 megapixels.')
                    image.verify()
            except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
                raise ValueError('The uploaded file is not a valid image.') from exc
            validated.append((Path(name).name, data))
        with self.connection() as db:
            review_id = db.execute('INSERT INTO reviews (antibody_id,author,experiment,rating,comment) VALUES (?,?,?,?,?)',
                                   (antibody_id, author.strip(), experiment.strip(), rating, comment.strip())).lastrowid
            db.executemany('INSERT INTO images (review_id,name,data) VALUES (?,?,?)', [(review_id, *item) for item in validated])

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
            db.execute('DELETE FROM images WHERE review_id=?', (review_id,))
            db.execute('DELETE FROM reviews WHERE id=?', (review_id,))

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
