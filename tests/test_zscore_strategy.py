from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sentinel_runtime.config import StrategyMode
from sentinel_runtime.models import SignalDecision
from sentinel_runtime.strategies.zscore_mean_reversion import (
    ZscoreMeanReversionEngine,
    ZscoreMeanReversionParams,
    compute_atr,
    compute_rolling_zscore,
    compute_rsi,
    compute_volume_zscore,
)


# ---------------------------------------------------------------------------
# Pure math helpers
# ---------------------------------------------------------------------------


def test_rolling_zscore_returns_nan_when_history_too_short():
    closes = np.array([100.0, 101.0, 102.0], dtype=float)
    assert np.isnan(compute_rolling_zscore(closes, window=10))


def test_rolling_zscore_returns_zero_when_variance_is_zero():
    closes = np.array([100.0] * 10, dtype=float)
    assert compute_rolling_zscore(closes, window=5) == 0.0


def test_rolling_zscore_negative_when_last_close_below_mean():
    closes = np.concatenate([np.full(9, 100.0), np.array([90.0])])
    z = compute_rolling_zscore(closes, window=10)
    assert z < 0.0


def test_rolling_zscore_positive_when_last_close_above_mean():
    closes = np.concatenate([np.full(9, 100.0), np.array([110.0])])
    z = compute_rolling_zscore(closes, window=10)
    assert z > 0.0


def test_rsi_is_100_when_all_gains():
    closes = np.array([100.0 + i for i in range(20)], dtype=float)
    assert compute_rsi(closes, window=14) == 100.0


def test_rsi_is_zero_when_all_losses():
    closes = np.array([200.0 - i for i in range(20)], dtype=float)
    # avg_gain == 0 → RS = 0 / avg_loss = 0 → RSI = 100 - 100/(1+0) = 0
    assert compute_rsi(closes, window=14) == 0.0


def test_rsi_nan_when_history_too_short():
    closes = np.array([100.0, 101.0], dtype=float)
    assert np.isnan(compute_rsi(closes, window=14))


def test_atr_matches_mean_of_high_minus_low_for_flat_closes():
    # closes are constant so |high - close_prev| == high - close and |low - close_prev| == close - low
    # TR = max(high-low, high-close, close-low). When high-low >= both others, TR = high - low.
    highs = np.array([10.0] * 20, dtype=float)
    lows = np.array([5.0] * 20, dtype=float)
    closes = np.array([7.5] * 20, dtype=float)
    atr = compute_atr(highs, lows, closes, window=14)
    assert atr == pytest.approx(5.0)


def test_atr_nan_when_history_too_short():
    highs = np.array([10.0, 11.0], dtype=float)
    lows = np.array([9.0, 10.0], dtype=float)
    closes = np.array([9.5, 10.5], dtype=float)
    assert np.isnan(compute_atr(highs, lows, closes, window=14))


def test_volume_zscore_handles_zero_variance():
    vols = np.array([100.0] * 20, dtype=float)
    assert compute_volume_zscore(vols, window=20) == 0.0


def test_volume_zscore_nan_when_history_too_short():
    vols = np.array([100.0, 200.0], dtype=float)
    assert np.isnan(compute_volume_zscore(vols, window=20))


# ---------------------------------------------------------------------------
# Engine integration
# ---------------------------------------------------------------------------


def _make_flat_frame(count: int, base_close: float = 100.0) -> pd.DataFrame:
    """Build a flat synthetic candle frame — useful as a baseline to mutate."""
    ts = pd.date_range("2026-01-01T00:00:00Z", periods=count, freq="5min")
    return pd.DataFrame(
        {
            "ts": ts,
            "open": [base_close] * count,
            "high": [base_close + 0.5] * count,
            "low": [base_close - 0.5] * count,
            "close": [base_close] * count,
            "vol": [1000.0] * count,
            "turnover": [base_close * 1000.0] * count,
        }
    )


def _tighten_high_low(frame: pd.DataFrame, wick: float = 0.05) -> pd.DataFrame:
    """Keep highs/lows tight around closes so ATR tracks close moves, not wicks."""
    frame = frame.copy()
    frame["high"] = frame["close"] + wick
    frame["low"] = frame["close"] - wick
    return frame


def test_engine_returns_no_action_when_history_too_short():
    engine = ZscoreMeanReversionEngine()
    frame = _make_flat_frame(count=10)

    decision = engine.evaluate(frame)

    assert isinstance(decision, SignalDecision)
    assert decision.action is None
    assert decision.long_probability == 0.0
    assert decision.short_probability == 0.0
    assert decision.market_price == Decimal("100.0")


def test_engine_raises_on_missing_columns():
    engine = ZscoreMeanReversionEngine()
    frame = pd.DataFrame({"ts": pd.date_range("2026-01-01", periods=3, freq="5min"), "close": [1.0, 2.0, 3.0]})

    with pytest.raises(ValueError, match="missing candle columns"):
        engine.evaluate(frame)


def _build_long_scenario(params: ZscoreMeanReversionParams) -> pd.DataFrame:
    """Flat history at 100, then last 5 bars decline by 1 per bar to 95.

    This produces:
      - RSI(14) == 0 (five losses, zero gains in the last 14 deltas)
      - z-score(48) deeply negative (last close well below rolling mean)
      - ATR(14) small — high/low are tight to closes
      - volume z-score positive (spike on the last bar)
    """
    n = params.minimum_history + 5
    frame = _make_flat_frame(count=n, base_close=100.0)

    closes = frame["close"].to_numpy().copy()
    for i, value in enumerate([99.0, 98.0, 97.0, 96.0, 95.0]):
        closes[n - 5 + i] = value
    frame["close"] = closes

    frame = _tighten_high_low(frame, wick=0.05)

    vols = frame["vol"].to_numpy().copy()
    vols[-1] = 1500.0
    frame["vol"] = vols
    return frame


