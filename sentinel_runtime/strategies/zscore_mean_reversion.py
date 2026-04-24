from __future__ import annotations

import logging
import os
from dataclasses import dataclass, replace
from decimal import Decimal

import numpy as np
import pandas as pd

from ..errors import ConfigError
from ..models import SignalDecision


@dataclass(frozen=True)
class ZscoreMeanReversionParams:
    zscore_window: int = 48
    zscore_entry_long: float = -2.1
    zscore_entry_short: float = 2.1
    rsi_window: int = 14
    rsi_long_max: float = 32.0
    rsi_short_min: float = 68.0
    atr_window: int = 14
    atr_pct_min: float = 0.0025
    atr_pct_max: float = 0.0180
    volume_zscore_window: int = 20
    volume_zscore_min: float = -0.5
    history_safety_buffer: int = 5

    @property
    def minimum_history(self) -> int:
        return (
            max(
                self.zscore_window,
                self.volume_zscore_window,
                self.rsi_window + 1,
                self.atr_window + 1,
            )
            + self.history_safety_buffer
        )


# ---------------------------------------------------------------------------
# Opt-in demo-tuning profiles and env overrides.
#
# The default profile is the spec-accurate `ZscoreMeanReversionParams()` above
# and is NOT changed. `demo_relaxed` is a strictly opt-in preset that loosens
# the entry gates for controlled demo runs on a quiet exchange regime (Bybit
# testnet/demo ETHUSDT 5m), where the default thresholds rarely fire within a
# short demo window. It only affects *entry* — TP/SL, risk limits, dry-run
# gating, reconciliation, and notifications all remain untouched.
# ---------------------------------------------------------------------------

DEMO_RELAXED_PARAMS = ZscoreMeanReversionParams(
    zscore_entry_long=-1.8,
    zscore_entry_short=1.8,
    rsi_long_max=40.0,
    rsi_short_min=60.0,
    atr_pct_min=0.0010,
    atr_pct_max=0.0250,
    volume_zscore_min=-1.0,
)

_PROFILES: dict[str, ZscoreMeanReversionParams] = {
    "default": ZscoreMeanReversionParams(),
    "demo_relaxed": DEMO_RELAXED_PARAMS,
}


# env var name -> (param field, parser)
_ENV_OVERRIDES: dict[str, tuple[str, type]] = {
    "ZSCORE_ENTRY_LONG": ("zscore_entry_long", float),
    "ZSCORE_ENTRY_SHORT": ("zscore_entry_short", float),
    "ZSCORE_RSI_LONG_MAX": ("rsi_long_max", float),
    "ZSCORE_RSI_SHORT_MIN": ("rsi_short_min", float),
    "ZSCORE_ATR_PCT_MIN": ("atr_pct_min", float),
    "ZSCORE_ATR_PCT_MAX": ("atr_pct_max", float),
    "ZSCORE_VOLUME_MIN": ("volume_zscore_min", float),
}


def params_from_env() -> ZscoreMeanReversionParams:
    """Build params from env vars.

    Two-tier resolution:
      1. `ZSCORE_PROFILE` picks a base preset. Defaults to `default` (spec values).
         Valid: `default`, `demo_relaxed`.
      2. Individual overrides (see `_ENV_OVERRIDES`) mutate the base on a
         per-field basis. Absent env vars leave the base unchanged.

    Unknown profile names raise `ConfigError` so typos fail loudly instead of
    silently selecting `default`.
    """
    profile_name = os.environ.get("ZSCORE_PROFILE", "default").strip().lower() or "default"
    base = _PROFILES.get(profile_name)
    if base is None:
        valid = ", ".join(sorted(_PROFILES))
        raise ConfigError(
            f"Unknown ZSCORE_PROFILE value: {profile_name!r}. Valid: {valid}."
        )

    overrides: dict[str, float | int] = {}
    for env_name, (field_name, parser) in _ENV_OVERRIDES.items():
        raw = os.environ.get(env_name)
        if raw is None or raw.strip() == "":
            continue
        try:
            overrides[field_name] = parser(raw)
        except ValueError as exc:
            raise ConfigError(
                f"Invalid value for {env_name}: {raw!r} (expected {parser.__name__})."
            ) from exc

    return replace(base, **overrides) if overrides else base


def compute_rolling_zscore(closes: np.ndarray, window: int) -> float:
    """Population z-score of the last close vs. the most recent `window` closes."""
    if len(closes) < window:
        return float("nan")
    recent = np.asarray(closes[-window:], dtype=float)
    mu = float(recent.mean())
    sigma = float(recent.std(ddof=0))
    if sigma == 0.0:
        return 0.0
    return (float(closes[-1]) - mu) / sigma


