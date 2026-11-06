"""Failure email of the batch: one email per failed run, handed directly to the mail server of the recipients."""

import logging
import smtplib
import socket
import sys
import traceback
from datetime import datetime
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from importlib.resources import files
from pathlib import Path
from string import Template
from typing import List

from environs import Env

# Email template (templates/failure_email.txt): the subject line, an empty line, the body
TEMPLATE: Template = Template(
    files("container_status_importer").joinpath("templates", "failure_email.txt").read_text(encoding="utf-8")
)


class _SMTPv4(smtplib.SMTP):
    """
    SMTP connection over IPv4 only: Gmail rejects IPv6 senders without PTR records and authentication, and the
    batch server (VPN only) has no reverse DNS; over IPv4 the mail is accepted without login
    """

    def _get_socket(self, host: str, port: int, timeout: float) -> socket.socket:
        return socket.create_connection((socket.gethostbyname(host), port), timeout, self.source_address)


def send_failure_email(env_file: Path, recipients: List[str], started: datetime, error: BaseException) -> None:
    """Sends the failure of a run to the recipients; a failure to send is only logged

    The email is handed directly to the mail server of the recipients, without login and without a local mail server.
    The SMTP settings come from the env file: `SMTP_HOST` (mail server of the recipients, e.g. `smtp.google.com`),
    `SMTP_PORT` (e.g. 25) and `SMTP_SENDER` (sender address).

    Args:
        env_file (Path): env file of the run (`--write`) with the SMTP settings
        recipients (List[str]): email addresses (`--notify`)
        started (datetime): start of the run (local time)
        error (BaseException): exception of the failed run
    """
    failed: datetime = datetime.now()
    try:
        # 1. SMTP settings
        env = Env()
        env.read_env(str(env_file), recurse=False)

        # 2. Message from the template with the error and its traceback
        subject_line, body = TEMPLATE.substitute(
            host=socket.getfqdn(),
            started=started,
            failed=failed,
            runtime=failed - started,
            command=" ".join(sys.argv),
            error=f"{type(error).__name__}: {error}",
            traceback="".join(traceback.format_exception(error)),
        ).split("\n\n", 1)
        message = EmailMessage()
        message["Subject"] = subject_line.removeprefix("Subject: ")
        message["From"] = env.str("SMTP_SENDER")
        message["To"] = ", ".join(recipients)
        message["Date"] = formatdate(localtime=True)
        message["Message-ID"] = make_msgid(domain=socket.getfqdn())
        message.set_content(body)

        # 3. Send over IPv4, STARTTLS when offered, no login
        with _SMTPv4(env.str("SMTP_HOST"), env.int("SMTP_PORT"), timeout=30) as smtp:
            smtp.ehlo()
            if smtp.has_extn("starttls"):
                smtp.starttls()
            smtp.send_message(message)
        logging.info(f"-- Failure email sent to {', '.join(recipients)}")
    except Exception as mail_error:
        logging.error(f"-- Failure email could not be sent: {mail_error}")
