import io
import sqlite3
from types import SimpleNamespace

from PIL import Image
import pytest

from antibody_manager.migrate_cloud import copy_records, configured_url, read_source
from antibody_manager.postgres import PostgresConnection
from antibody_manager.store import Store


def test_copy_preserves_inventory_reviews_images_and_import_tracking(tmp_path):
    source = Store(tmp_path / 'source.db')
    source.seed_demo()
    a = source.antibodies()[0]
    image = io.BytesIO()
    Image.new('RGB', (8, 8)).save(image, format='TIFF')
    source.add_review(a['id'], 'Laura', 'IF', 5, 'Original comment', [('test.tiff', image.getvalue())])
    source.archive(a['id'])
    with source.connection() as db:
        db.execute('''CREATE TABLE imported_rows (source TEXT, sheet TEXT, row_number INTEGER,
                      antibody_id INTEGER REFERENCES antibodies(id), PRIMARY KEY(source,sheet,row_number))''')
        db.execute('INSERT INTO imported_rows VALUES (?,?,?,?)', ('workbook', 'Primary', 2, a['id']))
    target = Store(tmp_path / 'target.db')
    snapshot = read_source(source.path)
    try:
        with target.connection() as db:
            counts = copy_records(snapshot, db)
        assert counts == dict(antibodies=4, reviews=1, images=1, imported_rows=1)
        assert target.antibodies(True) == source.antibodies(True)
        assert target.reviews(a['id']) == source.reviews(a['id'])
        with pytest.raises(ValueError, match='not empty'):
            with target.connection() as db:
                copy_records(snapshot, db)
        assert target.reviews(a['id'])[0]['images'][0]['data'] == image.getvalue()
    finally:
        snapshot.close()


def test_migration_rolls_back_on_image_verification_failure(tmp_path):
    source = Store(tmp_path / 'source.db')
    source.seed_demo()
    target = Store(tmp_path / 'target.db')
    # Simulate a target corrupting copied data: verification must detect it.
    with target.connection() as db:
        db.executescript("""CREATE TRIGGER bad_copy AFTER INSERT ON antibodies
            BEGIN UPDATE antibodies SET name='corrupted' WHERE id=NEW.id; END;""")
    snapshot = read_source(source.path)
    try:
        with pytest.raises(ValueError, match='Verification failed'):
            with target.connection() as db:
                copy_records(snapshot, db)
        assert target.antibodies(True) == []
        assert len(source.antibodies()) == 4
    finally:
        snapshot.close()


def test_cloud_configuration_never_silently_uses_local_db(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match='PostgreSQL'):
        Store(database_url='')
    with pytest.raises(ValueError, match='PostgreSQL'):
        Store(database_url='sqlite:///file.db')
    monkeypatch.setenv('ANTIBODY_DATABASE_URL', 'invalid')
    # An explicit path always permits isolated local development/migration snapshots.
    assert Store(tmp_path / 'local.db').database_url is None
    with pytest.raises(ValueError):
        Store()
    monkeypatch.delenv('ANTIBODY_DATABASE_URL')
    secrets = tmp_path / 'secrets.toml'
    secrets.write_text('[database]\nurl="postgresql://example/database"', encoding='utf-8')
    assert configured_url(secrets) == 'postgresql://example/database'
    with pytest.raises(sqlite3.OperationalError):
        read_source(tmp_path / 'missing.db')
    assert not (tmp_path / 'missing.db').exists()


def test_postgres_queries_bind_values_and_return_generated_ids():
    calls = []
    cursor = SimpleNamespace(rowcount=1, fetchone=lambda: {'id': 42})
    raw = SimpleNamespace(execute=lambda query, params: calls.append((query, params)) or cursor)
    db = PostgresConnection(raw)
    value = "User's ? antibody"
    result = db.execute('INSERT INTO antibodies (name) VALUES (?)', (value,))
    assert result.lastrowid == 42
    assert calls == [('INSERT INTO antibodies (name) VALUES (%s) RETURNING id', (value,))]
    calls.clear()
    db.executescript('''CREATE TABLE images (id INTEGER PRIMARY KEY, data BLOB);
        CREATE TABLE reviews (created TEXT DEFAULT CURRENT_TIMESTAMP);
        PRAGMA user_version = 1;''')
    assert len(calls) == 2
    assert 'BIGSERIAL PRIMARY KEY' in calls[0][0] and 'BYTEA' in calls[0][0]
    assert 'CURRENT_TIMESTAMP::text' in calls[1][0]
