"""The Rosart publisher service: a staff page that checks and publishes the site from the box.

It reuses `tools/` (export, publish, guard) in-process; see specs/006-rosart-publisher in sgs-server.
"""

import sys
from pathlib import Path

# tools/ holds plain modules (publish, shopify_admin, ...), not a package: the service imports them by name.
_TOOLS = str(Path(__file__).resolve().parent.parent / "tools")
if _TOOLS not in sys.path:
    sys.path.insert(0, _TOOLS)
