"""python -m scripts.migrate_question_library --database NAME [--apply]."""
import argparse
from importlib import import_module
import json

from pymongo import MongoClient
from pymongo.errors import PyMongoError

from app.config import Settings


def main(argv=None):
    parser = argparse.ArgumentParser(description='Add independent question identities without rewriting legacy documents.')
    parser.add_argument('--database', required=True, help='Explicit target database; use an isolated copy for rehearsal.')
    parser.add_argument('--uri', help='Defaults to MONGODB_URI; do not put credentials into shared command logs.')
    parser.add_argument('--apply', action='store_true', help='Write new collections; default is read-only preflight.')
    args = parser.parse_args(argv)
    settings = Settings.from_env()
    migration = import_module('database.migrations.002_question_library')
    try:
        with MongoClient(args.uri or settings.mongodb_uri, serverSelectionTimeoutMS=3000, tz_aware=True) as client:
            client.admin.command('ping')
            report = migration.migrate(client[args.database], apply=args.apply)
    except (ValueError, PyMongoError) as error:
        # Connection exceptions can contain credentials or hosts; do not echo them.
        report = {'status': 'failed', 'database': args.database,
                  'migration': migration.MIGRATION_ID, 'error_type': type(error).__name__,
                  'message': str(error) if isinstance(error, ValueError) else 'MongoDB operation failed; inspect local database availability and retry.'}
        print(json.dumps(report, ensure_ascii=False))
        return 2 if isinstance(error, ValueError) else 1
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