def compute_rsi(closes: np.ndarray, window: int = 14) -> float:
    """Simple moving-average RSI over the last `window` close deltas."""
    if len(closes) < window + 1:
        return float("nan")
    deltas = np.diff(np.asarray(closes[-(window + 1) :], dtype=float))
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = float(gains.mean())
    avg_loss = float(losses.mean())
    if avg_loss == 0.0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def compute_atr(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    window: int = 14,
) -> float:
    """Simple moving-average ATR over the last `window` true ranges."""
    if len(closes) < window + 1:
        return float("nan")
    h = np.asarray(highs[-window:], dtype=float)
    l = np.asarray(lows[-window:], dtype=float)
    c_prev = np.asarray(closes[-(window + 1) : -1], dtype=float)
    tr = np.maximum.reduce([h - l, np.abs(h - c_prev), np.abs(l - c_prev)])
    return float(tr.mean())


def compute_volume_zscore(vols: np.ndarray, window: int = 20) -> float:
    """Population z-score of the last volume vs. the most recent `window` volumes."""
    if len(vols) < window:
        return float("nan")
    recent = np.asarray(vols[-window:], dtype=float)
    mu = float(recent.mean())
    sigma = float(recent.std(ddof=0))
    if sigma == 0.0:
        return 0.0
    return (float(vols[-1]) - mu) / sigma


class ZscoreMeanReversionEngine:
    """
    Deterministic rule-based mean-reversion strategy (v1).

    Interface parity with ModelSignalEngine: `.evaluate(candles) -> SignalDecision`.
    No XGBoost model is loaded — rules fire on z-score, RSI, ATR-%, and volume z-score.
    The `long_probability` / `short_probability` fields on the returned SignalDecision
    are set to 1.0 for the chosen side (and 0.0 elsewhere) so the existing logging,
    notifications, and SQLite schema keep working without changes.
    """

    STRATEGY_NAME = "zscore_mean_reversion_v1"
    REQUIRED_COLUMNS = ("ts", "open", "high", "low", "close", "vol")

    def __init__(self, params: ZscoreMeanReversionParams | None = None) -> None:
        self._params = params or ZscoreMeanReversionParams()
        self._logger = logging.getLogger(self.__class__.__name__)

    def evaluate(self, candles: pd.DataFrame) -> SignalDecision:
        missing = [c for c in self.REQUIRED_COLUMNS if c not in candles.columns]
        if missing:
            raise ValueError(
                f"ZscoreMeanReversionEngine missing candle columns: {missing}."
            )
        if len(candles) == 0:
            raise ValueError("ZscoreMeanReversionEngine received an empty candle frame.")

        last_close = float(candles["close"].iloc[-1])
        last_ts = pd.Timestamp(candles["ts"].iloc[-1]).to_pydatetime()
        market_price = Decimal(str(last_close))

        def _no_action(reason: str) -> SignalDecision:
            self._logger.info(
                "Strategy=%s candle=%s close=%.4f skipped=%s",
                self.STRATEGY_NAME,
                last_ts.isoformat(),
                last_close,
                reason,
            )
            return SignalDecision(
                candle_open_time=last_ts,
                long_probability=0.0,
                short_probability=0.0,
                market_price=market_price,
                action=None,
            )

        if len(candles) < self._params.minimum_history:
            return _no_action(
                f"insufficient_history have={len(candles)} need={self._params.minimum_history}"
            )

        closes = candles["close"].to_numpy(dtype=float)
        highs = candles["high"].to_numpy(dtype=float)
        lows = candles["low"].to_numpy(dtype=float)
        vols = candles["vol"].to_numpy(dtype=float)

        z = compute_rolling_zscore(closes, self._params.zscore_window)
        rsi = compute_rsi(closes, self._params.rsi_window)
        atr = compute_atr(highs, lows, closes, self._params.atr_window)
        atr_pct = atr / last_close if last_close != 0.0 else float("nan")
        vol_z = compute_volume_zscore(vols, self._params.volume_zscore_window)

        if any(not np.isfinite(v) for v in (z, rsi, atr, atr_pct, vol_z)):
            return _no_action("nan_indicator")

        atr_in_band = self._params.atr_pct_min <= atr_pct <= self._params.atr_pct_max
        volume_ok = vol_z >= self._params.volume_zscore_min

        action = None
        if (
            z <= self._params.zscore_entry_long
            and rsi <= self._params.rsi_long_max
            and atr_in_band
            and volume_ok
        ):
            action = "Buy"
        elif (
            z >= self._params.zscore_entry_short
            and rsi >= self._params.rsi_short_min
            and atr_in_band
            and volume_ok
        ):
            action = "Sell"

        self._logger.info(
            "Strategy=%s candle=%s close=%.4f z=%.3f rsi=%.2f atr_pct=%.5f vol_z=%.3f "
            "atr_in_band=%s volume_ok=%s action=%s",
            self.STRATEGY_NAME,
            last_ts.isoformat(),
            last_close,
            z,
            rsi,
            atr_pct,
            vol_z,
            atr_in_band,
            volume_ok,
            action,
        )

        return SignalDecision(
            candle_open_time=last_ts,
            long_probability=1.0 if action == "Buy" else 0.0,
            short_probability=1.0 if action == "Sell" else 0.0,
            market_price=market_price,
            action=action,
        )
