ALTER TABLE market_snapshots
  ADD COLUMN level1_sample_count INTEGER
  CHECK (level1_sample_count IS NULL OR level1_sample_count >= 0);

ALTER TABLE market_snapshots
  ADD COLUMN level_max_sample_count INTEGER
  CHECK (level_max_sample_count IS NULL OR level_max_sample_count >= 0);
