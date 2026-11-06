# Installation

The batch runs on oculus as the user `reversi`, who owns the inbox `/nne/data` and may delete imported files there.

## Prerequisites (once per machine, with sudo)

uv in `/usr/local/bin` and Python 3.14.4 in `/opt/uv/python`, shared by all users of the machine:

```sh
curl -LsSf https://astral.sh/uv/install.sh | sudo env UV_INSTALL_DIR=/usr/local/bin INSTALLER_NO_MODIFY_PATH=1 sh
sudo env UV_PYTHON_INSTALL_DIR=/opt/uv/python uv python install 3.14.4
sudo chmod -R a+rX /opt/uv
```

## Apollo database address (once per machine, with sudo)

The report database on Apollo is reached on its internal address `10.133.16.10`, which the DNS of oculus does not know
(only the VPN DNS resolves `apollo.backend.crnalab.net`). The name is added to `/etc/hosts`:

```sh
echo "10.133.16.10 apollo.backend.crnalab.net" | sudo tee -a /etc/hosts
getent hosts apollo.backend.crnalab.net    # 10.133.16.10 apollo.backend.crnalab.net
```

The env file then uses `REPORT_DB_URL =apollo.backend.crnalab.net:3306` (the IP `10.133.16.10:3306` works as well).

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

## Production settings

The env file of the batch is `/home/reversi/.config/nne-container-status-importer/batch.env` (owner `reversi`, mode 600):
`REPORT_DB_*` of the report database (user `nne-containers` with SELECT, INSERT, UPDATE on `report.container_rawdata`) and
`SMTP_*` of the failure email ([USAGE.md](USAGE.md#settings-env-file-of---write)). It is never committed.

```sh
sudo -u reversi mkdir -p -m 700 /home/reversi/.config/nne-container-status-importer
sudo install -o reversi -g reversi -m 600 <prepared env file> /home/reversi/.config/nne-container-status-importer/batch.env
```

The batch writes its log to `logs/` of the repository, which `reversi` may write through the group `drifters`.

## Service (with sudo)

`deploy/nne-container-status-importer.service` runs the batch once as `reversi` with `--write` and `--notify`;
`deploy/nne-container-status-importer.timer` starts it every hour (a run still active is not started again, a missed hour
is caught up after a reboot):

```sh
sudo cp deploy/nne-container-status-importer.service deploy/nne-container-status-importer.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now nne-container-status-importer.timer
systemctl list-timers nne-container-status-importer.timer     # next run
journalctl -u nne-container-status-importer.service           # output of the runs
```

A run outside the schedule: `sudo systemctl start nne-container-status-importer.service`. Another notification address:
change `--notify` in the service file, copy it again and run `sudo systemctl daemon-reload`.
