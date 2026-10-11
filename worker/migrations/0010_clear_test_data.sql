-- Owner-authorized cleanup: remove all existing test submissions and derived data.
DELETE FROM market_snapshot_mutations;
DELETE FROM market_snapshots;
DELETE FROM exchange_characters;
DELETE FROM exchanges;
DELETE FROM exchange_submission_characters;
DELETE FROM exchange_review_actions;
DELETE FROM exchange_submissions;
DELETE FROM trade_review_actions;
DELETE FROM trade_characters;
DELETE FROM trades;
DELETE FROM blocked_ips;
DELETE FROM market_character_numbers;
DELETE FROM market_character_names;