def _build_short_scenario(params: ZscoreMeanReversionParams) -> pd.DataFrame:
    """Mirror of the long scenario — last 5 bars ascend from 101 to 105."""
    n = params.minimum_history + 5
    frame = _make_flat_frame(count=n, base_close=100.0)

    closes = frame["close"].to_numpy().copy()
    for i, value in enumerate([101.0, 102.0, 103.0, 104.0, 105.0]):
        closes[n - 5 + i] = value
    frame["close"] = closes

    frame = _tighten_high_low(frame, wick=0.05)

    vols = frame["vol"].to_numpy().copy()
    vols[-1] = 1500.0
    frame["vol"] = vols
    return frame


def test_engine_emits_long_on_deep_negative_zscore_and_oversold_rsi():
    params = ZscoreMeanReversionParams()
    frame = _build_long_scenario(params)

    decision = ZscoreMeanReversionEngine(params).evaluate(frame)

    assert decision.action == "Buy"
    assert decision.long_probability == 1.0
    assert decision.short_probability == 0.0
    assert decision.market_price == Decimal(str(float(frame["close"].iloc[-1])))


def test_engine_emits_short_on_deep_positive_zscore_and_overbought_rsi():
    params = ZscoreMeanReversionParams()
    frame = _build_short_scenario(params)

    decision = ZscoreMeanReversionEngine(params).evaluate(frame)

    assert decision.action == "Sell"
    assert decision.short_probability == 1.0
    assert decision.long_probability == 0.0


def test_engine_no_action_when_atr_pct_outside_band():
    params = ZscoreMeanReversionParams()
    frame = _build_long_scenario(params)

    # Widen highs/lows so ATR/close blows past the 0.018 upper band.
    close_ref = float(frame["close"].iloc[-1])
    half_range = 0.05 * close_ref  # ATR ≈ 10% of close — well above band
    frame = frame.copy()
    frame["high"] = frame["close"] + half_range
    frame["low"] = frame["close"] - half_range

    decision = ZscoreMeanReversionEngine(params).evaluate(frame)
    assert decision.action is None


def test_engine_no_action_when_volume_zscore_too_negative():
    params = ZscoreMeanReversionParams()
    frame = _build_long_scenario(params)

    # Volume collapse on the last bar pushes the volume z-score below the floor.
    vols = frame["vol"].to_numpy().copy()
    vols[-20:] = 1000.0
    vols[-1] = 0.0  # far below the 20-bar mean
    # Pump up a couple of earlier bars so the std is non-zero and the z-score is clearly < -0.5.
    vols[-5] = 2000.0
    frame["vol"] = vols

    decision = ZscoreMeanReversionEngine(params).evaluate(frame)
    assert decision.action is None


def test_engine_respects_candle_time_of_last_row():
    engine = ZscoreMeanReversionEngine()
    frame = _make_flat_frame(count=5)
    decision = engine.evaluate(frame)
    assert decision.candle_open_time == pd.Timestamp(frame["ts"].iloc[-1]).to_pydatetime()


# ---------------------------------------------------------------------------
# Strategy-mode selection in the runtime
# ---------------------------------------------------------------------------


def test_strategy_mode_defaults_to_xgb():
    # Default means unchanged behaviour: legacy path remains intact.
    assert StrategyMode("xgb") is StrategyMode.XGB


def test_strategy_mode_parses_zscore_variant():
    assert StrategyMode("zscore_mean_reversion_v1") is StrategyMode.ZSCORE_MEAN_REVERSION_V1


def _minimal_config_env(monkeypatch) -> None:
    """Set the minimum env needed for load_app_config() to succeed in demo mode."""
    monkeypatch.setenv("EXCHANGE_ENV", "demo")
    monkeypatch.setenv("ALLOW_LIVE_MODE", "false")
    monkeypatch.setenv("DRY_RUN_MODE", "true")
    monkeypatch.setenv("BYBIT_API_KEY", "k")
    monkeypatch.setenv("BYBIT_API_SECRET", "s")


def test_load_app_config_rejects_unknown_strategy_mode(monkeypatch, tmp_path):
    from sentinel_runtime.config import load_app_config
    from sentinel_runtime.errors import ConfigError

    _minimal_config_env(monkeypatch)
    monkeypatch.setenv("STRATEGY_MODE", "nonexistent_strategy")
    with pytest.raises(ConfigError, match="Unsupported STRATEGY_MODE"):
        load_app_config(tmp_path / ".env.missing")


def test_load_app_config_sets_zscore_strategy_mode(monkeypatch, tmp_path):
    from sentinel_runtime.config import load_app_config

    _minimal_config_env(monkeypatch)
    monkeypatch.setenv("STRATEGY_MODE", "zscore_mean_reversion_v1")
    config = load_app_config(tmp_path / ".env.missing")
    assert config.strategy.strategy_mode is StrategyMode.ZSCORE_MEAN_REVERSION_V1


def test_load_app_config_default_strategy_mode_is_xgb(monkeypatch, tmp_path):
    from sentinel_runtime.config import load_app_config

    _minimal_config_env(monkeypatch)
    monkeypatch.delenv("STRATEGY_MODE", raising=False)
    config = load_app_config(tmp_path / ".env.missing")
    assert config.strategy.strategy_mode is StrategyMode.XGB
