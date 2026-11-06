"""Logging of the command line: the whole log of a run, nnedbtools included, goes to --log-dir.

Runs the batch in a new Python process: only there nnedbtools is imported after the logging is set up."""

import os
import subprocess
import sys


def test_log_dir_receives_library_log(tmp_path):
    (tmp_path / "inbox").mkdir()
    completed = subprocess.run(
        [sys.executable, "-m", "container_status_importer.cli", "--data-dir", str(tmp_path / "inbox"),
         "--log-dir", str(tmp_path)],
        cwd=tmp_path, env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)}, capture_output=True, text=True,
    )
    assert completed.returncode == 0, completed.stderr
    log = (tmp_path / "importer.log").read_text()
    assert "-- Run started" in log and "-- Run finished" in log
    assert "CONNECTION: nnedbtools.connection.connect" in log
    assert not list(tmp_path.glob("logs/*.log"))
