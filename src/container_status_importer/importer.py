"""Import of NNE container heartbeat files into the report table `container_rawdata` (hourly batch).

Heartbeat files: <data-dir>/<node>/heartbeats/<YYYY-MM-DD>/heartbeat_<container>_<YYYYMMDDTHHMMSS>.json.xz, times in
UTC. The data directory is the inbox the nodes deliver into with rsync: every file present is processed, oldest creation
timestamp (from the name) first, and the GOOD files are deleted after the database commit.
"""

import argparse
import json
import logging
import lzma
import re
import smtplib
import socket
import sys
import traceback
from abc import ABC, abstractmethod
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage
from enum import Enum
from math import floor
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import nnedbtools.connection as con
import nnedbtools.tables as tb
from environs import Env
from nnedbtools.enums import DockerContainer
from nnedbtools.identityprovider import load_from_enviroment
from nnedbtools.utils import dt_functions
from sqlalchemy.dialects.mysql import insert

# Complete heartbeat file name: rsync's hidden temporary files (`.heartbeat_...json.xz.<random>`) never match
FILE_NAME = re.compile(r"heartbeat_(?P<container>[a-z]+)_(?P<ts>\d{8}T\d{6})\.json\.xz")


@dataclass
class HeartbeatUpdate:
    """
    Heartbeat Update Dataclass wrapper: one row of `container_rawdata`
    """

    # Node identifier
    node_id: int
    # Start timestamp of the container
    start_ts: datetime
    # Container enumeration
    container: DockerContainer
    # Container version (build name/tag)
    version: str
    # Running process name
    process: str
    # Last seen timestamp
    seen_ts: datetime
    # Uptime in seconds
    uptime: float
    # Extra information
    extra: Optional[str]


class HeartbeatFileStatus(Enum):
    """
    Enumeration of the processing results of one heartbeat file
    """

    # Not processed yet
    WORK = 0
    # Read and converted into updates (possibly none): deleted after the database commit
    GOOD = 1
    # Unreadable (e.g. unfinished transfer), unsupported version or container: left in the inbox
    BAD = 2
    # File gone before it was read: skipped
    LEAVE = 3


