"""The preprovision hook refuses to point everything at a Serverless service that does not exist
yet, and to leave out the Basic service while something still uses it. az and uv are stand-ins that record that they were called."""

import os
import subprocess
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[2] / "hooks" / "preprovision.sh"
SERVERLESS = "https://sl.search.windows.net"


def run_hook(tmp_path, **env):
    for name in ("az", "uv"):
        tool = tmp_path / name
        tool.write_text(f'#!/bin/sh\necho {name} "$@" >> "{tmp_path}/calls"\necho example.test\n')
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


@pytest.mark.parametrize("serverless", [{}, {"SEARCH_SERVERLESS": "off"}, {"SEARCH_SERVERLESS": "standby"}])
def test_no_basic_service_is_refused_before_anything_runs_unless_serverless_is_active(tmp_path, serverless):
    done, calls = run_hook(tmp_path, SEARCH_BASIC="off", SEARCH_SERVERLESS_ENDPOINT=SERVERLESS, **serverless)
    assert done.returncode != 0 and calls == ""
    assert "SEARCH_BASIC is off" in done.stderr and "SEARCH_SERVERLESS active" in done.stderr


def test_no_basic_service_and_no_serverless_service_is_refused(tmp_path):
    done, calls = run_hook(tmp_path, SEARCH_BASIC="off", SEARCH_SERVERLESS="active")
    assert done.returncode != 0 and calls == ""


def test_another_value_for_basic_is_refused(tmp_path):
    done, calls = run_hook(tmp_path, SEARCH_BASIC="Off", SEARCH_SERVERLESS="active", SEARCH_SERVERLESS_ENDPOINT=SERVERLESS)
    assert done.returncode != 0 and calls == "" and "SEARCH_BASIC" in done.stderr


@pytest.mark.parametrize("env", [
    {},
    {"SEARCH_BASIC": ""},
    {"SEARCH_BASIC": "on", "SEARCH_SERVERLESS": "standby"},
    {"SEARCH_BASIC": "on", "SEARCH_SERVERLESS": "active", "SEARCH_SERVERLESS_ENDPOINT": SERVERLESS},
    {"SEARCH_BASIC": "off", "SEARCH_SERVERLESS": "active", "SEARCH_SERVERLESS_ENDPOINT": SERVERLESS},
    {"SEARCH_SERVERLESS": "off"},
    {"SEARCH_SERVERLESS": "standby"},
    {"SEARCH_SERVERLESS": "active", "SEARCH_SERVERLESS_ENDPOINT": SERVERLESS},
])
def test_every_other_step_is_allowed(tmp_path, env):
    done, calls = run_hook(tmp_path, **env)
    assert done.returncode == 0, done.stderr
    assert "uv run" in calls


def test_a_known_dealer_url_is_passed_to_setup_and_otherwise_left_out(tmp_path):
    done, calls = run_hook(tmp_path, DEALER_URL="https://dealer.example")
    assert done.returncode == 0, done.stderr
    assert '--dealer-redirect-uri https://dealer.example' in calls
    other = tmp_path / "other"
    other.mkdir()
    done, calls = run_hook(other)
    assert done.returncode == 0, done.stderr
    assert "--dealer-redirect-uri" not in calls
