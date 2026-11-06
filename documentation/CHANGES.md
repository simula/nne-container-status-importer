# Changes

## 1.1.0 (2026-11-06)

- `--bad-dir DIR`: with `--write` the BAD files are moved to `DIR` after the database commit, same path as in the inbox;
  the service moves them to `/nne/bad/` (copy the service file again, `daemon-reload`)

## 1.0.0 – first release (2026-11-06)

Hourly batch importing the container heartbeat files of the NNE nodes from the inbox `/nne/data` on oculus into the
report table `container_rawdata`; in production on oculus since 2026-11-06 (first run 13:47, hourly from 15:00).

- Inbox processing: every complete heartbeat file, oldest creation timestamp first, rsync temporary files ignored; file
  format version 1; containers metadata, udpping, mqtt, speedtest, netperfmeter, traceroute
- Last update per table key, one upsert per run, GOOD files deleted after the commit, BAD files left in the inbox
- Dry run by default, `--write DB_ENV` for the database write
- Failure email (`--notify`) handed directly to the recipients' mail server over IPv4, template
  `templates/failure_email.txt`
- Log in `--log-dir` (rotated at midnight, 30 days), nnedbtools included
- systemd service and hourly timer as `reversi` (`deploy/`)
- nnedbtools `v.2026-11-06-Heartbeat-Split` (Python 3.14.4)
