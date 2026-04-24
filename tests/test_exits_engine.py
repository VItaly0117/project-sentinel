"""Unit tests for the exit engine (`sentinel_runtime/exits.py`).

These tests are deterministic, Decimal-based, and do not touch the
exchange, storage, or signal layers. They cover ATR correctness, long
and short trailing activation/advance, hard-SL precedence, fixed-TP
handling, min-lock floor, insufficient-ATR behaviour, and the
conservative same-candle ambiguity rule.
"""
from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sentinel_runtime.exits import (  # noqa: E402
    AtrTrailingConfig,
    ExitState,
    build_initial_levels,
    compute_atr,
    fixed_trailing_config,
    initial_exit_state,
    update_exit_state_with_candle,
)


def _D(v):
    return Decimal(str(v))


def _trailing(
    *,
    activation_pct="0.004",
    atr_mult="1.4",
    atr_period=14,
    min_lock_pct="0.0015",
    keep_fixed_tp=False,
    enabled=True,
) -> AtrTrailingConfig:
    cfg = AtrTrailingConfig(
        enabled=enabled,
        activation_pct=_D(activation_pct),
        atr_mult=_D(atr_mult),
        atr_period=atr_period,
        min_lock_pct=_D(min_lock_pct),
        keep_fixed_tp=keep_fixed_tp,
    )
    cfg.validate()
    return cfg


# ---------------------------------------------------------------------------
# ATR
# ---------------------------------------------------------------------------


def test_compute_atr_returns_none_when_history_is_short():
    highs = [_D("10"), _D("11")]
    lows = [_D("9"), _D("10")]
    closes = [_D("9.5"), _D("10.5")]
    assert compute_atr(highs, lows, closes, period=3) is None


def test_compute_atr_matches_hand_calculation():
    # Construct 4 bars so TR window of 3 samples is straightforward.
    #   bar0 close=10  (TR requires prev, ignored)
    #   bar1 H=12 L=9  prev_close=10 → TR = max(3, 2, 1) = 3
    #   bar2 H=13 L=11 prev_close=11 → TR = max(2, 2, 0) = 2
    #   bar3 H=15 L=12 prev_close=12 → TR = max(3, 3, 0) = 3
    highs = [_D("11"), _D("12"), _D("13"), _D("15")]
    lows = [_D("9"), _D("9"), _D("11"), _D("12")]
    closes = [_D("10"), _D("11"), _D("12"), _D("14")]
    atr = compute_atr(highs, lows, closes, period=3)
    assert atr == (_D("3") + _D("2") + _D("3")) / _D("3")


def test_compute_atr_rejects_invalid_period():
    with pytest.raises(ValueError):
        compute_atr([_D("1")], [_D("1")], [_D("1")], period=1)


# ---------------------------------------------------------------------------
# Long — activation, monotonic stop, min-lock floor
# ---------------------------------------------------------------------------


def _new_long_state(entry=_D("100"), hard_stop=_D("99.4"), fixed_tp=None, entry_atr=_D("1")):
    return initial_exit_state(
        side="Buy",
        qty=_D("0.001"),
        entry_price=entry,
        hard_stop=hard_stop,
        fixed_take_profit=fixed_tp,
        entry_atr=entry_atr,
    )


def test_long_trailing_activates_only_after_activation_pct():
    cfg = _trailing(activation_pct="0.01")  # 1% activation
    state = _new_long_state()

    # Candle well below activation — stays off.
    decision = update_exit_state_with_candle(
        state, cfg,
        candle_high=_D("100.5"), candle_low=_D("100"), candle_close=_D("100.4"),
        current_atr=_D("1"),
    )
    assert not decision.should_close
    assert decision.state.trailing_active is False

    # Candle pokes above activation — activates trailing on next candle.
    decision = update_exit_state_with_candle(
        decision.state, cfg,
        candle_high=_D("101.1"), candle_low=_D("100.5"), candle_close=_D("101"),
        current_atr=_D("1"),
    )
    assert decision.state.trailing_active is True
    assert decision.state.trailing_stop is not None


def test_long_trailing_stop_never_moves_down():
    cfg = _trailing(activation_pct="0", atr_mult="1", min_lock_pct="0")
    state = _new_long_state()

    # First advance: best=103, ATR=1 → trailing=102
    d1 = update_exit_state_with_candle(
        state, cfg,
        candle_high=_D("103"), candle_low=_D("100"), candle_close=_D("102.5"),
        current_atr=_D("1"),
    )
    assert d1.state.trailing_active
    first_stop = d1.state.trailing_stop
    assert first_stop == _D("102")

    # Next candle's high is lower; stop must NOT decrease.
    d2 = update_exit_state_with_candle(
        d1.state, cfg,
        candle_high=_D("102"), candle_low=_D("101"), candle_close=_D("101.5"),
        current_atr=_D("1"),
    )
    assert d2.state.trailing_stop == first_stop


