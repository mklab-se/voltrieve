"""scripts/check-host.sh only prints and checks. dig and azd are stand-ins."""

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check-host.sh"
HOST = "api.volt.example.com"
TARGET = "ca-api.region.example.io"
GOOD = {f"CNAME {HOST}": f"{TARGET}.", f"TXT asuid.{HOST}": '"ABC123"'}


def run(tmp_path, records, *args):
    answers = "".join(f"'{question}') printf '%s\\n' '{answer}' ;;\n" for question, answer in records.items())
    (tmp_path / "dig").write_text(f'#!/bin/sh\nshift\ncase "$*" in\n{answers}esac\n')
    (tmp_path / "azd").write_text(
        f'#!/bin/sh\necho "$@" >> "{tmp_path}/calls"\n'
        f'case "$3" in HOST_VERIFICATION_ID) echo ABC123 ;; *_URL) echo https://{TARGET} ;; esac\n'
    )
    for name in ("dig", "azd"):
        (tmp_path / name).chmod(0o755)
    env = {"PATH": f"{tmp_path}:/usr/bin:/bin", "HOME": os.environ["HOME"]}
    done = subprocess.run(["sh", str(SCRIPT), *args], env=env, capture_output=True, text=True)
    calls = tmp_path / "calls"
    return done, calls.read_text().splitlines() if calls.exists() else []


@pytest.mark.parametrize("caa", [{}, {"CAA example.com": '0 issue "digicert.com"'}, {
    "CAA volt.example.com": '0 issue "digicert.com"', "CAA example.com": '0 issue "other.example"'}])
@pytest.mark.parametrize("service,setting", [("api", "API_HOST"), ("dealer", "DEALER_HOST")])
def test_every_record_is_there(tmp_path, caa, service, setting):
    done, calls = run(tmp_path, GOOD | caa, service, HOST)
    assert done.returncode == 0, done.stdout + done.stderr
    assert f"azd env set {setting} {HOST}" in done.stdout
    assert all(call.startswith("env get-value ") for call in calls), "it only reads"


@pytest.mark.parametrize("records,missing", [
    ({}, "CNAME"),
    (GOOD | {f"CNAME {HOST}": "proxy.example.net."}, "CNAME"),
    (GOOD | {f"TXT asuid.{HOST}": '"OTHER"'}, "TXT"),
    (GOOD | {"CAA example.com": '0 issue "other.example"'}, "CAA"),
])
def test_what_is_missing_is_said_with_the_records(tmp_path, records, missing):
    done, _ = run(tmp_path, records, "api", HOST)
    assert done.returncode == 1
    assert f"missing  {missing}" in done.stdout and "azd env set" not in done.stdout
    assert f"asuid.{HOST}  ABC123" in done.stdout and TARGET in done.stdout


@pytest.mark.parametrize("args", [
    (), ("api",), ("web", HOST), ("api", "https://api.example.com"), ("api", "API.example.com"), ("api", "example.com"),
])
def test_a_wrong_call_is_refused(tmp_path, args):
    done, _ = run(tmp_path, GOOD, *args)
    assert done.returncode == 2
