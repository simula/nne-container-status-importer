# Development

## Local test database

A local MariaDB in Docker (same server version as production, 10.11.14) on `127.0.0.1:23307`, schema `report_test`. Own
compose project, container and port, so it runs next to the nnedbtools test database (23306):

```sh
docker compose --file docker/db.compose.yaml up --detach --wait
```

The database settings are in `.db_env_test` (git-ignored) in the repository root; every host points at the local
database:

```
NNE_DB_SERVERNAME       =localhost
NNE_DB_SCHEMA           =report_test
NNE_DB_USERNAME         =root
NNE_DB_PASSWORD         =password
NNE_DB_URL              =127.0.0.1:23307
NNE_DB_ROOT_USERNAME    =root
NNE_DB_ROOT_PASSWORD    =password
NNE_REPORT_YEAR         =2026
REPORT_DB_SERVERNAME    =localhost
REPORT_DB_SCHEMA        =report_test
REPORT_DB_USERNAME      =root
REPORT_DB_PASSWORD      =password
REPORT_DB_URL           =127.0.0.1:23307
REPORT_DB_ROOT_USERNAME =root
REPORT_DB_ROOT_PASSWORD =password
```

## Tests

```sh
uv pip install --python .venv/bin/python -e ".[test]"
.venv/bin/python -m pytest
```

- `tests/test_importer.py`: parsing, thread to process mapping, BAD and LEAVE files, file selection and order, files
  kept by a dry run and a failed write; no database. Test files: real heartbeat files of node 7056, 2026-11-06 09:00
  UTC, one per container (`tests/data/7056/heartbeats/2026-11-06/`).
- `tests/test_notification.py`: failure email from the template, recipients, headers, IPv4 connection (SMTP replaced
  by a stand-in, no email is sent).
- `tests/test_logging.py`: in a new process the whole log, nnedbtools included, goes to `--log-dir`.
- `tests/test_database.py`: upsert and deletion of the GOOD files against the local test database; skipped without
  `.db_env_test`, refuses any database other than `127.0.0.1:23307/report_test`, writes only rows of the test node 99999
  and removes them.

## Local full import

A copy of the inbox in `data/` (git-ignored), imported into the local test database:

```sh
rsync -a -m --include='/*/' --include='/*/heartbeats/***' --exclude='*' oculus:/nne/data/ data/
container-status-importer --data-dir data --write .db_env_test
```

Without `--log-dir` the log goes to stderr.

The write deletes the imported (GOOD) files from `data/`; copy them again with rsync for another run.
