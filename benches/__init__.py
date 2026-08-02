"""Benchmark phases — one package per suite; register new ones in ``registry``."""

from benches.registry import BENCH_ALIASES, BENCHES, BenchMetadata, get_bench, list_benches, normalize_bench_id

__all__ = [
    "BENCH_ALIASES",
    "BENCHES",
    "BenchMetadata",
    "get_bench",
    "list_benches",
    "normalize_bench_id",
]
