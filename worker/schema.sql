PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS trades (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  trade_type TEXT NOT NULL CHECK (trade_type IN ('single','bundle','set')),
  total_amount INTEGER NOT NULL CHECK (total_amount >= 1),
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected')),
  submitted_at TEXT NOT NULL,
  discord_user_id TEXT,
  client_ip TEXT,
  submission_id TEXT UNIQUE,
  reviewed_by TEXT,
  reviewed_at TEXT
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
  is_verified INTEGER NOT NULL DEFAULT 0 CHECK (is_verified IN (0,1)),
  FOREIGN KEY (trade_id) REFERENCES trades(id) ON DELETE CASCADE,
  UNIQUE (trade_id, position)
);
CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status);
CREATE INDEX IF NOT EXISTS idx_trade_characters_character ON trade_characters(character_id);
CREATE INDEX IF NOT EXISTS idx_trade_characters_verified ON trade_characters(is_verified);
CREATE INDEX IF NOT EXISTS idx_trades_client_ip ON trades(client_ip);

CREATE TABLE IF NOT EXISTS blocked_ips (ip TEXT PRIMARY KEY, blocked_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS market_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  character_id TEXT NOT NULL,
  level1_value INTEGER,
  level_max_value INTEGER,
  demand_score REAL,
  sample_count INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_market_snapshots_character_created ON market_snapshots(character_id, created_at DESC);
CREATE TABLE IF NOT EXISTS market_snapshot_mutations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  snapshot_id INTEGER NOT NULL,
  mutation TEXT NOT NULL,
  multiplier REAL,
  sample_count INTEGER NOT NULL DEFAULT 0,
  FOREIGN KEY (snapshot_id) REFERENCES market_snapshots(id) ON DELETE CASCADE,
  UNIQUE (snapshot_id, mutation)
);
CREATE INDEX IF NOT EXISTS idx_market_snapshot_mutations_snapshot ON market_snapshot_mutations(snapshot_id);

CREATE TABLE IF NOT EXISTS characters (
  character_number TEXT PRIMARY KEY,
  english_name TEXT NOT NULL,
  japanese_name TEXT NOT NULL,
  rarity TEXT NOT NULL CHECK (rarity IN ('common','rare','epic','legend','mythic','secret','ultimate','emperor','ancient')),
  is_tradable INTEGER NOT NULL DEFAULT 1 CHECK (is_tradable IN (0,1))
);

CREATE TABLE IF NOT EXISTS exchanges (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  submission_id TEXT UNIQUE,
  exchange_description TEXT NOT NULL CHECK (length(exchange_description) BETWEEN 1 AND 1000),
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected')),
  submitted_at TEXT NOT NULL,
  client_ip TEXT,
  reviewed_by TEXT,
  reviewed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_exchanges_status_id ON exchanges(status,id);
CREATE INDEX IF NOT EXISTS idx_exchanges_client_ip ON exchanges(client_ip);
CREATE TABLE IF NOT EXISTS exchange_characters (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  exchange_id INTEGER NOT NULL,
  position INTEGER NOT NULL CHECK (position BETWEEN 1 AND 3),
  character_id TEXT NOT NULL,
  rarity TEXT NOT NULL,
  level INTEGER NOT NULL CHECK (level BETWEEN 1 AND 301),
  mutation TEXT NOT NULL,
  quantity INTEGER NOT NULL CHECK (quantity >= 1),
  is_verified INTEGER NOT NULL DEFAULT 0 CHECK (is_verified IN (0,1)),
  FOREIGN KEY (exchange_id) REFERENCES exchanges(id) ON DELETE CASCADE,
  UNIQUE (exchange_id,position)
);
CREATE INDEX IF NOT EXISTS idx_exchange_characters_character ON exchange_characters(character_id);
