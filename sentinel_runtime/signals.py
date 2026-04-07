from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pandas as pd
import xgboost as xgb

from .errors import ConfigError
from .feature_engine import SMCEngine
from .models import SignalDecision


class ModelSignalEngine:
    def __init__(self, model_path: Path, confidence_threshold: float) -> None:
        if not model_path.exists():
            raise ConfigError(f"Model file not found: {model_path}.")

        self._confidence_threshold = confidence_threshold
        self._feature_names = SMCEngine.get_feature_names()
        self._model = xgb.XGBClassifier()
        self._model.load_model(str(model_path))

    def evaluate(self, candles: pd.DataFrame) -> SignalDecision:
        enriched = SMCEngine.add_features(candles)
        if enriched.empty:
            raise ConfigError("Not enough candle data after feature generation.")

        last_row = enriched.iloc[-1:]
        probabilities = self._model.predict_proba(last_row[self._feature_names])[0]
        short_probability = float(probabilities[1])
        long_probability = float(probabilities[2])
        market_price = Decimal(str(last_row["close"].iloc[0]))
        candle_open_time = last_row.index[-1].to_pydatetime()

        action = None
        if long_probability >= self._confidence_threshold:
            action = "Buy"
        elif short_probability >= self._confidence_threshold:
            action = "Sell"

        return SignalDecision(
            candle_open_time=candle_open_time,
            long_probability=long_probability,
            short_probability=short_probability,
            market_price=market_price,
            action=action,
        )
