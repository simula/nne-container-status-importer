"""Parsing and file selection of the heartbeat importer, no database.

Real files of node 7056, 2026-11-06 09:00 UTC, one per container: tests/data/7056/heartbeats/2026-11-06/.
"""

import json
import lzma
from datetime import datetime
from pathlib import Path

import pytest

from container_status_importer import importer
from container_status_importer.importer import (
    HeartbeatFileStatus,
    heartbeat_files,
    main,
    process_heartbeat_file,
    process_heartbeat_files,
)

DATA = Path(__file__).parent / "data"
DAY = DATA / "7056" / "heartbeats" / "2026-11-06"


def _sample(container: str) -> Path:
    return DAY / f"heartbeat_{container}_20261106T090000.json.xz"


def _write(path: Path, record: dict) -> Path:
    path.write_bytes(lzma.compress(json.dumps(record).encode()))
    return path


@pytest.mark.parametrize(
    "container, processes",
    [
        ("metadata", {("homekeeping", None), ("metadata", None)}),
        ("mqtt", {("container", None), ("mqtt", None)}),
        ("netperfmeter", {("container", None)}),
        ("speedtest", {("container", None)}),
        ("traceroute", {("container", None)}),
        (
            "udpping",
            {
                ("container", None),
                ("sender-10506", None),
                ("sender-11484", None),
                ("receiver-10506", "313741"),
                ("receiver-11484", "313986"),
            },
        ),
    ],
)
def test_thread_to_process(container, processes):
    status, updates = process_heartbeat_file(_sample(container))
    assert status == HeartbeatFileStatus.GOOD
    assert {(u.process, u.extra) for u in updates} == processes
    assert {(u.node_id, u.container.value) for u in updates} == {(7056, container)}


def test_unsupported_container_is_bad(tmp_path):
    record = json.loads(lzma.decompress(_sample("mqtt").read_bytes()))
    record["container_name"] = "unknown"
    assert process_heartbeat_file(_write(tmp_path / "a.json.xz", record)) == (HeartbeatFileStatus.BAD, None)


@pytest.mark.parametrize(
    "content",
    [
        b"not xz",
        lzma.compress(b"[1, 2]"),
        lzma.compress(json.dumps({"node_id": 7056}).encode()),
        lzma.compress(json.dumps({"version": 2}).encode()),
        lzma.compress(json.dumps({"version": 1, "node_id": 7056}).encode()),
    ],
    ids=["not_xz", "not_object", "no_version", "unknown_version", "missing_fields"],
)
def test_bad_file(tmp_path, content):
    path = tmp_path / "heartbeat_mqtt_20261106T090000.json.xz"
    path.write_bytes(content)
    assert process_heartbeat_file(path) == (HeartbeatFileStatus.BAD, None)


def test_missing_file_is_left(tmp_path):
    assert process_heartbeat_file(tmp_path / "heartbeat_mqtt_20261106T090000.json.xz") == (
        HeartbeatFileStatus.LEAVE,
        None,
    )


def test_no_update_when_uptime_reaches_sequence(tmp_path):
    # mqtt container up from 2026-10-13 08:59:54 to 2026-11-06 09:00:00: 576 whole hours
    record = json.loads(lzma.decompress(_sample("mqtt").read_bytes()))
    record["sequence"] = 576
    assert process_heartbeat_file(_write(tmp_path / "a.json.xz", record)) == (HeartbeatFileStatus.GOOD, [])
    record["sequence"] = 577
    assert len(process_heartbeat_file(_write(tmp_path / "b.json.xz", record))[1]) == 2


def test_selection_order_and_rsync_temporary_files(tmp_path):
    day = tmp_path / "7056" / "heartbeats" / "2026-11-06"
    day.mkdir(parents=True)
    names = ["heartbeat_mqtt_20261106T100000.json.xz", "heartbeat_udpping_20261106T090000.json.xz",
             ".heartbeat_mqtt_20261106T110000.json.xz.Xy12Ab"]
    for name in names:
        (day / name).write_bytes(b"")
    assert [p.name for p in heartbeat_files(tmp_path)] == names[1::-1]


def test_last_update_per_key(tmp_path):
    record = json.loads(lzma.decompress(_sample("mqtt").read_bytes()))
    record["seen_ts"] = "2026-11-06T10:00:00"
    later = _write(tmp_path / "heartbeat_mqtt_20261106T100000.json.xz", record)
    file_counts, updates, good_files = process_heartbeat_files([_sample("mqtt"), later])
    assert file_counts == {("GOOD", "mqtt"): 2}
    assert {(u.process, u.seen_ts) for u in updates} == {
        ("container", datetime(2026, 11, 6, 10)),
        ("mqtt", datetime(2026, 11, 6, 10)),
    }


