# nne-container-status-importer

Hourly batch importing the container heartbeat files of the NNE nodes (`/nne/data/<node>/heartbeats/`) into the report
table `container_rawdata`, built on the [nnedbtools](https://github.com/simula/nnedbtools) library.

## Installation

### Prerequisites (once per machine, with sudo)

uv in `/usr/local/bin` and Python 3.14.4 in `/opt/uv/python`, shared by all users of the machine:

```sh
curl -LsSf https://astral.sh/uv/install.sh | sudo env UV_INSTALL_DIR=/usr/local/bin INSTALLER_NO_MODIFY_PATH=1 sh
sudo env UV_PYTHON_INSTALL_DIR=/opt/uv/python uv python install 3.14.4
sudo chmod -R a+rX /opt/uv
```

### Virtual environment with nnedbtools

From the repository root. nnedbtools is installed from GitHub over SSH (a key with access to `simula/nnedbtools`, e.g.
a forwarded agent), pinned to a commit or a release tag:

```sh
export UV_PYTHON_INSTALL_DIR=/opt/uv/python
uv venv --python 3.14.4 .venv
uv pip install --python .venv/bin/python "nnedbtools @ git+ssh://git@github.com/simula/nnedbtools.git@34228de"
.venv/bin/python -c "import nnedbtools"
```

`.venv` is ignored by git. Another nnedbtools version: rerun the install command with another commit or tag.

## Local test import

A local MariaDB (same server version as production) in Docker on `127.0.0.1:23307`, schema `report_test`, and a copy of
the heartbeat files in `data/` (git-ignored):

```sh
docker compose --file docker/db.compose.yaml up --detach --wait
rsync -a -m --include='/*/' --include='/*/heartbeats/***' --exclude='*' oculus:/nne/data/ data/
container-status-importer --data-dir data --since 2026-04-08 --write .db_env_test
```

`.db_env_test` (git-ignored) holds the database settings: every host is `127.0.0.1:23307`, user `root`, password
`password` (see `docker/db.compose.yaml`), report and nne schema `report_test`.
