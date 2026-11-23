"""The preprovision hook refuses to point everything at a Serverless service that does not exist
yet. az and uv are stand-ins that record that they were called."""

import os
import subprocess
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[2] / "hooks" / "preprovision.sh"


def run_hook(tmp_path, **env):
    for name in ("az", "uv"):
        tool = tmp_path / name
        tool.write_text(f'#!/bin/sh\necho {name} >> "{tmp_path}/calls"\necho example.test\n')
        tool.chmod(0o755)
    base = {"PATH": f"{tmp_path}:/usr/bin:/bin", "HOME": os.environ["HOME"]}
    done = subprocess.run(["sh", str(HOOK)], env=base | env, capture_output=True, text=True)
    calls = tmp_path / "calls"
    return done, calls.read_text() if calls.exists() else ""


@pytest.mark.parametrize("endpoint", [{}, {"SEARCH_SERVERLESS_ENDPOINT": ""}])
def test_active_without_a_serverless_service_is_refused_before_anything_runs(tmp_path, endpoint):
    done, calls = run_hook(tmp_path, SEARCH_SERVERLESS="active", **endpoint)
    assert done.returncode != 0 and calls == ""
    assert "standby" in done.stderr


@pytest.mark.parametrize("env", [
    {},
    {"SEARCH_SERVERLESS": "off"},
    {"SEARCH_SERVERLESS": "standby"},
    {"SEARCH_SERVERLESS": "active", "SEARCH_SERVERLESS_ENDPOINT": "https://sl.search.windows.net"},
])
def test_every_other_step_is_allowed(tmp_path, env):
    done, calls = run_hook(tmp_path, **env)
    assert done.returncode == 0, done.stderr
    assert "uv" in calls
