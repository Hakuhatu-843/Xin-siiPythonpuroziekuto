PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS trades (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  trade_type TEXT NOT NULL CHECK (trade_type IN ('single', 'bundle', 'set')),
  total_amount INTEGER NOT NULL CHECK (total_amount >= 1),
  status TEXT NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending', 'approved', 'rejected')),
  submitted_at TEXT NOT NULL,
  discord_user_id TEXT,
  client_ip TEXT,
  submission_id TEXT,
  reviewed_by TEXT,
  reviewed_at TEXT,
  review_token TEXT
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

CREATE INDEX IF NOT EXISTS idx_trades_status
  ON trades(status);

CREATE INDEX IF NOT EXISTS idx_trade_characters_character
  ON trade_characters(character_id);

CREATE INDEX IF NOT EXISTS idx_trade_characters_verified
  ON trade_characters(is_verified);

CREATE INDEX IF NOT EXISTS idx_trades_client_ip
  ON trades(client_ip);

CREATE UNIQUE INDEX IF NOT EXISTS idx_trades_submission_id
  ON trades(submission_id);

CREATE TABLE IF NOT EXISTS blocked_ips (
  ip TEXT PRIMARY KEY,
  blocked_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS market_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  character_id TEXT NOT NULL,
  level1_value INTEGER,
  level_max_value INTEGER,
  demand_score REAL,
  sample_count INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  level1_sample_count INTEGER
    CHECK (level1_sample_count IS NULL OR level1_sample_count >= 0),
  level_max_sample_count INTEGER
    CHECK (level_max_sample_count IS NULL OR level_max_sample_count >= 0)
);

CREATE INDEX IF NOT EXISTS idx_market_snapshots_character_created
  ON market_snapshots(character_id, created_at DESC);

CREATE TABLE IF NOT EXISTS market_snapshot_mutations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  snapshot_id INTEGER NOT NULL,
  mutation TEXT NOT NULL,
  multiplier REAL,
  sample_count INTEGER NOT NULL DEFAULT 0,
  FOREIGN KEY (snapshot_id) REFERENCES market_snapshots(id) ON DELETE CASCADE,
  UNIQUE (snapshot_id, mutation)
);

CREATE INDEX IF NOT EXISTS idx_market_snapshot_mutations_snapshot
  ON market_snapshot_mutations(snapshot_id);

CREATE TABLE IF NOT EXISTS market_character_names (
  character_id TEXT PRIMARY KEY,
  character_name TEXT NOT NULL UNIQUE
);

INSERT OR IGNORE INTO market_character_names (character_id, character_name) VALUES
  ('c001', 'スライム'),
  ('c002', 'ゴブリン'),
  ('c003', 'コウモリ'),
  ('c004', 'キノコ'),
  ('r001', 'オーク'),
  ('r002', 'ウルフ'),
  ('r003', 'ガーゴイル'),
  ('r004', 'ハーピー'),
  ('e001', 'ミノタウロス'),
  ('e002', 'セイレーン'),
  ('e003', 'ゴーレム'),
  ('e004', 'ヴァンパイア'),
  ('l001', 'フェニックス'),
  ('l002', 'グリフォン'),
  ('l003', 'ユニコーン'),
  ('l004', 'ケルベロス'),
  ('m001', 'リヴァイアサン'),
  ('m002', 'ベヒーモス'),
  ('m003', 'ジャバウォック'),
  ('m004', 'クラーケン'),
  ('s001', '影の王'),
  ('s002', '星詠み'),
  ('s003', '虚無の番人'),
  ('s004', '月影の姫'),
  ('u001', '黄金の守護者'),
  ('u002', '光輪の聖騎士'),
  ('u003', '創世の巨神'),
  ('u004', '星海の覇龍'),
  ('x001', '天帝竜'),
  ('x002', '冥府の皇帝'),
  ('x003', '時の覇者'),
  ('x004', '終焉の女帝');

CREATE TABLE IF NOT EXISTS market_character_numbers (
  character_number TEXT PRIMARY KEY
    CHECK (character_number GLOB '[0-9][0-9][0-9]'),
  character_id TEXT NOT NULL UNIQUE
    REFERENCES market_character_names(character_id)
);

INSERT OR IGNORE INTO market_character_numbers (character_number, character_id) VALUES
  ('001', 'c002'),
  ('002', 'l001');

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
