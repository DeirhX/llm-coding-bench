"""Suite config knobs."""

from __future__ import annotations

import os

FIRST_BYTE_S = float(os.environ.get("MH_FIRST_BYTE_S", "600"))
STREAM_STALL_S = float(os.environ.get("MH_STREAM_STALL_S", "180"))
DEFAULT_WARMUP_TIMEOUT_S = float(os.environ.get("MH_WARMUP_TIMEOUT_S", "30"))
TASK_TIMEOUT_S = float(os.environ.get("MH_TASK_TIMEOUT_S", "600"))

# Evidence bonus for claim selftests (must stay >= 0).
EVIDENCE_BONUS_MAX = 3
