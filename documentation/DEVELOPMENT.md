# Development

## Development on macOS (localhost)

Checked on macOS 26.5 (Apple silicon) with Anaconda: environment created from `environment.yml`, 25 tests passed.

### Prerequisites

- conda ([Anaconda](https://www.anaconda.com/download) or [Miniforge](https://conda-forge.org/download/)) and
  [Docker Desktop](https://www.docker.com/products/docker-desktop/) (running)
- an SSH key with access to `simula/nnedbtools` on GitHub, check with `ssh -T git@github.com`
- for the local full import only: the VPN and SSH access to oculus, check with `ssh oculus true`

### Setup

```sh
git clone git@github.com:simula/nne-container-status-importer.git
cd nne-container-status-importer
conda env create -f environment.yml
conda activate nne-container-status-importer
container-status-importer --help
```

`environment.yml` takes Python 3.14.4 from conda-forge and installs the project with pip, editable and with the test
dependencies (`-e ".[test]"`): changes in `src/` take effect without reinstalling. The dependencies are defined only in
`pyproject.toml`; nnedbtools comes from the tag pinned there. Another tag: change it and run `pip install -e ".[test]"`
in the activated environment.

Visual Studio Code: select the interpreter of the conda environment `nne-container-status-importer` (Python: Select
Interpreter) and pytest in the Testing view; pytest reads its settings from `pyproject.toml` (`tests/`).

## Code structure

| Module | Task |
|---|---|
| `src/container_status_importer/cli.py` | entry point `container-status-importer`: arguments, logging (set up before nnedbtools is imported), run, failure email |
| `src/container_status_importer/importer.py` | the batch: file selection, reading and parsing (`HeartbeatRecord` per file format version), thread to process mapping, last update per key, upsert (`update_heartbeat_status`), deletion of the GOOD files (`run`) |
| `src/container_status_importer/notification.py` | failure email (`send_failure_email`), IPv4 SMTP connection |
| `src/container_status_importer/templates/failure_email.txt` | subject and body of the failure email (`string.Template`) |
| `deploy/` | systemd service and timer |

Flow of a run (`cli.main` → `importer.run`):

1. `heartbeat_files`: all complete heartbeat files of the inbox, oldest creation timestamp (file name) first
2. `process_heartbeat_files` → `process_heartbeat_file` per file: GOOD with its updates, BAD, LEAVE; the last update per
   key of `container_rawdata` is kept
3. summary on stdout
4. with `--write`: `update_heartbeat_status` (one upsert, commit), then the GOOD files are deleted
5. on any exception: `send_failure_email` (with `--notify`), the exception is raised again

Where changes go:

| Change | Place |
|---|---|
| another thread of a container, another process name | `HeartbeatRecord.process_update` (mapping table in USAGE.md) |
| a new file format version | a new `HeartbeatRecordv<N>` class and an entry in `RECORD_VERSIONS` |
| a new container | `DockerContainer` in nnedbtools (`enums.py`): library release with a new tag, the tag in `pyproject.toml`, then the mapping in `process_update` |
| table columns or key | `ReportContainerRawdata` in nnedbtools (`tables.py`), the table on Apollo, `update_heartbeat_status` |
| email text | `templates/failure_email.txt` |

After a change: tests (with a test for the new case, real heartbeat files where possible), the documentation, an entry
in [CHANGES.md](CHANGES.md) and the version in `pyproject.toml`.

## Local test database

A local MariaDB in Docker (same server version as production, 10.11.14) on `127.0.0.1:23307`, schema `report_test`. Own
compose project, container and port, so it runs next to the nnedbtools test database (23306):

```sh
docker compose --file docker/db.compose.yaml up --detach --wait     # start
docker compose --file docker/db.compose.yaml down                   # stop (the data stays in the volume)
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
python -m pytest     # in the activated environment
```

What each test checks and needs, selections and troubleshooting: [tests/RUNNING_TESTS.md](../tests/RUNNING_TESTS.md).

## Running the batch locally

On a copy of the inbox in `data/` (git-ignored), never on `/nne/data`:

```sh
rsync -a -m --include='/*/' --include='/*/heartbeats/***' --exclude='*' oculus:/nne/data/ data/
container-status-importer --data-dir data                          # dry run: summary only
container-status-importer --data-dir data --write .db_env_test     # import into the local test database
```

Without `--log-dir` the log goes to stderr (`--log-dir logs-local` writes `logs-local/importer.log`). The write deletes
the imported (GOOD) files from `data/`; copy them again with rsync for another run. The full import (about 1.6 million
files) takes about 5 minutes.
