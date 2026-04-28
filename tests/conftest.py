from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]


# Root-invoked pytest should always exercise the local backend package first,
# even when a different bloobcat build is installed in site-packages.
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# Mirror the backend workdir behavior so settings-dependent imports see the
# project .env during collection when tests run from the repository root.
load_dotenv(PROJECT_ROOT / ".env")
