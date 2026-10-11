ALTER TABLE trades ADD COLUMN submission_id TEXT;
ALTER TABLE trades ADD COLUMN reviewed_by TEXT;
ALTER TABLE trades ADD COLUMN reviewed_at TEXT;
ALTER TABLE trades ADD COLUMN review_token TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_trades_submission_id
  ON trades(submission_id);

CREATE TABLE IF NOT EXISTS trade_review_actions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  trade_id INTEGER NOT NULL UNIQUE
    REFERENCES trades(id),
  status TEXT NOT NULL CHECK (status IN ('approved', 'rejected')),
  reviewer_id TEXT NOT NULL,
  reviewed_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_trade_review_actions_reviewed_at
  ON trade_review_actions(reviewed_at DESC);
