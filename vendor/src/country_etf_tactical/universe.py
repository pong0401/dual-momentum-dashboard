"""Ticker universe definitions for the Country ETF Tactical + Gold DD Boost 16 strategy."""

SPY = "SPY"
GOLD = "GC=F"
BTC = "BTC-USD"
BIL = "BIL"
IEF = "IEF"

BASE_ASSETS = [SPY, GOLD, BTC, BIL]

# Loaded per spec but never used as a direct target weight.
AUX_PRICE_ONLY = [IEF]

MOMENTUM_UNIVERSE = ["SPMO", "MTUM", "SCHG", "XLK", "EWY", "EWJ", "INDA"]

# 11 SPDR Select Sector ETFs (XLC/XLRE are the youngest, inception 2018/2015 -- naturally
# excluded pre-inception by the eligibility mask's min-history/NaN handling, same as any
# other late-inception ticker in this repo).
SECTOR_ETFS = [
    "XLK", "XLF", "XLE", "XLV", "XLY", "XLP", "XLI", "XLU", "XLB", "XLRE", "XLC",
]

# Broad-market growth ETFs beyond the existing factor set (SPMO/MTUM/SCHG above).
GROWTH_ETFS = ["QQQ", "VUG", "IWF", "SPYG", "VGT"]

# Alternative momentum-sleeve candidate pool: sector + growth ETFs, explicitly excluding
# single-country bets (EWY/EWJ/INDA in MOMENTUM_UNIVERSE) since country exposure already
# has its own dedicated sleeve (COUNTRY_UNIVERSE below).
SECTOR_GROWTH_UNIVERSE = sorted(set(SECTOR_ETFS) | set(GROWTH_ETFS) | {"SPMO", "MTUM", "SCHG"})

# SECTOR_GROWTH_UNIVERSE minus the 11 SPDR sector ETFs. Tested head-to-head as the ETF
# sleeve in the dual-momentum strategy: this smaller, growth/factor-only pool beat the
# sector-inclusive one on every metric (CAGR/Sharpe/Calmar) at its own optimal top-K --
# the sector ETFs (defensive ones like XLU/XLP especially) rarely rank near the top of a
# momentum sort and mostly diluted the eligible pool rather than adding candidates worth
# picking. This is the pool the dual-momentum script defaults to.
GROWTH_FACTOR_UNIVERSE = sorted(set(GROWTH_ETFS) | {"SPMO", "MTUM", "SCHG"})

COUNTRY_UNIVERSE = [
    "EWC", "EWA", "EWW", "EWZ", "ARGT", "ECH", "EPU", "GXG", "EWG", "EWU",
    "EWQ", "EWL", "EWP", "EWI", "EWN", "EWD", "EDEN", "EIRL", "NORW", "EPOL",
    "GREK", "TUR", "EWJ", "DXJ", "EWT", "EWY", "INDA", "EPI", "MCHI", "KWEB",
    "EWS", "EWM", "EIDO", "VNM", "THD", "ENZL", "PAK", "KSA", "QAT", "UAE",
    "EIS", "EZA", "EGPT",
]

ALL_TICKERS = sorted(
    set(BASE_ASSETS) | set(AUX_PRICE_ONLY) | set(MOMENTUM_UNIVERSE) | set(COUNTRY_UNIVERSE)
    | set(SECTOR_GROWTH_UNIVERSE)
)

COUNTRY_MIN_HISTORY_DAYS = 2520  # ~10 years of trading days
