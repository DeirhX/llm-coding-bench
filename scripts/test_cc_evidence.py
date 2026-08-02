#!/usr/bin/env python3
"""What counts as having read the lines you cite.

The interesting cases are all about several reads rather than one. The guard caps a read at 500
lines, so any citation near a chunk boundary was read by two calls, and a coverage check that
insists on one call refuses a citation the model is looking straight at. Refusing a correct answer
is the failure this whole mechanism cannot afford, so these are the cases that get tests.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cc_evidence  # noqa: E402


def ranges(*spans):
    return {"src/m.py": list(spans)}


def test_one_read_that_spans_it() -> None:
    assert cc_evidence.covers(ranges((1, 500)), "src/m.py", 10, 20)


def test_two_adjacent_reads_that_straddle_it() -> None:
    assert cc_evidence.covers(ranges((1, 500), (501, 1000)), "src/m.py", 480, 520), \
        "a citation across a chunk boundary was read in full"


def test_two_overlapping_reads() -> None:
    assert cc_evidence.covers(ranges((1, 500), (400, 900)), "src/m.py", 450, 700)


def test_a_hole_between_the_reads_is_not_covered() -> None:
    assert not cc_evidence.covers(ranges((1, 100), (600, 900)), "src/m.py", 90, 620)


def test_reads_that_end_before_the_citation() -> None:
    assert not cc_evidence.covers(ranges((1, 100)), "src/m.py", 500, 520)


def test_reads_that_start_after_the_citation() -> None:
    assert not cc_evidence.covers(ranges((600, 900)), "src/m.py", 500, 520)


def test_unordered_reads_still_join_up() -> None:
    assert cc_evidence.covers(ranges((501, 1000), (1, 500)), "src/m.py", 480, 520), \
        "reads arrive in the order the model made them, not in line order"


def test_a_file_never_read() -> None:
    assert not cc_evidence.covers(ranges((1, 500)), "src/other.py", 1, 2)


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
