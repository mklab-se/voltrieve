# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx"]
#
# [tool.uv]
# exclude-newer = "2026-11-17T00:00:00Z"
# ///
"""Does an open page keep the dealer service awake?

    uv run scripts/dealer_awake.py --minutes 12 --read-seconds 60
    uv run scripts/dealer_awake.py --minutes 12 --read-seconds 0        only the stream
    ... [--out docs/evidence/dealer-awake.txt] [--app NAME]

Holds /staff/events open as the page does, and reads /staff/state every so many seconds (0 for never). It
prints every lost connection with the seconds of silence before it, the reads that failed, and the replicas
with their start times before and after. The same replica with the same start time: it stayed awake.
Exit code 0 when it did and nothing was lost, 1 otherwise.
No name, address or token is printed. With --out the report is written, otherwise nothing is.
Needs az, signed in as a user with Dealer.Manage, and AZURE_RESOURCE_GROUP. DEALER_URL and DEALER_CLIENT_ID come
from the environment or from azd.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import datetime as dt
import json
import os
import subprocess
import time
from pathlib import Path

import httpx
from voltrieve_client import azd_value, write_evidence

SILENCE = 45.0  # seconds without a byte: the connection is dead. The page waits as long.
LIFETIME = 30 * 60.0  # the service ends a stream by itself after this long
BACK = (1.0, 2.0, 5.0, 10.0)  # the waits of the page before it connects again


def az(*arguments: str) -> str:
    result = subprocess.run(["az", *arguments], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise SystemExit(f"az {arguments[0]} {arguments[1]} failed. Run `az login` as a user with Dealer.Manage.")
    return result.stdout


def token(client_id: str) -> str:
    scope = f"api://{client_id}/manage_as_user"
    return az("account", "get-access-token", "--scope", scope, "--query", "accessToken", "-o", "tsv").strip()


def has_name(jwt: str) -> bool:
    part = jwt.split(".")[1]
    return bool(json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))).get("name"))


def replicas(group: str, app: str) -> list[tuple[str, str]]:
    query = "[].[name, properties.createdTime]"
    found = json.loads(az("containerapp", "replica", "list", "-g", group, "-n", app, "--query", query, "-o", "json"))
    return sorted((name, created) for name, created in found)


def wake(url: str) -> None:
    """It may be at zero, and then there is no replica to compare with."""
    for _ in range(30):
        try:
            if httpx.get(f"{url}/healthz", timeout=30).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(2)
    raise SystemExit("The dealer service does not answer.")


class Report:
    def __init__(self) -> None:
        self.lines: list[str] = []
        self.lost = 0
        self.planned = 0
        self.failed_reads = 0
        self.reads = 0
        self.longest = 0.0

    def say(self, text: str) -> None:
        line = f"{dt.datetime.now(dt.UTC):%H:%M:%S} {text}"
        self.lines.append(line)
        print(line, flush=True)

    def show(self, when: str, found: list[tuple[str, str]]) -> None:
        self.say(f"replicas {when}: " + ("; ".join(f"{n} started {c}" for n, c in found) or "none"))


async def hold(http: httpx.AsyncClient, url: str, client_id: str, report: Report) -> None:
    """The stream, and a new one each time that it ends."""
    tries = 0
    while True:
        opened = last = time.monotonic()
        try:
            headers = {"Authorization": f"Bearer {await asyncio.to_thread(token, client_id)}"}
            opened = last = time.monotonic()
            async with http.stream("GET", f"{url}/staff/events", headers=headers, timeout=SILENCE) as response:
                if response.status_code != 200:
                    raise RuntimeError(f"status {response.status_code}")
                report.say("stream open")
                tries = 0
                async for _ in response.aiter_bytes():
                    report.longest = max(report.longest, time.monotonic() - last)
                    last = time.monotonic()
            why = "ended by the service"
        except (httpx.HTTPError, RuntimeError) as exc:
            why = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
        now = time.monotonic()
        report.longest = max(report.longest, now - last)
        if why == "ended by the service" and now - opened >= LIFETIME - 20:
            report.planned += 1
            report.say("stream ended after its 30 minutes, as it should")
        else:
            report.lost += 1
            report.say(f"stream lost after {now - opened:.0f} s: {why}, silent for {now - last:.0f} s")
        await asyncio.sleep(BACK[min(tries, len(BACK) - 1)])
        tries += 1


async def read(http: httpx.AsyncClient, url: str, client_id: str, seconds: float, report: Report) -> None:
    while seconds > 0:
        started = time.monotonic()
        headers = {"Authorization": f"Bearer {await asyncio.to_thread(token, client_id)}"}
        try:
            response = await http.get(f"{url}/staff/state", headers=headers, timeout=30)
            problem = "" if response.status_code == 200 else f"status {response.status_code}"
        except httpx.HTTPError as exc:
            problem = type(exc).__name__
        report.reads += 1
        if problem:
            report.failed_reads += 1
            report.say(f"read failed: {problem}")
        await asyncio.sleep(max(0.0, seconds - (time.monotonic() - started)))


async def run(url: str, client_id: str, minutes: float, seconds: float, report: Report) -> None:
    async with httpx.AsyncClient() as http:
        tasks = [
            asyncio.create_task(hold(http, url, client_id, report)),
            asyncio.create_task(read(http, url, client_id, seconds, report)),
        ]
        # The reader ends at once when there are no reads. A task that fails ends the run.
        end = time.monotonic() + minutes * 60
        while (left := end - time.monotonic()) > 0:
            done, _ = await asyncio.wait(tasks, timeout=min(left, 1.0), return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
            tasks = [task for task in tasks if not task.done()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--minutes", type=float, default=12.0)
    parser.add_argument("--read-seconds", type=float, default=60.0, help="0 for never")
    parser.add_argument("--app", default="ca-dealer-voltrieve-dev", help="the container app")
    parser.add_argument("--out", type=Path, help="where to write the report")
    args = parser.parse_args()

    group = os.environ.get("AZURE_RESOURCE_GROUP") or azd_value("AZURE_RESOURCE_GROUP")
    url = (os.environ.get("DEALER_URL") or azd_value("DEALER_URL")).rstrip("/")
    client_id = os.environ.get("DEALER_CLIENT_ID") or azd_value("DEALER_CLIENT_ID")

    report = Report()
    reads = f"a read every {args.read_seconds:g} s" if args.read_seconds > 0 else "no reads"
    report.say(f"{dt.date.today()}: the stream for {args.minutes:g} minutes, {reads}")
    report.say(f"the token has a name claim: {'yes' if has_name(token(client_id)) else 'no'}")
    wake(url)
    before = replicas(group, args.app)
    report.show("before", before)
    asyncio.run(run(url, client_id, args.minutes, args.read_seconds, report))
    after = replicas(group, args.app)
    report.show("after", after)

    awake = bool(before) and before == after
    report.say(f"longest silence {report.longest:.0f} s, connections lost {report.lost}, ended on time {report.planned}")
    report.say(f"reads {report.reads}, failed {report.failed_reads}")
    report.say("the same replicas before and after: " + ("yes" if awake else "no"))
    if args.out:
        write_evidence(args.out, "\n".join(report.lines) + "\n")
    raise SystemExit(0 if awake and not report.lost and not report.failed_reads else 1)


if __name__ == "__main__":
    main()
