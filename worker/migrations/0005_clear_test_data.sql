-- One-time cleanup requested by the owner: the existing records are test-only.
DELETE FROM blocked_ips;
DELETE FROM market_snapshot_mutations;
DELETE FROM market_snapshots;
DELETE FROM exchange_characters;
DELETE FROM exchanges;
DELETE FROM trade_characters;
DELETE FROM trades;
