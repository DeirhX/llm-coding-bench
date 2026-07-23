from __future__ import annotations

from pkg.money import Money


def test_split_sums_exactly():
    for cents, parts in [(100, 3), (1, 3), (999, 7), (50, 1), (10, 4)]:
        m = Money(cents)
        chunks = m.split(parts)
        assert len(chunks) == parts
        assert sum(x.cents for x in chunks) == cents
        assert all(x.currency == "USD" for x in chunks)
        assert max(x.cents for x in chunks) - min(x.cents for x in chunks) <= 1


def test_split_rejects_bad_parts():
    try:
        Money(10).split(0)
        assert False, "expected ValueError"
    except ValueError:
        pass
