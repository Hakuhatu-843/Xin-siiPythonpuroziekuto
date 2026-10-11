ALTER TABLE trades ADD COLUMN client_ip TEXT;

CREATE TABLE IF NOT EXISTS blocked_ips (
  ip TEXT PRIMARY KEY,
  blocked_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_trades_client_ip ON trades(client_ip);
