#!/usr/bin/env python3
"""Report suspicious spending rows, and apply opt-in repairs.

Run from the repo root, locally or in the backend pod:

  python scripts/check-spending-data.py
  python scripts/check-spending-data.py --user-id 1
  python scripts/check-spending-data.py --recategorize-payments
  python scripts/check-spending-data.py --delete-batch-merges --apply \\
      --i-have-a-backup

``DATABASE_URL`` is required. Repairs do nothing unless ``--apply`` is set,
and ``--apply`` is refused without ``--i-have-a-backup``.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from backend.check_spending_data import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
