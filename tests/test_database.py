"""Write of the heartbeat importer into the local test database (docker/db.compose.yaml, 127.0.0.1:23307).

Needs the local database running and `.db_env_test` (git-ignored, see documentation/DEVELOPMENT.md). Only rows of the
test node 99999 are written; they are removed before and after the test.
"""

import json
import lzma
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import text

import nnedbtools.connection as con
import nnedbtools.tables as tb
from nnedbtools.enums import DockerContainer
from nnedbtools.identityprovider import load_from_enviroment

from container_status_importer.importer import HeartbeatUpdate, main, update_heartbeat_status

DB_ENV = Path(__file__).parents[1] / ".db_env_test"
TEST_NODE = 99999
SELECT = text("SELECT seen_ts, uptime, extra FROM container_rawdata WHERE node_id = :node_id")
SAMPLE = Path(__file__).parent / "data" / "7056" / "heartbeats" / "2026-11-06" / "heartbeat_mqtt_20261106T090000.json.xz"
DELETE = text("DELETE FROM container_rawdata WHERE node_id = :node_id")


@pytest.fixture
def report_engine():
    if not DB_ENV.is_file():
        pytest.skip(f"{DB_ENV.name} missing: no local test database configured")
    load_from_enviroment(db_config_file=str(DB_ENV))
    engine = con.engines.report_engine
    url = engine.url
    assert (url.host, url.port, url.database) == ("127.0.0.1", 23307, "report_test"), "local test database only"
    tb.ReportContainerRawdata.__table__.create(bind=engine, checkfirst=True)
    with engine.begin() as connection:
        connection.execute(DELETE, {"node_id": TEST_NODE})
    yield engine
    with engine.begin() as connection:
        connection.execute(DELETE, {"node_id": TEST_NODE})


def _update(seen_ts: datetime, uptime: float, extra: str) -> HeartbeatUpdate:
    return HeartbeatUpdate(
        node_id=TEST_NODE,
        start_ts=datetime(2026, 11, 6, 8, 0),
        container=DockerContainer.UDPPING,
        version="electro-2-2-5",
        process="receiver-10506",
        seen_ts=seen_ts,
        uptime=uptime,
        extra=extra,
    )


def test_upsert_keeps_one_row_per_key(report_engine):
    update_heartbeat_status([_update(datetime(2026, 11, 6, 9), 3600.0, "313741")])
    update_heartbeat_status([_update(datetime(2026, 11, 6, 10), 7200.0, "313742")])
    with report_engine.connect() as connection:
        rows = connection.execute(SELECT, {"node_id": TEST_NODE}).all()
    assert [tuple(row) for row in rows] == [(datetime(2026, 11, 6, 10), 7200.0, "313742")]


def test_write_deletes_good_files_only(report_engine, tmp_path):
    day = tmp_path / str(TEST_NODE) / "heartbeats" / "2026-11-06"
    day.mkdir(parents=True)
    record = json.loads(lzma.decompress(SAMPLE.read_bytes()))
    record["node_id"] = TEST_NODE
    good = day / SAMPLE.name
    good.write_bytes(lzma.compress(json.dumps(record).encode()))
    bad = day / "heartbeat_mqtt_20261106T100000.json.xz"
    bad.write_bytes(b"unfinished transfer")
    assert main(["--data-dir", str(tmp_path), "--write", str(DB_ENV)]) == 0
    assert not good.exists() and bad.exists()
    with report_engine.connect() as connection:
        assert len(connection.execute(SELECT, {"node_id": TEST_NODE}).all()) == 2
