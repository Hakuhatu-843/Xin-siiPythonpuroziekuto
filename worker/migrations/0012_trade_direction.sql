-- Direction-aware records for transparent demand scoring.
-- Existing records remain NULL because their direction cannot be inferred safely.
ALTER TABLE trade_characters ADD COLUMN direction TEXT CHECK (direction IN ('acquired', 'given') OR direction IS NULL);
ALTER TABLE exchange_characters ADD COLUMN direction TEXT CHECK (direction = 'acquired' OR direction IS NULL);
ALTER TABLE market_snapshots ADD COLUMN acquired_count INTEGER;
ALTER TABLE market_snapshots ADD COLUMN exchange_acquired_count INTEGER;
ALTER TABLE market_snapshots ADD COLUMN given_count INTEGER;
ALTER TABLE market_snapshots ADD COLUMN demand_delta INTEGER;
