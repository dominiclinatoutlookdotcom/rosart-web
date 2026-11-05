import sys
from pathlib import Path

# The export tool lives in tools/ as plain modules, not an installed package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