def test_long_hard_sl_fires_before_activation():
    cfg = _trailing(activation_pct="0.01")
    state = _new_long_state(hard_stop=_D("99"))
    decision = update_exit_state_with_candle(
        state, cfg,
        candle_high=_D("100.5"), candle_low=_D("98.5"), candle_close=_D("99"),
        current_atr=_D("1"),
    )
    assert decision.should_close is True
    assert decision.reason == "hard_sl"
    assert decision.exit_price == _D("99")


def test_long_fixed_tp_fires_when_enabled():
    cfg = _trailing(activation_pct="0.01", keep_fixed_tp=True)
    state = _new_long_state(fixed_tp=_D("102"))
    decision = update_exit_state_with_candle(
        state, cfg,
        candle_high=_D("102.5"), candle_low=_D("100"), candle_close=_D("102"),
        current_atr=_D("1"),
    )
    assert decision.should_close is True
    assert decision.reason == "fixed_tp"
    assert decision.exit_price == _D("102")


def test_long_fixed_tp_is_off_when_not_kept():
    cfg = _trailing(activation_pct="0.01", keep_fixed_tp=False)
    state = _new_long_state(fixed_tp=None)
    decision = update_exit_state_with_candle(
        state, cfg,
        candle_high=_D("110"), candle_low=_D("100"), candle_close=_D("108"),
        current_atr=_D("1"),
    )
    assert decision.should_close is False


def test_long_min_lock_pct_clamps_trailing_stop_floor():
    # activation=0 so we activate on the first candle. ATR=10 gives a
    # very loose candidate (best - 1.4*10 = 93) but min-lock=0.005 on
    # entry=100 gives 100.5, which must win.
    cfg = _trailing(activation_pct="0", atr_mult="1.4", min_lock_pct="0.005")
    state = _new_long_state()
    decision = update_exit_state_with_candle(
        state, cfg,
        candle_high=_D("101"), candle_low=_D("99.6"), candle_close=_D("101"),
        current_atr=_D("10"),
    )
    assert decision.state.trailing_active
    assert decision.state.trailing_stop == _D("100.5")


def test_long_insufficient_atr_leaves_trailing_stop_unchanged_but_may_activate():
    cfg = _trailing(activation_pct="0", min_lock_pct="0.001")
    state = _new_long_state()
    decision = update_exit_state_with_candle(
        state, cfg,
        candle_high=_D("103"), candle_low=_D("100"), candle_close=_D("102"),
        current_atr=None,
    )
    # Activates because best_price crossed activation (=entry).
    assert decision.state.trailing_active is True
    # Without ATR the candidate from best_price - atr_mult*atr is skipped,
    # so stop falls back to the min-lock floor: 100 * 1.001 = 100.1
    assert decision.state.trailing_stop == _D("100.1")


def test_long_same_candle_adverse_wins_when_not_yet_active():
    # Entry 100, hard SL 99. activation=0 (would activate on a favorable
    # move). Candle has both a deep wick below hard SL and a spike up —
    # adverse must win since trailing was not active BEFORE this candle.
    cfg = _trailing(activation_pct="0")
    state = _new_long_state(hard_stop=_D("99"))
    decision = update_exit_state_with_candle(
        state, cfg,
        candle_high=_D("110"), candle_low=_D("98"), candle_close=_D("109"),
        current_atr=_D("1"),
    )
    assert decision.should_close is True
    assert decision.reason == "hard_sl"


def test_long_same_candle_favorable_updates_before_adverse_when_already_active():
    # Activate first.
    cfg = _trailing(activation_pct="0", atr_mult="1", min_lock_pct="0")
    state = _new_long_state(hard_stop=_D("98"))
    d1 = update_exit_state_with_candle(
        state, cfg,
        candle_high=_D("103"), candle_low=_D("100"), candle_close=_D("102.5"),
        current_atr=_D("1"),
    )
    assert d1.state.trailing_active
    assert d1.state.trailing_stop == _D("102")

    # Next candle: price spikes to 110 and wicks down to 101.
    # Already active → favorable updates first, new trailing stop = 110-1 = 109.
    # Adverse 101 < 109 → trailing_stop exit at 109.
    d2 = update_exit_state_with_candle(
        d1.state, cfg,
        candle_high=_D("110"), candle_low=_D("101"), candle_close=_D("109"),
        current_atr=_D("1"),
    )
    assert d2.should_close is True
    assert d2.reason == "trailing_stop"
    assert d2.exit_price == _D("109")


# ---------------------------------------------------------------------------
# Short — mirrors the long cases
# ---------------------------------------------------------------------------


def _new_short_state(entry=_D("100"), hard_stop=_D("100.6"), fixed_tp=None, entry_atr=_D("1")):
    return initial_exit_state(
        side="Sell",
        qty=_D("0.001"),
        entry_price=entry,
        hard_stop=hard_stop,
        fixed_take_profit=fixed_tp,
        entry_atr=entry_atr,
    )


