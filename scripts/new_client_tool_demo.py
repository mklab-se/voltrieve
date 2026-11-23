# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx"]
# ///
"""A client declares a tool the server has never seen, as a car with a seat heater would.

    uv run scripts/new_client_tool_demo.py

Nothing about `set_seat_heating` exists in the API, the agents or Foundry: it is declared only here,
in the request. Backends loop and hosted must relay the call to this script and finish the turn.
Backend prompt must refuse it, since a prompt agent's tools are fixed in its definition.
No car is involved; the handler stands in for one. Writes docs/evidence/new-client-tool.txt.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from voltrieve_client import Api, api_token, api_url, cached_token, write_evidence

SET_SEAT_HEATING = {
    "name": "set_seat_heating",
    "description": "Set the heating of the driver's seat: 0 is off, 3 is the warmest.",
    "parameters": {
        "type": "object",
        "properties": {"level": {"type": "integer", "minimum": 0, "maximum": 3}},
        "required": ["level"],
        "additionalProperties": False,
    },
}
EVIDENCE = Path(__file__).parent.parent / "docs" / "evidence" / "new-client-tool.txt"


def main() -> int:
    token = cached_token(api_token)
    url = api_url()
    lines = []
    passed = True
    for backend in ("loop", "hosted", "prompt"):
        api = Api(url, token, backend)
        received: list[int] = []

        def car(arguments: str) -> str:
            received.append(json.loads(arguments)["level"])
            return f"Seat heating is now at level {received[-1]}."

        turn = api.turn("Turn my seat heating to level 2.",
                        client_tools=[SET_SEAT_HEATING], handlers={"set_seat_heating": car})
        api.close()
        if backend == "prompt":
            code = (turn.error or {}).get("code")
            ok = code == "unsupported_client_tool" and not received
            lines.append(f"{'PASS' if ok else 'FAIL'}  prompt  refused as expected, error: {code}")
        else:
            ok = received == [2] and turn.done is not None
            lines.append(f"{'PASS' if ok else 'FAIL'}  {backend}  tool received {received}, "
                         f"answer: {turn.text[:90]!r}")
        passed &= ok
        print(lines[-1], flush=True)
    write_evidence(EVIDENCE, "\n".join(lines) + "\n")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
