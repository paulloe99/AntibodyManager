"""PostgreSQL adapter for the store's internal, parameterized SQL statements."""
import re


class Result:
    def __init__(self, cursor, lastrowid=None):
        self.cursor = cursor
        self.lastrowid = lastrowid
        self.rowcount = cursor.rowcount

    def fetchone(self):
        return self.cursor.fetchone()

    def fetchall(self):
        return self.cursor.fetchall()

    def __iter__(self):
        return iter(self.cursor)


class PostgresConnection:
    def __init__(self, connection):
        self.raw = connection

    def execute(self, query, parameters=()):
        # These are application-owned queries; user values are always parameters.
        query = query.replace('?', '%s')
        returns_id = bool(re.match(r'\s*INSERT INTO (antibodies|reviews|images)\s*\(', query, re.I))
        if returns_id:
            query = query.rstrip().rstrip(';') + ' RETURNING id'
        cursor = self.raw.execute(query, parameters or None)
        return Result(cursor, cursor.fetchone()['id'] if returns_id else None)

    def executemany(self, query, parameters):
        cursor = self.raw.cursor()
        cursor.executemany(query.replace('?', '%s'), parameters)
        return Result(cursor)

    def executescript(self, script):
        script = script.replace('id INTEGER PRIMARY KEY', 'id BIGSERIAL PRIMARY KEY')
        script = script.replace('INTEGER NOT NULL REFERENCES', 'BIGINT NOT NULL REFERENCES')
        script = script.replace('data BLOB', 'data BYTEA')
        script = script.replace('DEFAULT CURRENT_TIMESTAMP', 'DEFAULT (CURRENT_TIMESTAMP::text)')
        for statement in script.split(';'):
            if statement.strip() and not statement.strip().startswith('PRAGMA'):
                self.execute(statement)