@dataclass
class HeartbeatRecord(ABC):
    """
    Heartbeat file content, base class of one class per file format version
    """

    # File format version
    version: int

    @abstractmethod
    def _updates(self) -> List[Optional[HeartbeatUpdate]]:
        """Converts the record into updates, `None` for threads that are not monitored

        Returns:
            List[Optional[HeartbeatUpdate]]: one entry per thread of the record
        """

    @classmethod
    @abstractmethod
    def from_dict(cls, data: Dict[str, Any]) -> "HeartbeatRecord":
        """Creates the record from the JSON content of a heartbeat file

        Args:
            data (Dict[str, Any]): JSON object of the file

        Returns:
            HeartbeatRecord: record of the file format version
        """

    def updates(self) -> List[HeartbeatUpdate]:
        """Updates of the monitored threads of the record

        Returns:
            List[HeartbeatUpdate]: updates without the threads that are not monitored
        """
        return [one_update for one_update in self._updates() if one_update is not None]

    def process_update(
        self,
        node_id: int,
        start_ts: datetime,
        container: str,
        version: str,
        thread: str,
        seen_ts: datetime,
        uptime: float,
        sequence: int,
    ) -> Optional[HeartbeatUpdate]:
        """Converts one thread of a container into an update of its process

        Thread to process mapping:
            * metadata: `MetaColect` -> metadata, `HomeKeep` and `MainThread` -> homekeeping
            * udpping: `MainThread` -> container, `UdpSend<instance>` -> sender-<instance>,
              `UdpRecv<instance>-<extra>` -> receiver-<instance> with extra
            * mqtt: `MainThread` -> container, `MqttClient` -> mqtt
            * speedtest, netperfmeter, traceroute: `MainThread` -> container

        Args:
            node_id (int): node identifier
            start_ts (datetime): start timestamp of the container (UTC)
            container (str): container name
            version (str): container version (build name/tag)
            thread (str): thread name reported by the container
            seen_ts (datetime): timestamp of the heartbeat (UTC)
            uptime (float): uptime in seconds
            sequence (int): heartbeat sequence number

        Raises:
            ValueError: container is not a `DockerContainer`

        Returns:
            Optional[HeartbeatUpdate]: update of the process, None for a thread that is not monitored or when the
            whole hours between start and heartbeat reach the sequence number
        """
        process: Optional[str] = None
        extra: Optional[str] = None

        # 1. Validate container
        try:
            container: DockerContainer = DockerContainer(container)
        except ValueError:
            raise ValueError(f"Unsupported container for monitoring {container}")

        # 2. Map thread to process
        if container == DockerContainer.METADATA:
            if thread == "MetaColect":
                process = "metadata"
            elif thread == "HomeKeep":
                process = "homekeeping"
            # This is homekeeping fallback to mainthread
            elif thread == "MainThread":
                process = "homekeeping"
            else:
                return None
        elif container == DockerContainer.UDPPING:
            if thread == "MainThread":
                process = "container"
            elif thread.startswith("UdpSend"):
                measurement_instance: str = thread.removeprefix("UdpSend")
                process = f"sender-{measurement_instance}"
            elif thread.startswith("UdpRecv"):
                instance_thread: List[str] = thread.removeprefix("UdpRecv").split("-")
                measurement_instance: str = instance_thread[0]
                process = f"receiver-{measurement_instance}"
                extra = instance_thread[1]
            else:
                return None
        elif container == DockerContainer.SPEEDTEST:
            if thread == "MainThread":
                process = "container"
            else:
                return None
        elif container == DockerContainer.MQTT:
            if thread == "MainThread":
                process = "container"
            elif thread == "MqttClient":
                process = "mqtt"
            else:
                return None
        elif container == DockerContainer.NETPERFMETER:
            if thread == "MainThread":
                process = "container"
            else:
                return None
        elif container == DockerContainer.TRACEROUTE:
            if thread == "MainThread":
                process = "container"
            else:
                return None

        # 3. Skip when the whole hours between start and heartbeat reach the sequence number
        uptime_whole_hours: float = floor((seen_ts - start_ts).total_seconds() / 3600)
        if uptime_whole_hours >= sequence:
            return None

        return HeartbeatUpdate(
            node_id=node_id,
            start_ts=start_ts,
            container=container,
            version=version,
            process=process,
            seen_ts=seen_ts,
            uptime=uptime,
            extra=extra,
        )


@dataclass
class HeartbeatRecordv1(HeartbeatRecord):
    """
    Heartbeat file content of file format version 1

    Example:
        ```json
        {"node_id": 7056, "start_ts": "2026-10-13T08:59:50.489892", "seen_ts": "2026-11-06T09:00:00.000333",
         "container_name": "udpping", "container_version": "electro-2-2-5", "uptime": 2073609.510372,
         "threads": ["MainThread", "UdpSend10506", "UdpRecv10506-313741"], "sequence": 589, "version": 1}
        ```
    """

    # Node identifier
    node_id: int
    # Start timestamp of the container (UTC)
    start_ts: datetime
    # Timestamp of the heartbeat (UTC)
    seen_ts: datetime
    # Container name
    container_name: str
    # Container version (build name/tag)
    container_version: str
    # Uptime in seconds
    uptime: float
    # Running threads
    threads: List[str]
    # Heartbeat sequence number
    sequence: int

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "HeartbeatRecordv1":
        """Creates the record from the JSON content of a version 1 heartbeat file

        Args:
            data (Dict[str, Any]): JSON object of the file

        Raises:
            KeyError: a required field is missing

        Returns:
            HeartbeatRecordv1: record with timestamps normalized to naive UTC
        """
        return cls(
            node_id=data["node_id"],
            start_ts=dt_functions.normalize_to_date_time_utc(data["start_ts"]),
            seen_ts=dt_functions.normalize_to_date_time_utc(data["seen_ts"]),
            container_name=data["container_name"],
            container_version=data["container_version"],
            uptime=float(data["uptime"]),
            threads=list(data.get("threads", [])),
            sequence=data["sequence"],
            version=data.get("version", 1),
        )

    def _updates(self) -> List[Optional[HeartbeatUpdate]]:
        """Converts every thread of the record (see `HeartbeatRecord.process_update`)

        Returns:
            List[Optional[HeartbeatUpdate]]: one entry per thread, None for threads that are not monitored
        """
        return [
            self.process_update(
                node_id=self.node_id,
                start_ts=self.start_ts,
                container=self.container_name,
                version=self.container_version,
                thread=thread,
                seen_ts=self.seen_ts,
                uptime=self.uptime,
                sequence=self.sequence,
            )
            for thread in self.threads
        ]


