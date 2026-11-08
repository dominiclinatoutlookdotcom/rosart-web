import sys
from pathlib import Path

# The export tool lives in tools/ as plain modules, not an installed package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

# The shared fakes live in tests/publisher, a plain folder (not a package: `publisher` is the
# service package at the repo root). Putting it on sys.path lets any test `import fake_aws` etc.
sys.path.insert(0, str(Path(__file__).resolve().parent / "publisher"))
