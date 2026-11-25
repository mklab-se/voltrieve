"""The preprovision hook refuses to point everything at a Serverless service that does not exist
yet, and to leave out the Basic service while something still uses it. az and uv are stand-ins that record that they were called."""

import os
import subprocess
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[2] / "hooks" / "preprovision.sh"
SERVERLESS = "https://sl.search.windows.net"


def run_hook(tmp_path, **env):
    for name in ("az", "uv", "azd"):
        tool = tmp_path / name
        tool.write_text(f'#!/bin/sh\necho {name} "$@" >> "{tmp_path}/calls"\necho example.test\n')
        tool.chmod(0o755)
    base = {"PATH": f"{tmp_path}:/usr/bin:/bin", "HOME": os.environ["HOME"]}
    done = subprocess.run(["sh", str(HOOK)], env=base | env, cwd=HOOK.parents[1], capture_output=True, text=True)
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


@pytest.mark.parametrize("env", [{}, {"MCP_DOORS": "false", "API_HOST": "api.example.com", "MCP_ALLOW_CLI": "true"}])
def test_without_mcp_doors_nothing_about_them_runs(tmp_path, env):
    done, calls = run_hook(tmp_path, **env)
    assert done.returncode == 0, done.stderr
    assert "--mcp" not in calls and "--allow-cli" not in calls and "-host" not in calls
    assert "azd" not in calls


def test_mcp_doors_pass_their_flags(tmp_path):
    done, calls = run_hook(
        tmp_path, MCP_DOORS="true", MCP_ALLOW_CLI="true", API_HOST="api.example.com", DEALER_HOST="dealer.example.com")
    assert done.returncode == 0, done.stderr
    assert "--mcp --allow-cli --api-host api.example.com --dealer-host dealer.example.com" in calls


def test_mcp_doors_without_hosts_and_the_cli(tmp_path):
    done, calls = run_hook(tmp_path, MCP_DOORS="true", MCP_ALLOW_CLI="false", API_HOST="")
    assert done.returncode == 0, done.stderr
    (setup,) = [line for line in calls.splitlines() if line.startswith("uv ")]
    assert setup.endswith("--verified-domain example.test --mcp")


@pytest.mark.parametrize("env", [{"MCP_DOORS": "yes"}, {"MCP_DOORS": "true", "MCP_ALLOW_CLI": "1"}])
def test_another_value_for_the_doors_is_refused_before_anything_runs(tmp_path, env):
    done, calls = run_hook(tmp_path, **env)
    assert done.returncode != 0 and calls == "" and "MCP_DOORS" in done.stderr


def test_the_deployer_gets_a_city_at_the_dealer_door(tmp_path):
    done, calls = run_hook(tmp_path, MCP_DOORS="true")
    assert done.returncode == 0, done.stderr
    assert "azd env set DEALER_DOOR_CITIES example.test=Göteborg" in calls  # example.test: what az answers here


def test_a_list_of_cities_is_not_replaced(tmp_path):
    done, calls = run_hook(tmp_path, MCP_DOORS="true", DEALER_DOOR_CITIES="someone=Malmö")
    assert done.returncode == 0, done.stderr
    assert "azd" not in calls
