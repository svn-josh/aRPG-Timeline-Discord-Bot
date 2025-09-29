-- Guild-specific settings for aRPG Timeline notifications (lean schema)
CREATE TABLE IF NOT EXISTS `guild_settings` (
  `guild_id` text PRIMARY KEY,
  `notifications_enabled` integer NOT NULL DEFAULT 1, -- master enable/disable
  `created_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Per-guild per-game enable/disable toggles
CREATE TABLE IF NOT EXISTS `guild_games` (
  `guild_id` varchar(20) NOT NULL,
  `game_slug` varchar(50) NOT NULL,
  `enabled` integer NOT NULL DEFAULT 1,
  PRIMARY KEY (`guild_id`, `game_slug`)
);

-- Cache of seasons already notified to avoid duplicates (per guild)
CREATE TABLE IF NOT EXISTS `season_cache` (
  `guild_id` varchar(20) NOT NULL,
  `game_slug` varchar(50) NOT NULL,
  `season_key` varchar(200) NOT NULL, -- season id/slug or computed key
  `notified_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`guild_id`, `game_slug`, `season_key`)
);

-- Persistent store for API tokens with expiry
CREATE TABLE IF NOT EXISTS `api_tokens` (
  `key` text PRIMARY KEY,
  `token` text,
  `expires_at` text, -- ISO 8601 timestamp in UTC
  `updated_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Generic API cache storage
CREATE TABLE IF NOT EXISTS `api_cache` (
  `key` text PRIMARY KEY,
  `value` text, -- JSON payload
  `expires_at` text, -- ISO 8601 timestamp in UTC
  `updated_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP
);