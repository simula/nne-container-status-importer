# Usage

## Command line

```sh
container-status-importer [--data-dir DIR] [--write DB_ENV]
```

| Option | Default | Meaning |
|---|---|---|
| `--data-dir DIR` | `/nne/data` | inbox with the node directories |
| `--write DB_ENV` | – (dry run) | write into `container_rawdata` with the database settings of the env file `DB_ENV`, then delete the imported files |

Without `--write` the batch only reads the files and prints the summary: nothing is written and no file is deleted.

The summary lists the files per status and container, the updates per container and process and, with `--write`, the
number of written updates and deleted files. The library log is written to `logs/robustnet<date-time>.log` in the
working directory.

## Inbox and file format

The nodes deliver the files with rsync into `<data-dir>/<node>/heartbeats/<YYYY-MM-DD>/`, one file per container per
hour: `heartbeat_<container>_<YYYYMMDDTHHMMSS>.json.xz` (xz compressed JSON, times in UTC). rsync writes a file under a
hidden temporary name (`.heartbeat_...json.xz.<random>`) and renames it when the transfer is complete; only complete
names are selected.

File format version 1:

```json
{"node_id": 7056, "start_ts": "2026-10-13T08:59:50.489892", "seen_ts": "2026-11-06T09:00:00.000333",
 "container_name": "udpping", "container_version": "electro-2-2-5", "uptime": 2073609.510372,
 "threads": ["MainThread", "UdpSend10506", "UdpRecv10506-313741"], "sequence": 589, "version": 1}
```

## Processing

1. Every heartbeat file present is processed, oldest creation timestamp (from the file name) first; the batch does not
   select by time.
2. Each file gets a status:

   | Status | Meaning | File |
   |---|---|---|
   | GOOD | read and converted into updates (possibly none) | deleted after the database commit |
   | BAD | unreadable (e.g. an unfinished transfer), missing fields, unsupported version or container | left in the inbox, read again in the next run |
   | LEAVE | gone before it was read | – |

3. Each monitored thread of a container gives one update of its process:

   | Container | Thread | Process | extra |
   |---|---|---|---|
   | metadata | `MetaColect` | `metadata` | – |
   | metadata | `HomeKeep`, `MainThread` | `homekeeping` | – |
   | udpping | `MainThread` | `container` | – |
   | udpping | `UdpSend<instance>` | `sender-<instance>` | – |
   | udpping | `UdpRecv<instance>-<extra>` | `receiver-<instance>` | `<extra>` |
   | mqtt | `MainThread` | `container` | – |
   | mqtt | `MqttClient` | `mqtt` | – |
   | speedtest, netperfmeter, traceroute | `MainThread` | `container` | – |

   Other threads are not monitored. No update is made when the whole hours between start and heartbeat reach the
   sequence number.
4. Only the last update per table key is written (files are processed oldest first, so the latest heartbeat wins).
5. With `--write`: one upsert into `container_rawdata` and commit; only then are the GOOD files deleted. A failed write
   raises before any file is deleted, so the next run imports the same files again.

## Target table `container_rawdata`

Report database (`REPORT_DB_*` settings), table model `nnedbtools.tables.ReportContainerRawdata`, created by the first
write if it does not exist.

| Column | Type | Key | Content |
|---|---|---|---|
| `node_id` | INT | yes | node identifier |
| `start_ts` | DATETIME(6) | yes | container start (UTC) |
| `container` | VARCHAR(20) | yes | `nnedbtools.enums.DockerContainer` value |
| `version` | VARCHAR(30) | yes | container version (build name/tag) |
| `process` | VARCHAR(25) | yes | process (see the mapping above) |
| `seen_ts` | DATETIME(6) | – | last heartbeat (UTC) |
| `uptime` | FLOAT | – | uptime in seconds |
| `extra` | VARCHAR(25) | – | extra information (udpping receiver) |

A row exists per running container process; the upsert updates `seen_ts`, `uptime` and `extra` of an existing key.

## Database settings

The env file passed to `--write` uses the variables of nnedbtools (`NNE_DB_*`, `REPORT_DB_*`, `NNE_REPORT_YEAR`; see the
nnedbtools documentation, INSTALLATION.md). The batch writes only to the report database.
