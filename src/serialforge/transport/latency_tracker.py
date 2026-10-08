"""EWMA latency and adaptive read interval tracking."""

from __future__ import annotations

from serialforge.advanced import HandlerStats, ReadLoopConfig


class LatencyTracker:
    """Track uncontended round trips and derive a bounded poll interval."""

    _config: ReadLoopConfig
    _sample_count: int
    _srtt_s: float | None
    _rttvar_s: float | None
    _rto_s: float | None

    def __init__(self, config: ReadLoopConfig | None = None) -> None:
        """Create a cold-start tracker."""
        self._config = config or ReadLoopConfig()
        self._sample_count = 0
        self._srtt_s = None
        self._rttvar_s = None
        self._rto_s = None

    def observe(self, latency_s: float) -> None:
        """Record one positive uncontended round-trip sample."""
        if latency_s <= 0:
            raise ValueError("latency_s must be positive")
        if self._srtt_s is None or self._rttvar_s is None:
            self._srtt_s = latency_s
            self._rttvar_s = latency_s / 2
        else:
            self._rttvar_s = 0.75 * self._rttvar_s + 0.25 * abs(
                self._srtt_s - latency_s
            )
            self._srtt_s = 0.875 * self._srtt_s + 0.125 * latency_s
        self._rto_s = self._srtt_s + 4 * self._rttvar_s
        self._sample_count += 1

    def reset(self) -> None:
        """Forget all samples after reconnect or device changes."""
        self._sample_count = 0
        self._srtt_s = None
        self._rttvar_s = None
        self._rto_s = None

    def poll_interval_s(self) -> float:
        """Return the cold-start or adaptive bounded read interval."""
        if self._sample_count < self._config.min_samples:
            return self._config.poll_initial_s
        assert self._srtt_s is not None
        value = self._srtt_s * self._config.poll_factor
        return min(self._config.poll_max_s, max(self._config.poll_min_s, value))

    def stats(self) -> HandlerStats:
        """Return a frozen snapshot for the public diagnostics API."""
        return HandlerStats(
            srtt_s=self._srtt_s,
            rttvar_s=self._rttvar_s,
            poll_interval_s=self.poll_interval_s(),
            rto_s=self._rto_s,
        )


__all__ = ["LatencyTracker"]
