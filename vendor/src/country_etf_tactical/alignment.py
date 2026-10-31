"""Calendar alignment: map every ticker's price series onto a single master trading calendar."""
from __future__ import annotations

import pandas as pd

from .universe import SPY


def align_to_master_calendar(prices: pd.DataFrame) -> pd.DataFrame:
    """Use SPY's trading calendar as the master; forward-fill everything else onto it."""
    master_index = prices[SPY].dropna().index
    aligned = prices.reindex(master_index).ffill()
    return aligned
