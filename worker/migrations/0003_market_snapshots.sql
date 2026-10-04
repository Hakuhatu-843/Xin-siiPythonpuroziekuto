CREATE TABLE IF NOT EXISTS market_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  character_id TEXT NOT NULL,
  level1_value INTEGER,
  level_max_value INTEGER,
  demand_score REAL,
  sample_count INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
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
