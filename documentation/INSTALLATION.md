# Installation

The batch runs on oculus as the user `reversi`, who owns the inbox `/nne/data` and may delete imported files there.

## Prerequisites (once per machine, with sudo)

uv in `/usr/local/bin` and Python 3.14.4 in `/opt/uv/python`, shared by all users of the machine:

```sh
curl -LsSf https://astral.sh/uv/install.sh | sudo env UV_INSTALL_DIR=/usr/local/bin INSTALLER_NO_MODIFY_PATH=1 sh
sudo env UV_PYTHON_INSTALL_DIR=/opt/uv/python uv python install 3.14.4
sudo chmod -R a+rX /opt/uv
```

## Virtual environment with the project and nnedbtools

From the repository root. The project installs nnedbtools from GitHub over SSH, pinned in `pyproject.toml` to the
release tag `v.2026-11-06-Heartbeat-Split` (needs a key with access to `simula/nnedbtools`, e.g. a forwarded agent):

```sh
export UV_PYTHON_INSTALL_DIR=/opt/uv/python
uv venv --python 3.14.4 .venv
uv pip install --python .venv/bin/python .
.venv/bin/container-status-importer --help
```

`.venv` is ignored by git. Another nnedbtools version: change the tag in `pyproject.toml` and rerun the install command.

For development and tests install the project editable with the test dependencies:
`uv pip install --python .venv/bin/python -e ".[test]"` ([DEVELOPMENT.md](DEVELOPMENT.md)).
