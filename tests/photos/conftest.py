import sys
from pathlib import Path

# The scripts share their client, and the tests import it as a script does.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
