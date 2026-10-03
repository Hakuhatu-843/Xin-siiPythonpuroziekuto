-- TEST ONLY: 仮相場データ。実運用DBには投入しない。
-- 削除: このファイルを消すだけで仮データ定義を撤去できます。
-- 実際のDBへの投入は、以下をCodespacesで一度だけ実行してください。
-- sqlite3 python-app/data/trades.sqlite3 < python-app/data/test_market_seed.sql

PRAGMA foreign_keys = ON;

INSERT INTO trades
(character_name, mutation, quantity, total_amount, transaction_type, registered_at, registered_by_discord_user_id, level, is_verified)
VALUES
('A','通常',1,'1000','単体','2026-10-03T00:00:00+00:00','TEST','1',1),
('A','通常',1,'10000','単体','2026-10-03T00:01:00+00:00','TEST','100',1),
('A','ユグ',1,'1500','単体','2026-10-03T00:02:00+00:00','TEST','1',1),
('A','ダイヤ',1,'3000','単体','2026-10-03T00:03:00+00:00','TEST','1',1),
('B','通常',1,'2000','単体','2026-10-03T00:04:00+00:00','TEST','1',1),
('B','通常',1,'8000','単体','2026-10-03T00:05:00+00:00','TEST','80',1),
('B','アクア',1,'4000','単体','2026-10-03T00:06:00+00:00','TEST','1',1),
('B','ゴールド',1,'10000','単体','2026-10-03T00:07:00+00:00','TEST','1',1);
