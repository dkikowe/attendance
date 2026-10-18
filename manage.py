"""Explicit schema/bootstrap command for a linked cloud database."""
import argparse

parser = argparse.ArgumentParser()
parser.add_argument('command', choices=['init-db'])
args = parser.parse_args()

from app import init_db
from attendance import init_attendance_db

if args.command == 'init-db':
    init_db()
    init_attendance_db()
    print('Attendance database initialized without deleting existing records.')
