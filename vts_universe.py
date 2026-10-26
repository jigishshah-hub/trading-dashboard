"""
vts_universe.py — Nifty 200 symbol list.

NSE tickers as used by yfinance (no .NS suffix here; scanner appends it).
Source: NSE India Nifty 200 index constituents (last verified Oct 2026).
Update this list when NSE rebalances the index (typically semi-annually).
"""

NIFTY_200 = [
    # ── Financials ──────────────────────────────────────────────────────────
    "HDFCBANK", "ICICIBANK", "KOTAKBANK", "AXISBANK", "SBIN",
    "BAJFINANCE", "BAJAJFINSV", "HDFCLIFE", "SBILIFE", "ICICIGI",
    "INDUSINDBK", "BANDHANBNK", "FEDERALBNK", "IDFCFIRSTB", "PNB",
    "CANBK", "BANKBARODA", "CHOLAFIN", "MUTHOOTFIN", "M&MFIN",

    # ── Information Technology ───────────────────────────────────────────────
    "TCS", "INFY", "WIPRO", "HCLTECH", "TECHM",
    "LTIM", "PERSISTENT", "MPHASIS", "COFORGE", "OFSS",

    # ── Consumer Staples / FMCG ─────────────────────────────────────────────
    "HINDUNILVR", "ITC", "NESTLEIND", "DABUR", "MARICO",
    "GODREJCP", "BRITANNIA", "TATACONSUM", "COLPAL", "EMAMILTD",

    # ── Consumer Discretionary ──────────────────────────────────────────────
    "TITAN", "TRENT", "DMART", "ABFRL", "PAGEIND",
    "WHIRLPOOL", "BATA", "RELAXO", "VBL", "MCDOWELL-N",

    # ── Automobile ──────────────────────────────────────────────────────────
    "MARUTI", "TATAMOTORS", "BAJAJ-AUTO", "HEROMOTOCO", "EICHERMOT",
    "BOSCHLTD", "MOTHERSON", "BALKRISIND", "APOLLOTYRE", "CEAT",

    # ── Pharmaceuticals & Healthcare ────────────────────────────────────────
    "SUNPHARMA", "DRREDDY", "CIPLA", "DIVISLAB", "TORNTPHARM",
    "LUPIN", "AUROPHARMA", "ALKEM", "APOLLOHOSP", "MAXHEALTH",
    "FORTIS", "IPCALAB", "ABBOTINDIA", "PFIZER", "SANOFI",

    # ── Energy ──────────────────────────────────────────────────────────────
    "RELIANCE", "ONGC", "NTPC", "POWERGRID", "COALINDIA",
    "TATAPOWER", "ADANIGREEN", "ADANIPOWER", "TORNTPOWER", "CESC",
    "PETRONET", "GAIL", "IGL", "MGL", "BPCL",

    # ── Metals & Mining ─────────────────────────────────────────────────────
    "HINDALCO", "TATASTEEL", "JSWSTEEL", "VEDL", "NMDC",
    "HINDZINC", "NATIONALUM", "SAIL", "JINDALSTEL", "APL",

    # ── Capital Goods / Industrials ─────────────────────────────────────────
    "LT", "BHEL", "ABB", "SIEMENS", "HAVELLS",
    "VOLTAS", "CROMPTON", "POLYCAB", "CUMMINSIND", "THERMAX",
    "TIINDIA", "GRINDWELL", "SCHAEFFLER", "SKFINDIA",

    # ── Cement ──────────────────────────────────────────────────────────────
    "ULTRACEMCO", "SHREECEM", "AMBUJACEM", "ACC", "RAMCOCEM",
    "DALMIACEM", "JKCEMENT", "HEIDELBERG",

    # ── Telecom ─────────────────────────────────────────────────────────────
    "BHARTIARTL", "INDUSTOWER",

    # ── Infrastructure / Real Estate ────────────────────────────────────────
    "ADANIPORTS", "ADANIENT", "DLF", "PRESTIGE", "GODREJPROP",
    "OBEROIRLTY", "PHOENIXLTD",

    # ── Chemicals & Specialty ───────────────────────────────────────────────
    "PIDILITIND", "ASTRAL", "AAVAS", "ALKYLAMINE", "DEEPAKNTR",
    "ATUL", "NAVINFLUOR", "VINATIORGA", "CLEAN",

    # ── Media / Others ──────────────────────────────────────────────────────
    "ZOMATO", "PAYTM", "NYKAA", "POLICYBZR", "DELHIVERY",
    "IRCTC", "CONCOR", "LTTS", "KPITTECH", "TATAELXSI",

    # ── Asset Management / Exchanges ────────────────────────────────────────
    "BSE", "CDSL", "CAMS", "ANGELONE", "360ONE",
]

# yfinance symbol = NSE_SYMBOL + ".NS"
def yf_symbols() -> list[str]:
    return [s + ".NS" for s in NIFTY_200]