# Record class per file format version
RECORD_VERSIONS: Dict[int, type] = {1: HeartbeatRecordv1}


def _log_failure(message: str, file_path: Path, error: Exception) -> None:
    """Logs a file failure: the reason at info, the traceback at debug level

    Args:
        message (str): what failed
        file_path (Path): heartbeat file
        error (Exception): raised exception
    """
    logging.info(f"-- {message} {file_path}: {error}")
    for line in traceback.format_exc().splitlines():
        logging.debug(f"---- {line}")


def process_heartbeat_file(
    file_path: Path,
) -> Tuple[HeartbeatFileStatus, Optional[List[HeartbeatUpdate]]]:
    """Reads one heartbeat file and converts it into updates

    Args:
        file_path (Path): heartbeat file

    Returns:
        Tuple[HeartbeatFileStatus, Optional[List[HeartbeatUpdate]]]: GOOD with the updates of the file (possibly none),
        BAD with None when the file cannot be read (e.g. an unfinished transfer) or has an unsupported version or
        container, LEAVE with None when the file is gone
    """
    logging.debug(f"-- Processing {file_path}")

    # 1. File gone in the meantime
    if not file_path.is_file():
        return (HeartbeatFileStatus.LEAVE, None)

    # 2. Read xz compressed JSON
    try:
        with lzma.open(file_path, "rt", encoding="utf-8") as file_descriptor:
            data: Any = json.load(file_descriptor)
    except Exception as error:
        _log_failure("Failed to open", file_path, error)
        return (HeartbeatFileStatus.BAD, None)

    if not isinstance(data, dict):
        logging.info(f"-- Malformed json in file {file_path}")
        return (HeartbeatFileStatus.BAD, None)

    # 3. Select the record class of the file format version
    record_class: Optional[type] = RECORD_VERSIONS.get(data.get("version"))
    if record_class is None:
        logging.info(f"-- Missing or unsupported version {data.get('version')!r} in {file_path}")
        return (HeartbeatFileStatus.BAD, None)

    # 4. Convert into updates (missing fields, unsupported container -> BAD)
    try:
        updates: List[HeartbeatUpdate] = record_class.from_dict(data).updates()
    except Exception as error:
        _log_failure("Failed to extract updates from", file_path, error)
        return (HeartbeatFileStatus.BAD, None)

    if not updates:
        logging.debug(f"-- No valid updates found in {file_path}")
    return (HeartbeatFileStatus.GOOD, updates)


