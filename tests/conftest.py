from __future__ import annotations

import sys
import os
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]


# Root-invoked pytest should always exercise the local backend package first,
# even when a different bloobcat build is installed in site-packages.
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# Unit tests must not depend on or leak the operator's local/prod-like .env.
# Force safe example defaults before application modules import settings.py;
# settings.py's own load_dotenv() keeps these values because override=False.
load_dotenv(PROJECT_ROOT / ".env.example", override=True)
os.environ["REMNAWAVE_LTE_NODE_MARKER"] = ""
