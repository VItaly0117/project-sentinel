#!/usr/bin/env python3
"""
Sentinel Backtester — algorithm sandbox tool for Dima.

ЦЕЛЬ:
  Быстрая проверка гипотез: помогает понять, было бы выгодным
  следование сигналам модели на историческом CSV, если бы мы исполняли
  TP/SL строго по тем же правилам, что и labels.py.

КАК ЗАПУСКАТЬ (копируй и меняй параметры):

  # Базовый прогон на нашем январском датасете:
  python3 scripts/backtest.py \
      --data-path data/normalized/binance/BTCUSDT/5m/binance_BTCUSDT_5m_20240101T000000Z_20240131T235500Z.csv \
      --model-path monster_v4_2.json

  # Поиск гипертпараметров — поднять уверенность и изменить TP/SL:
  python3 scripts/backtest.py \
      --data-path data/normalized/binance/BTCUSDT/5m/... \
      --confidence 0.60 \
      --tp-pct 0.015 \
      --sl-pct 0.007

  # Прогон на Bybit-датасете (другой exchange, для сравнения):
  python3 scripts/backtest.py \
      --data-path data/normalized/bybit/BTCUSDT/5/... \
      --model-path monster_v4_2.json

ПАРАМЕТРЫ (все опциональные, кроме --data-path):

  --confidence    Минимальная уверенность модели для генерации сигнала (default: 0.51)
  --tp-pct        Take-profit от цены входа (default: 0.012 = 1.2%)
  --sl-pct        Stop-loss от цены входа  (default: 0.006 = 0.6%)
  --look-ahead    Макс. свечей ожидания TP/SL; timeout = выход по close (default: 36)
  --order-qty     Размер позиции в базовой валюте (default: 0.001 BTC)
  --commission    Комиссия тейкера на одну сторону (default: 0.00055 = 0.055%)
  --initial-balance   Стартовый виртуальный баланс для расчёта drawdown% (default: 1000)
  --interval-minutes  Таймфрейм свечей в минутах, только для отображения длительности (default: 5)

ВАЖНЫЕ ОГРАНИЧЕНИЯ (обязательно прочти перед доверием цифрам):

  1. Нет проскальзывания (slippage) — вход строго по close-цене сигнальной свечи.
  2. TP/SL исполняется ровно по указанной цене — в реале будет хуже.
  3. Нет стакана, очереди, частичных исполнений.
  4. Одна позиция одновременно — нет перекрытий.
  5. Это инструмент валидации лейблов, НЕ production-trading-сигнал.
  6. Прошлые результаты не гарантируют будущих.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd
import xgboost as xgb

# Добавляем корень проекта в sys.path, чтобы импортировать sentinel-пакеты
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sentinel_runtime.feature_engine import SMCEngine  # READ-ONLY — не редактировать

_FEATURE_NAMES: list[str] = SMCEngine.get_feature_names()

# SMCEngine.add_features использует rolling(288) для high_24h — нужен тёплый период
_MIN_CANDLES_REQUIRED: int = 300


# ---------------------------------------------------------------------------
# Типы данных
# ---------------------------------------------------------------------------

@dataclass
class Trade:
    side: Literal["long", "short"]
    entry_price: float
    exit_price: float
    outcome: Literal["tp", "sl", "timeout"]
    pnl_usdt: float
    duration_candles: int


# ---------------------------------------------------------------------------
# Загрузка данных и модели
# ---------------------------------------------------------------------------

def _load_csv(path: Path) -> pd.DataFrame:
    """Загрузить нормализованный CSV из нашего ingest pipeline."""
    if not path.exists():
        raise SystemExit(f"[backtest] Файл не найден: {path}")
    df = pd.read_csv(path)
    required = {"ts", "open", "high", "low", "close", "vol"}
    missing = required - set(df.columns)
    if missing:
        raise SystemExit(f"[backtest] Отсутствуют колонки: {missing}")
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    df = df.sort_values("ts").reset_index(drop=True)
    return df


def _load_model(path: Path) -> xgb.XGBClassifier:
    """Загрузить XGBoost артефакт."""
    if not path.exists():
        raise SystemExit(f"[backtest] Модель не найдена: {path}")
    model = xgb.XGBClassifier()
    model.load_model(str(path))
    return model


# ---------------------------------------------------------------------------
# Признаки и предсказания (batch — намного быстрее, чем построчно)
# ---------------------------------------------------------------------------

def _compute_features_and_probs(
    raw_df: pd.DataFrame,
    model: xgb.XGBClassifier,
) -> tuple[pd.DataFrame, np.ndarray]:
    """
    Возвращает (enriched_df, probs).

    enriched_df — DatetimeIndex, содержит OHLCV + фичи (NaN-строки удалены).
    probs       — shape (n_rows, 3).
                  Индексы классов: 0=no-signal, 1=short, 2=long
                  (совпадает с labels.py: 0, 1, 2).
    """
    enriched = SMCEngine.add_features(raw_df.copy())
    if enriched.empty:
        raise SystemExit(
            "[backtest] После вычисления признаков не осталось строк. "
            "Нужно больше данных (минимум ~300 свечей)."
        )
    probs = model.predict_proba(enriched[_FEATURE_NAMES])
    return enriched, probs


# ---------------------------------------------------------------------------
# Симуляция сделок
# ---------------------------------------------------------------------------

def _simulate(
    raw_df: pd.DataFrame,
    enriched: pd.DataFrame,
    probs: np.ndarray,
    *,
    confidence: float,
    tp_pct: float,
    sl_pct: float,
    look_ahead: int,
    order_qty: float,
    commission_pct: float,
) -> list[Trade]:
    """
    Симулирует сделки без перекрытий (одна позиция одновременно).

    Логика входа: сигнал на close-цене сигнальной свечи.
    Логика выхода: первый пробой TP/SL по H/L будущих свечей
                   (идентично barrier-touch в labels.py).
    Timeout: выход по close последней допустимой свечи (look_ahead).
    PnL = брутто ± комиссия за 2 стороны (entry + exit).
    """
    # Быстрый поиск позиции свечи в raw_df по её timestamp
    raw_ts_series: list[pd.Timestamp] = list(raw_df["ts"])
    ts_to_raw_pos: dict[pd.Timestamp, int] = {ts: i for i, ts in enumerate(raw_ts_series)}

    raw_highs = raw_df["high"].to_numpy(dtype=float)
    raw_lows = raw_df["low"].to_numpy(dtype=float)
    raw_closes = raw_df["close"].to_numpy(dtype=float)

    trades: list[Trade] = []
    # Блокируем вход до тех пор, пока текущая сделка не закрыта
    block_until: pd.Timestamp = pd.Timestamp("1970-01-01", tz="UTC")

    for i, (signal_ts, row) in enumerate(enriched.iterrows()):
        if signal_ts <= block_until:
            continue

        p_long = float(probs[i, 2])   # класс 2 = long
        p_short = float(probs[i, 1])  # класс 1 = short

        if p_long >= confidence:
            side: Literal["long", "short"] = "long"
        elif p_short >= confidence:
            side = "short"
        else:
            continue

        raw_pos = ts_to_raw_pos.get(signal_ts)
        if raw_pos is None or raw_pos + look_ahead >= len(raw_df):
            continue  # недостаточно будущих данных

        entry_price = float(row["close"])

        if side == "long":
            tp_price = entry_price * (1.0 + tp_pct)
            sl_price = entry_price * (1.0 - sl_pct)
        else:
            tp_price = entry_price * (1.0 - tp_pct)
            sl_price = entry_price * (1.0 + sl_pct)

        # Смотрим в будущее (так же, как labels.py)
        future_h = raw_highs[raw_pos + 1 : raw_pos + 1 + look_ahead]
        future_l = raw_lows[raw_pos + 1 : raw_pos + 1 + look_ahead]

        if side == "long":
            tp_hits = np.flatnonzero(future_h >= tp_price)
            sl_hits = np.flatnonzero(future_l <= sl_price)
        else:
            tp_hits = np.flatnonzero(future_l <= tp_price)
            sl_hits = np.flatnonzero(future_h >= sl_price)

        first_tp = int(tp_hits[0]) if len(tp_hits) else look_ahead
        first_sl = int(sl_hits[0]) if len(sl_hits) else look_ahead

        if first_tp < first_sl:
            outcome: Literal["tp", "sl", "timeout"] = "tp"
            exit_offset = first_tp
            exit_price = tp_price
        elif first_sl < first_tp:
            outcome = "sl"
            exit_offset = first_sl
            exit_price = sl_price
        else:
            # Ни TP, ни SL не достигнуты — выход по close
            outcome = "timeout"
            exit_offset = look_ahead - 1
            exit_price = float(raw_closes[raw_pos + 1 + exit_offset])

        direction = 1.0 if side == "long" else -1.0
        gross_pnl = direction * (exit_price - entry_price) * order_qty
        # Комиссия: 2 стороны × fee × notional (оцениваем notional по entry)
        commission = 2.0 * commission_pct * entry_price * order_qty
        net_pnl = gross_pnl - commission

        exit_raw_pos = raw_pos + 1 + exit_offset
        block_until = raw_ts_series[exit_raw_pos]

        trades.append(Trade(
            side=side,
            entry_price=entry_price,
            exit_price=exit_price,
            outcome=outcome,
            pnl_usdt=net_pnl,
            duration_candles=exit_offset + 1,
        ))

    return trades


# ---------------------------------------------------------------------------
# Отчёт
# ---------------------------------------------------------------------------

def _section(label: str, width: int = 60) -> None:
    print(f"\n  {'─' * 4}  {label}  {'─' * max(0, width - len(label) - 8)}")


def _print_report(
    trades: list[Trade],
    initial_balance: float,
    interval_minutes: int,
    data_path: Path,
    model_path: Path,
    confidence: float,
    tp_pct: float,
    sl_pct: float,
    look_ahead: int,
) -> None:
    bar = "═" * 60

    print()
    print(f"  {bar}")
    print("      SENTINEL BACKTEST REPORT")
    print(f"  {bar}")
    print(f"  Data   : {data_path.name}")
    print(f"  Model  : {model_path.name}")
    print(f"  Config : confidence={confidence}  TP={tp_pct*100:.2f}%  SL={sl_pct*100:.2f}%  "
          f"look_ahead={look_ahead}")
    print(f"  {bar}")

    if not trades:
        print("  Сделки не сгенерированы.")
        print("  → Попробуй снизить --confidence или проверь alignment модели/данных.")
        print(f"  {bar}\n")
        return

    pnls = np.array([t.pnl_usdt for t in trades], dtype=float)

    # Equity curve
    equity_curve = initial_balance + np.cumsum(pnls)
    peak = np.maximum.accumulate(np.concatenate(([initial_balance], equity_curve[:-1])))
    drawdowns_abs = peak - equity_curve
    max_dd_usdt = float(drawdowns_abs.max())
    max_dd_pct = max_dd_usdt / initial_balance * 100.0
    final_equity = float(equity_curve[-1])

    total_pnl = float(pnls.sum())
    total_pnl_pct = total_pnl / initial_balance * 100.0

    wins = [t for t in trades if t.pnl_usdt > 0]
    losses = [t for t in trades if t.pnl_usdt <= 0]
    win_rate = len(wins) / len(trades) * 100.0

    gross_profit = sum(t.pnl_usdt for t in wins)
    gross_loss = abs(sum(t.pnl_usdt for t in losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    longs = [t for t in trades if t.side == "long"]
    shorts = [t for t in trades if t.side == "short"]
    long_wr = (sum(1 for t in longs if t.pnl_usdt > 0) / len(longs) * 100) if longs else 0.0
    short_wr = (sum(1 for t in shorts if t.pnl_usdt > 0) / len(shorts) * 100) if shorts else 0.0

    tp_n = sum(1 for t in trades if t.outcome == "tp")
    sl_n = sum(1 for t in trades if t.outcome == "sl")
    to_n = sum(1 for t in trades if t.outcome == "timeout")

    avg_dur = np.mean([t.duration_candles for t in trades])
    avg_dur_min = avg_dur * interval_minutes

    avg_win = np.mean([t.pnl_usdt for t in wins]) if wins else 0.0
    avg_loss = np.mean([t.pnl_usdt for t in losses]) if losses else 0.0
    best = float(pnls.max())
    worst = float(pnls.min())

    # Sharpe (грубая оценка: mean/std * sqrt(252*candles_per_day))
    # Для 5m: 288 свечей/день → 252*288 = 72576 «периодов» в году
    # Но у нас PnL на сделку, не на свечу, поэтому annualization условная.
    # Выводим просто mean/std как ориентир.
    pnl_std = float(pnls.std()) if len(pnls) > 1 else 0.0
    sharpe_proxy = (pnls.mean() / pnl_std) if pnl_std > 0 else 0.0

    _section("ИТОГО")
    print(f"  Сделок всего        : {len(trades)}  "
          f"(long={len(longs)}, short={len(shorts)})")
    print(f"  Win Rate            : {win_rate:.1f}%  "
          f"(long={long_wr:.1f}%, short={short_wr:.1f}%)")
    print(f"  Profit Factor       : {profit_factor:.2f}")

    _section("PnL")
    print(f"  Total PnL           : {total_pnl:+.4f} USDT  ({total_pnl_pct:+.2f}%)")
    print(f"  Начальный баланс    : {initial_balance:.2f} USDT")
    print(f"  Конечный баланс     : {final_equity:.2f} USDT")
    print(f"  Max Drawdown        : -{max_dd_usdt:.4f} USDT  (-{max_dd_pct:.2f}%)")

    _section("ИСХОДЫ СДЕЛОК")
    print(f"  TP достигнут        : {tp_n}  ({tp_n/len(trades)*100:.1f}%)")
    print(f"  SL достигнут        : {sl_n}  ({sl_n/len(trades)*100:.1f}%)")
    print(f"  Timeout (no touch)  : {to_n}  ({to_n/len(trades)*100:.1f}%)")

    _section("СТАТИСТИКА СДЕЛОК")
    print(f"  Лучшая сделка       : {best:+.4f} USDT")
    print(f"  Худшая сделка       : {worst:+.4f} USDT")
    print(f"  Средний winner      : {avg_win:+.4f} USDT")
    print(f"  Средний loser       : {avg_loss:+.4f} USDT")
    print(f"  Средний PnL/сделку  : {pnls.mean():+.4f} USDT")
    print(f"  Средняя длительность: {avg_dur:.1f} свечей  ({avg_dur_min:.0f} мин)")
    print(f"  Sharpe (proxy)      : {sharpe_proxy:.3f}  "
          f"(mean/std на сделку, не аннуализированный)")

    _section("ДИСКЛЕЙМЕР", width=60)
    print("  Нет slippage, spread, queue, partial fills.")
    print("  Вход по close; TP/SL исполняются точно — на реале будет хуже.")
    print("  Инструмент для валидации лейблов, не для торговли.")
    print()
    print(f"  {bar}\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Sentinel backtester — algorithm sandbox for Dima.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument(
        "--data-path", type=Path, required=True,
        help="Путь к нормализованному CSV (ts,open,high,low,close,vol).",
    )
    p.add_argument(
        "--model-path", type=Path, default=Path("monster_v4_2.json"),
        help="Путь к XGBoost-артефакту (.json).",
    )
    p.add_argument(
        "--confidence", type=float, default=0.51,
        help="Минимальная уверенность модели для генерации сигнала.",
    )
    p.add_argument(
        "--tp-pct", type=float, default=0.012,
        help="Take-profit как доля от цены входа (например, 0.012 = 1.2%%).",
    )
    p.add_argument(
        "--sl-pct", type=float, default=0.006,
        help="Stop-loss как доля от цены входа (например, 0.006 = 0.6%%).",
    )
    p.add_argument(
        "--look-ahead", type=int, default=36,
        help="Максимум свечей для ожидания TP/SL; иначе — timeout по close.",
    )
    p.add_argument(
        "--order-qty", type=float, default=0.001,
        help="Размер контракта в базовой валюте (BTC).",
    )
    p.add_argument(
        "--commission", type=float, default=0.00055,
        help="Тейкер-комиссия на одну сторону (0.00055 = 0.055%%).",
    )
    p.add_argument(
        "--initial-balance", type=float, default=1000.0,
        help="Стартовый виртуальный баланс USDT для расчёта drawdown%%.",
    )
    p.add_argument(
        "--interval-minutes", type=int, default=5,
        help="Таймфрейм свечей в минутах (только для отображения длительности).",
    )
    return p.parse_args()


def main() -> int:
    args = _parse_args()

    print(f"\n[backtest] Загрузка данных: {args.data_path}")
    raw_df = _load_csv(args.data_path)
    n = len(raw_df)
    print(f"[backtest] {n} свечей загружено  "
          f"({raw_df['ts'].iloc[0].date()} → {raw_df['ts'].iloc[-1].date()})")

    if n < _MIN_CANDLES_REQUIRED:
        raise SystemExit(
            f"[backtest] Нужно минимум {_MIN_CANDLES_REQUIRED} свечей. Получено: {n}."
        )

    print(f"[backtest] Загрузка модели: {args.model_path}")
    model = _load_model(args.model_path)

    print("[backtest] Вычисление признаков (batch)...")
    enriched, probs = _compute_features_and_probs(raw_df, model)
    print(f"[backtest] {len(enriched)} свечей с валидными признаками.")

    n_long_signals = int((probs[:, 2] >= args.confidence).sum())
    n_short_signals = int((probs[:, 1] >= args.confidence).sum())
    print(f"[backtest] Сигналов выше порога: long={n_long_signals}, short={n_short_signals}")

    print("[backtest] Симуляция сделок...")
    trades = _simulate(
        raw_df,
        enriched,
        probs,
        confidence=args.confidence,
        tp_pct=args.tp_pct,
        sl_pct=args.sl_pct,
        look_ahead=args.look_ahead,
        order_qty=args.order_qty,
        commission_pct=args.commission,
    )
    print(f"[backtest] Симулировано {len(trades)} сделок (без перекрытий).")

    _print_report(
        trades,
        initial_balance=args.initial_balance,
        interval_minutes=args.interval_minutes,
        data_path=args.data_path,
        model_path=args.model_path,
        confidence=args.confidence,
        tp_pct=args.tp_pct,
        sl_pct=args.sl_pct,
        look_ahead=args.look_ahead,
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
