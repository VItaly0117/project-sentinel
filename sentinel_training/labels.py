from __future__ import annotations

from typing import Iterable

import numpy as np
import pandas as pd

from .config import LabelConfig


def create_labels(dataframe: pd.DataFrame, config: LabelConfig | None = None) -> list[int]:
    effective_config = config or LabelConfig()
    return create_label_series(dataframe, effective_config).tolist()


def create_label_series(dataframe: pd.DataFrame, config: LabelConfig) -> pd.Series:
    closes = dataframe["close"].to_numpy()
    highs = dataframe["high"].to_numpy()
    lows = dataframe["low"].to_numpy()
    labels = np.zeros(len(dataframe), dtype=int)

    for index in range(len(closes) - config.look_ahead):
        current_price = closes[index]
        future_highs = highs[index + 1 : index + 1 + config.look_ahead]
        future_lows = lows[index + 1 : index + 1 + config.look_ahead]

        long_take_profit = current_price * (1 + config.tp_pct)
        long_stop_loss = current_price * (1 - config.sl_pct)
        short_take_profit = current_price * (1 - config.tp_pct)
        short_stop_loss = current_price * (1 + config.sl_pct)

        first_long_tp = _first_hit(future_highs >= long_take_profit)
        first_long_sl = _first_hit(future_lows <= long_stop_loss)
        first_short_tp = _first_hit(future_lows <= short_take_profit)
        first_short_sl = _first_hit(future_highs >= short_stop_loss)

        long_valid = first_long_tp < first_long_sl
        short_valid = first_short_tp < first_short_sl

        if long_valid and short_valid:
            labels[index] = 2 if first_long_tp < first_short_tp else 1
        elif long_valid:
            labels[index] = 2
        elif short_valid:
            labels[index] = 1

    return pd.Series(labels, index=dataframe.index, name="target")


def _first_hit(mask: Iterable[bool]) -> int:
    hits = np.flatnonzero(mask)
    return int(hits[0]) if len(hits) > 0 else 999