def heartbeat_files(data_dir: Path) -> List[Path]:
    """Lists the heartbeat files of all nodes present in the inbox

    Args:
        data_dir (Path): inbox, <data-dir>/<node>/heartbeats/<YYYY-MM-DD>/heartbeat_<container>_<ts>.json.xz

    Returns:
        List[Path]: complete heartbeat files, oldest creation timestamp (from the name) first; rsync's hidden temporary
        files are never listed
    """
    selected: List[Tuple[datetime, Path]] = []
    for file_path in data_dir.glob("*/heartbeats/*/heartbeat_*.json.xz"):
        match = FILE_NAME.fullmatch(file_path.name)
        if match:
            selected.append((datetime.strptime(match["ts"], "%Y%m%dT%H%M%S"), file_path))
    return [file_path for _, file_path in sorted(selected)]


def process_heartbeat_files(
    file_paths: List[Path],
) -> Tuple[Counter, List[HeartbeatUpdate], List[Path]]:
    """Processes heartbeat files in the given order (oldest first)

    Args:
        file_paths (List[Path]): heartbeat files, oldest first

    Returns:
        Tuple[Counter, List[HeartbeatUpdate], List[Path]]: file count per (status name, container of the file name),
        the last update per primary key of `container_rawdata`, the GOOD files
    """
    file_counts: Counter = Counter()
    latest: Dict[tuple, HeartbeatUpdate] = {}
    good_files: List[Path] = []
    for file_path in file_paths:
        # 1. Read the file
        status, updates = process_heartbeat_file(file_path=file_path)
        file_counts[(status.name, FILE_NAME.fullmatch(file_path.name)["container"])] += 1
        if status == HeartbeatFileStatus.GOOD:
            good_files.append(file_path)
        # 2. Keep the last update per key (files are processed oldest first)
        for update in updates or []:
            key: tuple = (update.node_id, update.start_ts, update.container, update.version, update.process)
            latest[key] = update
    return (file_counts, list(latest.values()), good_files)


def update_heartbeat_status(updates: List[HeartbeatUpdate]) -> None:
    """
    Updates records in table `container_rawdata` of the report database with data contained in heartbeat updates

    Args:
        updates (List[HeartbeatUpdate]): Listing of heartbeat updates parsed from files.


    SQL statement example:
    ```sql
        INSERT INTO container_rawdata (
            node_id, start_ts, container, version, process, seen_ts, uptime, extra
        )
        VALUES
            (8080, '2026-04-08 10:00:00', 'mqtt', 'locum-2-1-7', 'container', '2026-04-08 10:05:00', 12345, NULL)
        ON DUPLICATE KEY UPDATE
            seen_ts = VALUES(seen_ts),
            uptime  = VALUES(uptime),
            extra   = VALUES(extra);
    ```
    """

    # 1. Check applicability: at least one update record
    if len(updates) == 0:
        logging.info(f"-- No updates provided, there is no need to update {tb.ReportContainerRawdata.__tablename__}")
        return

    # 2. Create the table if it does not exist
    table = tb.ReportContainerRawdata
    table.__table__.create(bind=con.engines.report_engine, checkfirst=True)

    # 3. Insert all updates, ON DUPLICATE KEY UPDATE of the non-key values seen, uptime and extra
    with con.engines.report_session as session:
        insert_or_update_values = insert(table).values(
            [
                {
                    "node_id": u.node_id,
                    "start_ts": u.start_ts,
                    "container": u.container.value,
                    "version": u.version,
                    "process": u.process,
                    "seen_ts": u.seen_ts,
                    "uptime": u.uptime,
                    "extra": u.extra,
                }
                for u in updates
            ]
        )
        insert_or_update_values = insert_or_update_values.on_duplicate_key_update(
            seen_ts=insert_or_update_values.inserted.seen_ts,
            uptime=insert_or_update_values.inserted.uptime,
            extra=insert_or_update_values.inserted.extra,
        )
        session.execute(insert_or_update_values)
        session.commit()


