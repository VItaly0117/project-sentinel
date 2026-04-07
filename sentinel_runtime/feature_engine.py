from __future__ import annotations

import numpy as np
import pandas as pd


class SMCEngine:
    @staticmethod
    def calculate_atr(dataframe: pd.DataFrame, window: int = 14) -> pd.Series:
        high_low = dataframe["high"] - dataframe["low"]
        high_close = np.abs(dataframe["high"] - dataframe["close"].shift())
        low_close = np.abs(dataframe["low"] - dataframe["close"].shift())
        ranges = pd.concat([high_low, high_close, low_close], axis=1)
        true_range = np.max(ranges, axis=1)
        return true_range.rolling(window=window).mean()

    @staticmethod
    def calculate_rsi(dataframe: pd.DataFrame, window: int = 14) -> pd.Series:
        delta = dataframe["close"].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=window).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=window).mean()
        rs = gain / loss
        return 100 - (100 / (1 + rs))

    @staticmethod
    def add_features(dataframe: pd.DataFrame) -> pd.DataFrame:
        frame = dataframe.copy()

        if not isinstance(frame.index, pd.DatetimeIndex):
            frame["ts"] = pd.to_datetime(frame["ts"], utc=True)
            frame.set_index("ts", inplace=True)

        frame["hour"] = frame.index.hour
        frame["day_of_week"] = frame.index.dayofweek
        frame["msb_long"] = (frame["close"] > frame["high"].shift(1).rolling(24).max()).astype(int)
        frame["msb_short"] = (frame["close"] < frame["low"].shift(1).rolling(24).min()).astype(int)
        frame["returns"] = frame["close"].pct_change()
        frame["velocity"] = frame["returns"].rolling(window=3).mean()
        frame["rsi"] = SMCEngine.calculate_rsi(frame, window=14)
        frame["rsi_slope"] = frame["rsi"].diff(periods=3)
        frame["price_slope"] = frame["close"].diff(periods=3)
        frame["bear_div"] = ((frame["price_slope"] > 0) & (frame["rsi_slope"] < 0)).astype(int)
        frame["atr"] = SMCEngine.calculate_atr(frame, window=14)
        frame["relative_atr"] = frame["atr"] / frame["close"]
        frame["high_24h"] = frame["high"].rolling(window=288).max()
        frame["dist_to_high"] = (frame["high_24h"] - frame["close"]) / frame["close"]
        frame["vol_zscore"] = (frame["vol"] - frame["vol"].rolling(20).mean()) / frame["vol"].rolling(20).std()
        frame.dropna(inplace=True)
        return frame

    @staticmethod
    def get_feature_names() -> list[str]:
        return [
            "hour",
            "day_of_week",
            "msb_long",
            "msb_short",
            "returns",
            "velocity",
            "rsi",
            "bear_div",
            "relative_atr",
            "dist_to_high",
            "vol_zscore",
        ]


SMC_Engine = SMCEngine
