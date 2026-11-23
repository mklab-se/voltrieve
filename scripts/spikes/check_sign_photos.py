"""Shows the car's photos of the signs to the photo handler of the API.

The photos at 50 km/h must be accepted and those at 90 km/h rejected as too_blurry.

    cd clients/car && npm run sign-photos
    uv run --project api python scripts/spikes/check_sign_photos.py clients/car/out
"""

import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).parents[2] / "api"))

from app.attachments.base import Rejected  # noqa: E402
from app.attachments.photo import PhotoHandler, sharpness  # noqa: E402


def outcome(path: Path) -> str:
    try:
        PhotoHandler().process(path.read_bytes(), "Parking sign")
    except Rejected as e:
        return e.code
    return "ready"


def main() -> int:
    counts: dict[str, list[str]] = {"50": [], "90": []}
    for path in sorted(Path(sys.argv[1]).glob("*.jpg")):
        speed = path.stem.rsplit("_", 1)[1]
        result = outcome(path)
        counts[speed].append(result)
        print(f"{path.name:32} {sharpness(Image.open(path)):7.0f}  {result}")
    ready, blurry = counts["50"].count("ready"), counts["90"].count("too_blurry")
    print(f"{ready} ready, {blurry} too_blurry")
    return 0 if (ready, blurry) == (8, 8) and len(counts["50"]) == len(counts["90"]) == 8 else 1


if __name__ == "__main__":
    raise SystemExit(main())
