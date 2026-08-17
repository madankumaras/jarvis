"""Test-suite-wide fixtures."""
import shutil
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def short_tmp_path():
    """Short-path tmp dir.

    macOS caps AF_UNIX sun_path at 104 bytes. Pytest's default tmp_path is
    nested under the (often deep) macOS TMPDIR -- e.g.
    /private/var/folders/.../pytest-of-<user>/pytest-<n>/<test-name>0/ --
    which routinely blows past that limit once a socket filename is
    appended, causing "OSError: AF_UNIX path too long" that has nothing to
    do with the code under test. Use a short, fresh directory under /tmp
    instead, and clean it up after each test so re-runs never collide with
    a leftover bound socket file.
    """
    path = Path(tempfile.mkdtemp(dir="/tmp"))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)
