CREATE TABLE IF NOT EXISTS exchange_submissions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  submission_id TEXT NOT NULL UNIQUE,
  exchange_description TEXT NOT NULL
    CHECK (length(trim(exchange_description)) BETWEEN 1 AND 1000),
  status TEXT NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending', 'approved', 'rejected')),
  submitted_at TEXT NOT NULL,
  client_ip TEXT,
  reviewed_by TEXT,
  reviewed_at TEXT,
  review_token TEXT
);

CREATE INDEX IF NOT EXISTS idx_exchange_submissions_status
  ON exchange_submissions(status);

CREATE INDEX IF NOT EXISTS idx_exchange_submissions_client_ip
  ON exchange_submissions(client_ip);

CREATE TABLE IF NOT EXISTS exchange_submission_characters (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  submission_id INTEGER NOT NULL
    REFERENCES exchange_submissions(id) ON DELETE CASCADE,
  position INTEGER NOT NULL CHECK (position BETWEEN 1 AND 3),
  character_id TEXT NOT NULL
    REFERENCES market_character_names(character_id),
  rarity TEXT NOT NULL,
  level INTEGER NOT NULL CHECK (level BETWEEN 1 AND 301),
  mutation TEXT NOT NULL,
  quantity INTEGER NOT NULL CHECK (quantity BETWEEN 1 AND 9999),
  UNIQUE (submission_id, position)
);

CREATE INDEX IF NOT EXISTS idx_exchange_submission_characters_submission
  ON exchange_submission_characters(submission_id);

CREATE TABLE IF NOT EXISTS exchange_review_actions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  submission_id INTEGER NOT NULL UNIQUE
    REFERENCES exchange_submissions(id),
  status TEXT NOT NULL CHECK (status IN ('approved', 'rejected')),
  reviewer_id TEXT NOT NULL,
  reviewed_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_exchange_review_actions_reviewed_at
  ON exchange_review_actions(reviewed_at DESC);
