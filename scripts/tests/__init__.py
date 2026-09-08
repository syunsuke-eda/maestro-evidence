"""テストパッケージ。melib と support を import できるよう path を通す。"""

from __future__ import annotations

import sys
from pathlib import Path

for candidate in (Path(__file__).resolve().parent, Path(__file__).resolve().parents[1]):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))
