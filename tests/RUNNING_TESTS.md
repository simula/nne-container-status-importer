# Running the tests

How to run the complete test suite by hand. Setup of the virtual environment and the local test database:
[documentation/DEVELOPMENT.md](../documentation/DEVELOPMENT.md).

## Prerequisites

1. Environment with the project and the test dependencies (`-e ".[test]"`), activated: on macOS the conda environment
   `nne-container-status-importer` (documentation/DEVELOPMENT.md), on oculus `.venv` (documentation/INSTALLATION.md)
2. Local test database running: `docker compose --file docker/db.compose.yaml up --detach --wait`
3. `.db_env_test` in the repository root (content in DEVELOPMENT.md)

## Complete suite

```sh
python -m pytest
```

Expected: `25 passed`. Without `.db_env_test` the two database tests are skipped: `23 passed, 2 skipped`.

## Test files

| File | Checks | Needs | Writes |
|---|---|---|---|
| `test_importer.py` (18) | thread to process mapping per container, unknown container, BAD files (not xz, not an object, missing version, unknown version, missing fields), file gone (LEAVE), uptime / sequence rule, file order by name timestamp, rsync temporary files ignored, last update per key, files kept by a dry run and by a failed write | – | temporary folders only |
| `test_notification.py` (4) | failure email from the template to all recipients with `Date`, `Message-ID` and traceback, a mail error does not hide the failure, no email for a successful run, IPv4 connection | – | nothing: SMTP is replaced by a stand-in, no email is sent |
| `test_logging.py` (1) | in a new Python process the whole log, nnedbtools included, goes to `--log-dir`; nnedbtools writes no log file of its own | – | temporary folder only |
| `test_database.py` (2) | upsert keeps one row per key with the latest values; a write deletes the GOOD files and keeps the BAD ones | local test database, `.db_env_test` | rows of the test node 99999 in `127.0.0.1:23307/report_test`, removed before and after |

Test data: real heartbeat files of node 7056, 2026-11-06 09:00 UTC, one per container
(`tests/data/7056/heartbeats/2026-11-06/`). The tests never touch `/nne/data`, never write to production and never send
email; the database test refuses any database other than `127.0.0.1:23307/report_test`.

## Selections

```sh
python -m pytest tests/test_importer.py                  # one file
python -m pytest -k "thread_to_process and udpping"      # tests by name
python -m pytest -v                                      # one line per test
python -m pytest -x                                      # stop at the first failure
```

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `2 skipped` | `.db_env_test` missing | create it (DEVELOPMENT.md) |
| database tests fail with `'NoneType' object has no attribute 'url'` | the local database is not running | start it with docker compose |
| docker compose: port 23307 already allocated | another container uses the port | stop it or change the port in `docker/db.compose.yaml` and `.db_env_test` |
| installing the project fails on `nnedbtools` | no SSH access to GitHub | `ssh -T git@github.com`, load the key (`ssh-add`) |
| `assert ... == ("127.0.0.1", 23307, "report_test")` | `.db_env_test` points at another database | fix `.db_env_test`; the test refuses to write anywhere else |

## Manual end-to-end test

The local full import of a copy of the inbox (DEVELOPMENT.md, "Running the batch locally") checks the whole batch with
real data. Afterwards the rows can be counted in the local database:

```sh
docker exec container-status-db sh -c 'mariadb -uroot -p"$MARIADB_ROOT_PASSWORD" -e \
  "SELECT container, COUNT(*) FROM report_test.container_rawdata GROUP BY container"'
```

The number of rows equals "Updates, one per container_rawdata key" of the summary; a second run of the same files
adds no rows (upsert).
