"""Measure the effective sleep granularity on the current Windows host."""

from __future__ import annotations

import argparse
import statistics
import time


def measure(samples: int, requested_s: float) -> tuple[float, float, float]:
    """Return minimum, median, and maximum observed sleep durations."""
    values: list[float] = []
    for _ in range(samples):
        started = time.perf_counter()
        time.sleep(requested_s)
        values.append(time.perf_counter() - started)
    return min(values), statistics.median(values), max(values)


def main() -> int:
    """Parse options and print a timing-granularity report."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", type=int, default=100)
    parser.add_argument("--requested-ms", type=float, default=1.0)
    args = parser.parse_args()
    minimum, median, maximum = measure(args.samples, args.requested_ms / 1000)
    print(
        f"samples={args.samples} requested_ms={args.requested_ms:.3f} "
        f"min_ms={minimum * 1000:.3f} "
        f"median_ms={median * 1000:.3f} "
        f"max_ms={maximum * 1000:.3f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
