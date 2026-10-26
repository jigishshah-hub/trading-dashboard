-- VTS Scanner: create vts_scores table
-- Run once against the Supabase project (egfsjboyzajyemjqazot).

CREATE TABLE IF NOT EXISTS vts_scores (
    id          bigserial        PRIMARY KEY,
    scan_date   date             NOT NULL,
    symbol      text             NOT NULL,
    vts         numeric(6,2)     NOT NULL,
    varb_spread numeric(6,2)     NOT NULL,
    signal      text             NOT NULL,  -- STRONG_BUY | BUY | WATCH | AVOID
    entry_price numeric(12,4),
    stop_price  numeric(12,4),
    target_1    numeric(12,4),
    atr20       numeric(12,4),
    ema20       numeric(12,4),
    vol_ratio   numeric(6,2),
    regime      text             NOT NULL,  -- risk_on | selective | risk_off
    created_at  timestamptz      NOT NULL DEFAULT now(),

    UNIQUE (scan_date, symbol)
);

CREATE INDEX IF NOT EXISTS vts_scores_date_vts
    ON vts_scores (scan_date DESC, vts DESC);

-- Optional: keep only last 90 scan days to control table size.
-- Enable if disk usage becomes a concern.
-- CREATE UNIQUE INDEX ... + a pg_cron job, or handle in scanner.
