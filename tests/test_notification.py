"""Failure email: template, recipients, headers, IPv4 connection; SMTP replaced by a stand-in, no email is sent."""

from pathlib import Path

import pytest

from container_status_importer import importer, notification
from container_status_importer.importer import main

SAMPLE = Path(__file__).parent / "data" / "7056" / "heartbeats" / "2026-11-06" / "heartbeat_mqtt_20261106T090000.json.xz"


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
            raise notification.smtplib.SMTPDataError(550, b"rejected")
        _FakeSMTP.sent.append(message)


@pytest.mark.parametrize("mail_fails", [False, True], ids=["mail_sent", "mail_error"])
def test_failed_run_is_emailed_and_raised(tmp_path, monkeypatch, mail_fails):
    env_file = tmp_path / "batch.env"
    env_file.write_text("SMTP_HOST=smtp.example.org\nSMTP_PORT=25\nSMTP_SENDER=batch@example.org\n")
    inbox = tmp_path / "inbox" / "7056" / "heartbeats" / "2026-11-06"
    inbox.mkdir(parents=True)
    (inbox / SAMPLE.name).write_bytes(SAMPLE.read_bytes())

    def fail(updates):
        raise RuntimeError("database down")

    monkeypatch.setattr(importer, "load_from_enviroment", lambda db_config_file: None)
    monkeypatch.setattr(importer, "update_heartbeat_status", fail)
    monkeypatch.setattr(notification, "_SMTPv4", _FakeSMTP)
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
    monkeypatch.setattr(notification, "_SMTPv4", _FakeSMTP)
    monkeypatch.setattr(_FakeSMTP, "sent", [])
    assert main(["--data-dir", str(tmp_path), "--write", "unused.env", "--notify", "a@x.org"]) == 0
    assert _FakeSMTP.sent == []


def test_smtp_connects_over_ipv4(monkeypatch):
    addresses = []
    monkeypatch.setattr(notification.socket, "gethostbyname", lambda host: "192.0.2.25")
    monkeypatch.setattr(notification.socket, "create_connection", lambda address, timeout, source: addresses.append(address))
    notification._SMTPv4()._get_socket("smtp.example.org", 25, 30)
    assert addresses == [("192.0.2.25", 25)]
