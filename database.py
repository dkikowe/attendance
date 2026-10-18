"""Local SQLite in development, remote libSQL for durable serverless storage."""
import os
import sqlite3


class Row(tuple):
    """Keep SQLite's indexed/named row access for existing views and CSV exports."""
    def __new__(cls, values, columns):
        row = super().__new__(cls, values)
        row.columns = tuple(columns)
        return row

    def __getitem__(self, key):
        if isinstance(key, str):
            key = self.columns.index(key)
        return super().__getitem__(key)

    def keys(self):
        return self.columns


def _invoke(operation, *args):
    import libsql
    try:
        return operation(*args)
    except (libsql.Error, ValueError) as error:
        # libsql exposes one Error class; retain the app's existing duplicate
        # and foreign-key handling without misclassifying network errors.
        if any(marker in str(error) for marker in (
            'SQLITE_CONSTRAINT', 'UNIQUE constraint failed',
            'FOREIGN KEY constraint failed', 'NOT NULL constraint failed',
            'CHECK constraint failed',
        )):
            raise sqlite3.IntegrityError(str(error)) from error
        raise


class Cursor:
    def __init__(self, connection, cursor):
        self.connection = connection
        self.raw = cursor

    @property
    def lastrowid(self):
        return self.raw.lastrowid

    @property
    def rowcount(self):
        return self.raw.rowcount

    def execute(self, sql, params=()):
        _invoke(self.raw.execute, sql, params)
        return self

    def executescript(self, script):
        _invoke(self.raw.executescript, script)
        return self

    def _row(self, values):
        if values is None or self.connection.row_factory is None:
            return values
        return Row(values, [column[0] for column in self.raw.description])

    def fetchone(self):
        return self._row(self.raw.fetchone())

    def fetchall(self):
        return [self._row(values) for values in self.raw.fetchall()]

    def __iter__(self):
        while True:
            row = self.fetchone()
            if row is None:
                return
            yield row

    def close(self):
        self.raw.close()


class Connection:
    def __init__(self, raw):
        self.raw = raw
        self.row_factory = None

    def cursor(self):
        return Cursor(self, self.raw.cursor())

    def execute(self, sql, params=()):
        return self.cursor().execute(sql, params)

    def executescript(self, script):
        return self.cursor().executescript(script)

    def commit(self):
        _invoke(self.raw.commit)

    def rollback(self):
        _invoke(self.raw.rollback)

    def close(self):
        self.raw.close()


def connect(local_path):
    url = os.getenv('TURSO_DATABASE_URL', '')
    if url:
        if not os.getenv('TURSO_AUTH_TOKEN'):
            raise RuntimeError('TURSO_AUTH_TOKEN is required for the cloud database.')
        import libsql
        connection = Connection(libsql.connect(
            database=url, auth_token=os.environ['TURSO_AUTH_TOKEN'], timeout=15,
        ))
    else:
        if os.getenv('VERCEL') == '1' or os.getenv('APP_ENV') == 'production':
            raise RuntimeError('Production requires TURSO_DATABASE_URL and TURSO_AUTH_TOKEN.')
        connection = sqlite3.connect(local_path)
    connection.execute('PRAGMA foreign_keys=ON')
    return connection