def test_short_trailing_activates_after_activation_pct():
    cfg = _trailing(activation_pct="0.01")
    state = _new_short_state()
    # First candle barely moves — no activation.
    d1 = update_exit_state_with_candle(
        state, cfg,
        candle_high=_D("100.1"), candle_low=_D("99.7"), candle_close=_D("99.9"),
        current_atr=_D("1"),
    )
    assert d1.state.trailing_active is False

    # Price drops >1% → activates.
    d2 = update_exit_state_with_candle(
        d1.state, cfg,
        candle_high=_D("99.5"), candle_low=_D("98.9"), candle_close=_D("99"),
        current_atr=_D("1"),
    )
    assert d2.state.trailing_active is True
    assert d2.state.trailing_stop is not None


def test_short_trailing_stop_never_moves_up():
    cfg = _trailing(activation_pct="0", atr_mult="1", min_lock_pct="0")
    state = _new_short_state()

    d1 = update_exit_state_with_candle(
        state, cfg,
        candle_high=_D("100"), candle_low=_D("97"), candle_close=_D("97.5"),
        current_atr=_D("1"),
    )
    first_stop = d1.state.trailing_stop
    assert first_stop == _D("98")  # best=97 → 97 + 1*1 = 98

    # Favorable moves stall; stop must not move UP.
    d2 = update_exit_state_with_candle(
        d1.state, cfg,
        candle_high=_D("98"), candle_low=_D("97.5"), candle_close=_D("97.8"),
        current_atr=_D("1"),
    )
    assert d2.state.trailing_stop == first_stop


def test_short_hard_sl_fires_before_activation():
    cfg = _trailing(activation_pct="0.01")
    state = _new_short_state(hard_stop=_D("101"))
    decision = update_exit_state_with_candle(
        state, cfg,
        candle_high=_D("101.5"), candle_low=_D("99.5"), candle_close=_D("101"),
        current_atr=_D("1"),
    )
    assert decision.should_close is True
    assert decision.reason == "hard_sl"
    assert decision.exit_price == _D("101")


def test_short_fixed_tp_fires_when_enabled():
    cfg = _trailing(activation_pct="0.01", keep_fixed_tp=True)
    state = _new_short_state(fixed_tp=_D("98"))
    decision = update_exit_state_with_candle(
        state, cfg,
        candle_high=_D("100"), candle_low=_D("97.5"), candle_close=_D("98"),
        current_atr=_D("1"),
    )
    assert decision.should_close is True
    assert decision.reason == "fixed_tp"
    assert decision.exit_price == _D("98")


def test_short_min_lock_pct_clamps_trailing_stop_ceiling():
    cfg = _trailing(activation_pct="0", atr_mult="1.4", min_lock_pct="0.005")
    state = _new_short_state()
    decision = update_exit_state_with_candle(
        state, cfg,
        candle_high=_D("100.4"), candle_low=_D("99"), candle_close=_D("99"),
        current_atr=_D("10"),
    )
    assert decision.state.trailing_active
    # entry*(1-0.005) = 99.5; candidate best+atr_mult*atr=99+14=113 (way higher)
    # So min(113, 99.5, previous_None→ignored) = 99.5.
    assert decision.state.trailing_stop == _D("99.5")


# ---------------------------------------------------------------------------
# State serialization + initial-levels helper
# ---------------------------------------------------------------------------


def test_exit_state_round_trips_through_dict():
    state = ExitState(
        side="Buy",
        qty=_D("0.002"),
        entry_price=_D("101.5"),
        hard_stop=_D("100.8"),
        fixed_take_profit=_D("102.6"),
        trailing_active=True,
        best_price=_D("102.3"),
        trailing_stop=_D("101.9"),
        entry_atr=_D("0.4"),
        last_update_candle_time="2026-04-24T12:05:00+00:00",
    )
    restored = ExitState.from_dict(state.to_dict())
    assert restored == state


def test_build_initial_levels_matches_existing_percent_math():
    long_levels = build_initial_levels(
        side="Buy",
        entry_price=_D("100"),
        sl_pct=_D("0.006"),
        tp_pct=_D("0.012"),
        include_fixed_tp=True,
    )
    assert long_levels.hard_stop == _D("99.400")
    assert long_levels.fixed_take_profit == _D("101.200")

    short_levels = build_initial_levels(
        side="Sell",
        entry_price=_D("100"),
        sl_pct=_D("0.006"),
        tp_pct=_D("0.012"),
        include_fixed_tp=False,
    )
    assert short_levels.hard_stop == _D("100.600")
    assert short_levels.fixed_take_profit is None


def test_fixed_trailing_config_is_disabled_and_validates():
    cfg = fixed_trailing_config()
    cfg.validate()
    assert cfg.enabled is False
