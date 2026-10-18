import importlib.util
from pathlib import Path
import sqlite3

import libsql
import pytest

spec = importlib.util.spec_from_file_location('database_adapter', Path(__file__).resolve().parents[1] / 'database.py')
database = importlib.util.module_from_spec(spec)
spec.loader.exec_module(database)


def test_libsql_rows_constraints_and_transactions():
    connection = database.Connection(libsql.connect(':memory:'))
    connection.execute('PRAGMA foreign_keys=ON')
    connection.executescript('CREATE TABLE parents(id INTEGER PRIMARY KEY, name TEXT UNIQUE); CREATE TABLE children(parent_id INTEGER REFERENCES parents(id));')
    cursor = connection.execute('INSERT INTO parents(name) VALUES(?)', ('example',))
    assert cursor.lastrowid == 1
    connection.commit()
    connection.row_factory = sqlite3.Row
    row = connection.execute('SELECT id,name FROM parents').fetchone()
    assert row['name'] == 'example'
    assert row[0] == 1
    assert tuple(row) == (1, 'example')
    assert row.keys() == ('id', 'name')
    assert [record['name'] for record in connection.execute('SELECT name FROM parents')] == ['example']
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute('INSERT INTO parents(name) VALUES(?)', ('example',))
    connection.rollback()
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute('INSERT INTO children VALUES(999)')
    connection.rollback()
    connection.execute('INSERT INTO parents(name) VALUES(?)', ('rolled back',))
    connection.rollback()
    assert connection.execute('SELECT COUNT(*) AS n FROM parents').fetchone()['n'] == 1
    connection.close()


def test_production_cannot_fall_back_to_a_local_database(monkeypatch, tmp_path):
    monkeypatch.delenv('TURSO_DATABASE_URL', raising=False)
    monkeypatch.setenv('VERCEL', '1')
    with pytest.raises(RuntimeError, match='Production requires'):
        database.connect(tmp_path / 'must-not-exist.db')
    assert not (tmp_path / 'must-not-exist.db').exists()


def test_missing_cloud_token_fails_instead_of_using_local_storage(monkeypatch):
    monkeypatch.setenv('TURSO_DATABASE_URL', 'libsql://test.example')
    monkeypatch.delenv('TURSO_AUTH_TOKEN', raising=False)
    with pytest.raises(RuntimeError, match='TURSO_AUTH_TOKEN'):
        database.connect('unused.db')
