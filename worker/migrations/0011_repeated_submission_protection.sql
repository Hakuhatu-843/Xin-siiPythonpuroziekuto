-- Track identical valid submissions by IP for a rolling 10-minute window.
-- Four matching submissions automatically add the IP to blocked_ips.
CREATE TABLE IF NOT EXISTS repeated_submissions (
  ip TEXT NOT NULL,
  fingerprint TEXT NOT NULL,
  hit_count INTEGER NOT NULL DEFAULT 1 CHECK (hit_count >= 1),
  first_seen TEXT NOT NULL,
  last_seen TEXT NOT NULL,
  PRIMARY KEY (ip, fingerprint)
);
CREATE INDEX IF NOT EXISTS idx_repeated_submissions_last_seen
  ON repeated_submissions(last_seen);
