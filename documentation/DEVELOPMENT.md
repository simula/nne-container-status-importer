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
