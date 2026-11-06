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
