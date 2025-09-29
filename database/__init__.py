import aiosqlite


class DatabaseManager:
    def __init__(self, *, connection: aiosqlite.Connection) -> None:
        self.connection = connection

    # ---------------- aRPG Timeline - Guild Settings ----------------
    async def get_guild_settings(self, guild_id: int) -> dict:
        rows = await self.connection.execute(
            "SELECT guild_id, notifications_enabled FROM guild_settings WHERE guild_id=?",
            (str(guild_id),),
        )
        async with rows as cursor:
            res = await cursor.fetchone()
            if res is None:
                # Insert default row
                await self.connection.execute(
                    "INSERT OR IGNORE INTO guild_settings(guild_id) VALUES (?)",
                    (str(guild_id),),
                )
                await self.connection.commit()
                return {
                    "guild_id": str(guild_id),
                    "notifications_enabled": 1,
                }
            return {
                "guild_id": res[0],
                "notifications_enabled": res[1],
            }

    async def set_guild_enabled(self, guild_id: int | str, enabled: int) -> None:
        await self.connection.execute(
            "INSERT INTO guild_settings(guild_id, notifications_enabled) VALUES(?, ?) ON CONFLICT(guild_id) DO UPDATE SET notifications_enabled=excluded.notifications_enabled, updated_at=CURRENT_TIMESTAMP",
            (str(guild_id), int(enabled)),
        )
        await self.connection.commit()

    async def get_guild_games(self, guild_id: int | str) -> dict:
        rows = await self.connection.execute(
            "SELECT game_slug, enabled FROM guild_games WHERE guild_id=?",
            (str(guild_id),),
        )
        out: dict[str, int] = {}
        async with rows as cursor:
            async for slug, enabled in cursor:
                out[str(slug)] = int(enabled)
        return out

    async def set_guild_game(self, guild_id: int | str, game_slug: str, enabled: int) -> None:
        await self.connection.execute(
            "INSERT INTO guild_games(guild_id, game_slug, enabled) VALUES(?, ?, ?) ON CONFLICT(guild_id, game_slug) DO UPDATE SET enabled=excluded.enabled",
            (str(guild_id), game_slug, int(enabled)),
        )
        await self.connection.commit()

    # ---------------- aRPG Timeline - Season Cache ----------------
    async def is_season_seen(self, guild_id: int | str, game_slug: str, season_key: str) -> bool:
        rows = await self.connection.execute(
            "SELECT 1 FROM season_cache WHERE guild_id=? AND game_slug=? AND season_key=?",
            (str(guild_id), game_slug, season_key),
        )
        async with rows as cursor:
            return (await cursor.fetchone()) is not None

    async def mark_season_seen(self, guild_id: int | str, game_slug: str, season_key: str) -> None:
        await self.connection.execute(
            "INSERT OR IGNORE INTO season_cache(guild_id, game_slug, season_key) VALUES(?, ?, ?)",
            (str(guild_id), game_slug, season_key),
        )
        await self.connection.commit()

    # ---------------- API Tokens (persistent) ----------------
    async def get_api_token(self, key: str) -> tuple[str | None, str | None]:
        """Return (token, expires_at_iso) for the given key or (None, None)."""
        rows = await self.connection.execute(
            "SELECT token, expires_at FROM api_tokens WHERE key=?",
            (key,),
        )
        async with rows as cursor:
            res = await cursor.fetchone()
            if not res:
                return None, None
            return res[0], res[1]

    async def set_api_token(self, key: str, token: str, expires_at_iso: str | None) -> None:
        await self.connection.execute(
            "INSERT INTO api_tokens(key, token, expires_at, updated_at) VALUES(?, ?, ?, CURRENT_TIMESTAMP) "
            "ON CONFLICT(key) DO UPDATE SET token=excluded.token, expires_at=excluded.expires_at, updated_at=CURRENT_TIMESTAMP",
            (key, token, expires_at_iso),
        )
        await self.connection.commit()

    async def get_latest_api_token(self) -> tuple[str | None, str | None, str | None]:
        """Return (key, token, expires_at_iso) of the most recent token, or (None, None, None)."""
        rows = await self.connection.execute(
            "SELECT key, token, expires_at FROM api_tokens "
            "WHERE token IS NOT NULL AND token != '' "
            "ORDER BY (CASE WHEN expires_at IS NULL THEN 1 ELSE 0 END), datetime(expires_at) DESC "
            "LIMIT 1"
        )
        async with rows as cursor:
            res = await cursor.fetchone()
            if not res:
                return None, None, None
            return res[0], res[1], res[2]

    # ---------------- Generic API Cache ----------------
    async def get_api_cache(self, key: str) -> tuple[str | None, str | None]:
        """Return (value_json, expires_at_iso) for key, or (None, None)."""
        rows = await self.connection.execute(
            "SELECT value, expires_at FROM api_cache WHERE key=?",
            (key,),
        )
        async with rows as cursor:
            res = await cursor.fetchone()
            if not res:
                return None, None
            return res[0], res[1]

    async def set_api_cache(self, key: str, value_json: str, expires_at_iso: str | None) -> None:
        await self.connection.execute(
            "INSERT INTO api_cache(key, value, expires_at, updated_at) VALUES(?, ?, ?, CURRENT_TIMESTAMP) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, expires_at=excluded.expires_at, updated_at=CURRENT_TIMESTAMP",
            (key, value_json, expires_at_iso),
        )
        await self.connection.commit()
