"""Preview or copy the local inventory to an empty hosted PostgreSQL database."""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import sqlite3
import tomllib
from dotenv import dotenv_values

from antibody_manager.store import Store
from antibody_manager.config import database_urls

TABLES = ('antibodies', 'reviews', 'images')


def read_source(path):
    # mode=ro also prevents creating an empty database when the path is wrong.
    connection = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def copy_records(source, target):
    """Caller supplies one destination transaction; preserve IDs and image bytes."""
    tables = list(TABLES)
    has_cleanup = source.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='image_cleanup'").fetchone()
    if has_cleanup and source.execute('SELECT 1 FROM image_cleanup LIMIT 1').fetchone():
        tables.append('image_cleanup')
    has_imports = source.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='imported_rows'").fetchone()
    if has_imports:
        target.execute('''CREATE TABLE IF NOT EXISTS imported_rows (
            source TEXT NOT NULL, sheet TEXT NOT NULL, row_number INTEGER NOT NULL,
            antibody_id BIGINT NOT NULL REFERENCES antibodies(id),
            PRIMARY KEY(source, sheet, row_number))''')
        tables.append('imported_rows')
    for table in tables:
        if target.execute(f'SELECT 1 FROM {table} LIMIT 1').fetchone():
            raise ValueError('Destination is not empty. Migration stopped without replacing any records.')
    counts = {}
    for table in tables:
        columns = [row['name'] for row in source.execute(f'PRAGMA table_info({table})')]
        # All identifiers come from our own SQLite schema, never user input.
        if any(not column.replace('_', '').isalnum() for column in columns):
            raise ValueError('Unexpected source column name.')
        names = ','.join(columns)
        records = source.execute(f'SELECT {names} FROM {table}').fetchall()
        if records:
            target.executemany(f'INSERT INTO {table} ({names}) VALUES ({",".join("?" for _ in columns)})',
                               [tuple(record) for record in records])
        # Verify complete values, including every original image byte, before commit.
        order = {'imported_rows': 'source,sheet,row_number', 'image_cleanup': 'storage_key'}.get(table, 'id')
        originals = source.execute(f'SELECT {names} FROM {table} ORDER BY {order}').fetchall()
        copied = target.execute(f'SELECT {names} FROM {table} ORDER BY {order}').fetchall()
        if [tuple(row) for row in originals] != [tuple(row[column] for column in columns) for row in copied]:
            raise ValueError(f'Verification failed for {table}; migration will be rolled back.')
        counts[table] = len(records)
    return counts


def migrate(source_path, database_url):
    source_path = Path(source_path)
    backup_path = source_path.with_name(f'{source_path.stem}.before-cloud-{datetime.now():%Y%m%d-%H%M%S-%f}.sqlite3')
    source = read_source(source_path)
    try:
        with sqlite3.connect(backup_path) as backup:
            source.backup(backup)
    finally:
        source.close()
    # Upgrade only the snapshot if it predates newer inventory fields.
    Store(backup_path)
    destination = Store(database_url=database_url)
    snapshot = read_source(backup_path)
    try:
        with destination.connection() as db:
            db.execute('LOCK TABLE antibodies, reviews, images, image_cleanup IN ACCESS EXCLUSIVE MODE')
            counts = copy_records(snapshot, db)
            for table in TABLES:
                db.execute(f"SELECT setval(pg_get_serial_sequence(?, 'id'), COALESCE(MAX(id), 1), COUNT(*)>0) FROM {table}", (table,))
    finally:
        snapshot.close()
    return dict(counts=counts, backup=str(backup_path))


def configured_url(secrets_path):
    url = os.environ.get('ANTIBODY_DATABASE_URL')
    if not url and secrets_path.exists():
        with secrets_path.open('rb') as stream:
            url = tomllib.load(stream).get('database', {}).get('url')
    if not url:
        _, url = database_urls()
    if not url:
        raise ValueError('Run neon deploy to configure DATABASE_URL_UNPOOLED, or set [database].url before migration.')
    return url


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path('data/antibodies.sqlite3'))
    parser.add_argument('--secrets', type=Path, default=Path('.streamlit/secrets.toml'))
    parser.add_argument('--env-file', type=Path, help='Use this Neon env file (direct URL preferred) instead of the default connection settings.')
    parser.add_argument('--apply', action='store_true', help='Copy and verify all records; otherwise preview only.')
    args = parser.parse_args()
    try:
        if args.apply:
            if args.env_file:
                if not args.env_file.is_file():
                    raise ValueError('The requested Neon environment file does not exist.')
                values = dotenv_values(args.env_file)
                url = values.get('DATABASE_URL_UNPOOLED') or values.get('DATABASE_URL')
                if not url:
                    raise ValueError('The Neon environment file has no database URL.')
            else:
                url = configured_url(args.secrets)
            result = migrate(args.source, url)
        else:
            source = read_source(args.source)
            try:
                result = {'mode': 'preview', 'counts': {table: source.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] for table in TABLES},
                          'file_bytes': args.source.stat().st_size}
            finally:
                source.close()
        print(json.dumps(result, indent=2))
    except ValueError as exc:
        parser.exit(1, str(exc) + '\n')
    except Exception:
        # Driver errors may include the connection string. Never print credentials.
        parser.exit(1, 'Migration failed. Check the source file and database connection. No source records were changed.\n')


if __name__ == '__main__':
    main()