def send_failure_email(env_file: Path, recipients: List[str], started: datetime, error: BaseException) -> None:
    """Sends the failure of a run to the recipients; a failure to send is only logged

    The SMTP settings come from the env file: `SMTP_HOST`, `SMTP_PORT` (STARTTLS), `SMTP_USER` (also the sender) and
    `SMTP_PASSWORD`.

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
        user: str = env.str("SMTP_USER")

        # 2. Message with the error and its traceback
        message = EmailMessage()
        message["Subject"] = f"[nne-container-status-importer] batch failed on {socket.getfqdn()}"
        message["From"] = user
        message["To"] = ", ".join(recipients)
        message.set_content(
            "\n".join(
                [
                    f"Batch nne-container-status-importer failed on {socket.getfqdn()}",
                    "",
                    f"Started: {started}",
                    f"Failed:  {failed}",
                    f"Runtime: {failed - started}",
                    f"Command: {' '.join(sys.argv)}",
                    f"Error:   {type(error).__name__}: {error}",
                    "",
                    "".join(traceback.format_exception(error)),
                ]
            )
        )

        # 3. Send over STARTTLS with login
        with smtplib.SMTP(env.str("SMTP_HOST"), env.int("SMTP_PORT"), timeout=30) as smtp:
            smtp.starttls()
            smtp.login(user, env.str("SMTP_PASSWORD"))
            smtp.send_message(message)
        logging.info(f"-- Failure email sent to {', '.join(recipients)}")
    except Exception as mail_error:
        logging.error(f"-- Failure email could not be sent: {mail_error}")


def _run(args: argparse.Namespace) -> int:
    """Processes the inbox, writes the updates and deletes the GOOD files (see `main`)

    Args:
        args (argparse.Namespace): parsed command line

    Returns:
        int: exit code 0
    """
    # 1. Process all files of the inbox
    file_counts, updates, good_files = process_heartbeat_files(heartbeat_files(data_dir=args.data_dir))

    # 2. Summary
    print(f"Heartbeat files in {args.data_dir}:")
    for (status, container), count in sorted(file_counts.items()):
        print(f"  {status:5} {container:13} {count}")
    process_counts: Counter = Counter((u.container.value, u.process.split("-")[0]) for u in updates)
    print(f"Updates, one per container_rawdata key: {len(updates)}")
    for (container, process), count in sorted(process_counts.items()):
        print(f"  {container:13} {process:12} {count}")

    # 3. Write and delete the GOOD files (dry run: nothing)
    if args.write is None:
        print("Dry run: nothing written, no file deleted")
        return 0
    load_from_enviroment(db_config_file=str(args.write))
    update_heartbeat_status(updates=updates)
    # Only after the commit: a failed write raises before any file is deleted
    for file_path in good_files:
        file_path.unlink(missing_ok=True)
    print(f"Written: {len(updates)} updates; deleted: {len(good_files)} GOOD files")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    """Batch entry point `container-status-importer`

    Args:
        argv (Optional[List[str]], optional): command line arguments. Defaults to None (sys.argv).

    Returns:
        int: exit code 0; a failed run raises (after the failure email with `--notify`) before any file is deleted
    """
    # 1. Arguments
    parser = argparse.ArgumentParser(description="Import NNE container heartbeat files into container_rawdata")
    parser.add_argument("--data-dir", type=Path, default=Path("/nne/data"), help="node data (default /nne/data)")
    parser.add_argument(
        "--write",
        type=Path,
        metavar="DB_ENV",
        help="write into container_rawdata with the database settings of this env file and delete the GOOD files "
        "(default: dry run, nothing written or deleted)",
    )
    parser.add_argument(
        "--notify",
        nargs="+",
        metavar="EMAIL",
        help="email addresses informed when the run fails (SMTP settings from the --write env file)",
    )
    args = parser.parse_args(argv)
    if args.notify and args.write is None:
        parser.error("--notify needs --write: the SMTP settings are read from its env file")

    # 2. Run, a failure is emailed and raised again
    started: datetime = datetime.now()
    try:
        return _run(args)
    except Exception as error:
        if args.notify:
            send_failure_email(env_file=args.write, recipients=args.notify, started=started, error=error)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
