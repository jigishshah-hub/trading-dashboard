-- VTS Scanner: create vts_scores table
-- Run once against the Supabase project (egfsjboyzajyemjqazot).

CREATE TABLE IF NOT EXISTS vts_scores (
    id               bigserial        PRIMARY KEY,
    scan_date        date             NOT NULL,
    symbol           text             NOT NULL,

    -- Composite score and components
    vts              numeric(6,2)     NOT NULL,    -- 0–100 composite
    rs_score         numeric(6,2),                 -- relative strength vs Nifty (40%)
    slope_score      numeric(6,2),                 -- ATR-normalised EMA slope (35%)
    setup_score      numeric(6,2),                 -- ATR compression + near high (25%)

    -- Signal and VARB
    varb_spread      numeric(6,2)     NOT NULL,    -- stock_realvol - India_VIX
    signal           text             NOT NULL,    -- STRONG_BUY | BUY | WATCH | AVOID

    -- Entry / exit levels
    entry_price      numeric(12,4),
    stop_price       numeric(12,4),
    target_1         numeric(12,4),

    -- Indicators
    atr20            numeric(12,4),
    ema20            numeric(12,4),
    atr_compression  numeric(6,3),                 -- 0=expanded, 1=fully coiled
    near_high        boolean,                      -- close within 5% of 60-day high
    vol_ratio        numeric(6,2),                 -- today vol / 20-day avg vol

    -- Context
    regime           text             NOT NULL,    -- risk_on | selective | risk_off
    created_at       timestamptz      NOT NULL DEFAULT now(),

    UNIQUE (scan_date, symbol)
);

CREATE INDEX IF NOT EXISTS vts_scores_date_vts
    ON vts_scores (scan_date DESC, vts DESC);

CREATE INDEX IF NOT EXISTS vts_scores_signal
    ON vts_scores (scan_date DESC, signal);
