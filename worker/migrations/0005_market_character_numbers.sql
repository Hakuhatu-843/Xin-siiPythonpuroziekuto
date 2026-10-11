CREATE TABLE IF NOT EXISTS market_character_numbers (
  character_number TEXT PRIMARY KEY
    CHECK (character_number GLOB '[0-9][0-9][0-9]'),
  character_id TEXT NOT NULL UNIQUE
    REFERENCES market_character_names(character_id)
);

INSERT OR IGNORE INTO market_character_numbers (character_number, character_id) VALUES
  ('001', 'c002'),
  ('002', 'l001');