@pytest.mark.parametrize("write", [False, True], ids=["dry_run", "failed_write"])
def test_files_kept_without_successful_write(tmp_path, monkeypatch, write):
    good = tmp_path / "7056" / "heartbeats" / "2026-11-06" / _sample("mqtt").name
    good.parent.mkdir(parents=True)
    good.write_bytes(_sample("mqtt").read_bytes())
    if write:
        def fail(updates):
            raise RuntimeError("database down")
        monkeypatch.setattr(importer, "load_from_enviroment", lambda db_config_file: None)
        monkeypatch.setattr(importer, "update_heartbeat_status", fail)
        with pytest.raises(RuntimeError):
            main(["--data-dir", str(tmp_path), "--write", "unused.env"])
    else:
        assert main(["--data-dir", str(tmp_path)]) == 0
    assert good.exists()


class _FakeSMTP:
    """Stands in for the SMTP connection: records the messages, `fail` makes sending fail (no mail is sent)."""

    sent: list = []
    fail: bool = False

    def __init__(self, host, port, timeout):
        self.server = (host, port)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def ehlo(self):
        return (250, b"ok")

    def has_extn(self, name):
        return name == "starttls"

    def starttls(self):
        pass

    def send_message(self, message):
        if _FakeSMTP.fail:
            raise importer.smtplib.SMTPDataError(550, b"rejected")
        _FakeSMTP.sent.append(message)


@pytest.mark.parametrize("mail_fails", [False, True], ids=["mail_sent", "mail_error"])
def test_failed_run_is_emailed_and_raised(tmp_path, monkeypatch, mail_fails):
    env_file = tmp_path / "batch.env"
    env_file.write_text("SMTP_HOST=smtp.example.org\nSMTP_PORT=25\nSMTP_SENDER=batch@example.org\n")
    inbox = tmp_path / "inbox" / "7056" / "heartbeats" / "2026-11-06"
    inbox.mkdir(parents=True)
    (inbox / _sample("mqtt").name).write_bytes(_sample("mqtt").read_bytes())

    def fail(updates):
        raise RuntimeError("database down")

    monkeypatch.setattr(importer, "load_from_enviroment", lambda db_config_file: None)
    monkeypatch.setattr(importer, "update_heartbeat_status", fail)
    monkeypatch.setattr(importer, "_SMTPv4", _FakeSMTP)
    monkeypatch.setattr(_FakeSMTP, "sent", [])
    monkeypatch.setattr(_FakeSMTP, "fail", mail_fails)
    with pytest.raises(RuntimeError, match="database down"):
        main(["--data-dir", str(tmp_path / "inbox"), "--write", str(env_file), "--notify", "a@x.org", "b@x.org"])
    if mail_fails:
        assert _FakeSMTP.sent == []
    else:
        (message,) = _FakeSMTP.sent
        assert message["To"] == "a@x.org, b@x.org" and message["From"] == "batch@example.org"
        assert message["Subject"].startswith("[nne-container-status-importer] batch failed on ")
        assert message["Date"] and message["Message-ID"]
        assert "RuntimeError: database down" in message.get_content() and "Traceback" in message.get_content()


def test_successful_run_sends_no_email(tmp_path, monkeypatch):
    monkeypatch.setattr(importer, "load_from_enviroment", lambda db_config_file: None)
    monkeypatch.setattr(importer, "update_heartbeat_status", lambda updates: None)
    monkeypatch.setattr(importer, "_SMTPv4", _FakeSMTP)
    monkeypatch.setattr(_FakeSMTP, "sent", [])
    assert main(["--data-dir", str(tmp_path), "--write", "unused.env", "--notify", "a@x.org"]) == 0
    assert _FakeSMTP.sent == []


def test_smtp_connects_over_ipv4(monkeypatch):
    addresses = []
    monkeypatch.setattr(importer.socket, "gethostbyname", lambda host: "192.0.2.25")
    monkeypatch.setattr(importer.socket, "create_connection", lambda address, timeout, source: addresses.append(address))
    importer._SMTPv4()._get_socket("smtp.example.org", 25, 30)
    assert addresses == [("192.0.2.25", 25)]
