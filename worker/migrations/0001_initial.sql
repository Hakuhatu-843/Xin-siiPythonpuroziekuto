CREATE TABLE IF NOT EXISTS trades (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  trade_type TEXT NOT NULL CHECK (trade_type IN ('single', 'bundle', 'set')),
  total_amount INTEGER NOT NULL CHECK (total_amount >= 1),
  status TEXT NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending', 'approved', 'rejected')),
  submitted_at TEXT NOT NULL,
  discord_user_id TEXT
);

CREATE TABLE IF NOT EXISTS trade_characters (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  trade_id INTEGER NOT NULL,
  position INTEGER NOT NULL CHECK (position BETWEEN 1 AND 3),
  character_id TEXT NOT NULL,
  rarity TEXT NOT NULL,
  level INTEGER NOT NULL CHECK (level BETWEEN 1 AND 301),
  mutation TEXT NOT NULL,
  quantity INTEGER NOT NULL CHECK (quantity >= 1),
  is_verified INTEGER NOT NULL DEFAULT 0 CHECK (is_verified IN (0, 1)),
  FOREIGN KEY (trade_id) REFERENCES trades(id) ON DELETE CASCADE,
  UNIQUE (trade_id, position)
);

CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status);
CREATE INDEX IF NOT EXISTS idx_trade_characters_character ON trade_characters(character_id);
CREATE INDEX IF NOT EXISTS idx_trade_characters_verified ON trade_characters(is_verified);
