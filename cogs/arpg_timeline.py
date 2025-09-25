import asyncio
from datetime import timedelta
from typing import List, Optional, Tuple

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

from services.arpg_api import ARPGApiClient, Season

class ARPGTimeline(commands.Cog, name="arpg"):
    """Tracks ARPG seasons using the aRPG Timeline API."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.session: Optional[aiohttp.ClientSession] = None
        # API client handles tokens, caching, and HTTP
        self.api = ARPGApiClient(session=self.session, db=getattr(bot, "database", None), logger=self.bot.logger)
        self.poll_seasons_task.start()

    def cog_unload(self) -> None:
        self.poll_seasons_task.cancel()
        if self.session and not self.session.closed:
            asyncio.create_task(self.session.close())

    async def get_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
            # keep API client session in sync
            self.api.session = self.session
        return self.session

    async def _fetch_token(self) -> Optional[Tuple[str, object]]:
        # Delegate to service for future compatibility; retained for any internal uses
        return await self.api._fetch_token()

    async def _get_access_token(self) -> Optional[str]:
        return await self.api._get_access_token()

    async def fetch_active_seasons(self) -> List[Season]:
        # Delegate to API client cached seasons (standard TTL)
        return await self.api.get_cached_active_seasons()

    # 5-minute poll
    @tasks.loop(minutes=5.0)
    async def poll_seasons_task(self) -> None:
        await self.bot.wait_until_ready()
        # Ensure database is set
        if not getattr(self.bot, "database", None):
            return

        try:
            seasons = await self.fetch_active_seasons()
        except Exception as e:
            self.bot.logger.error(f"Failed to fetch seasons: {e}")
            return

        if not seasons:
            return

        # Iterate guilds and notify based on settings
        for guild in self.bot.guilds:
            try:
                await self._process_guild(guild, seasons)
            except Exception as e:
                self.bot.logger.error(f"Error processing guild {guild.id}: {e}")

    async def _process_guild(self, guild: discord.Guild, seasons: List[Season]) -> None:
        db = self.bot.database
        settings = await db.get_guild_settings(guild.id)
        if not settings or int(settings["notifications_enabled"]) != 1:
            return
        # Per-game toggles now default OFF unless explicitly enabled (value=1)
        game_toggles = await db.get_guild_games(guild.id)

        now = discord.utils.utcnow()
        for s in seasons:
            # Skip if this game's toggle is not explicitly enabled (default is disabled)
            if not game_toggles.get(s.game_slug, 0):
                self.bot.logger.debug(
                    f"guild={guild.id} game={s.game_slug} season_key={s.season_key} skip=toggle_off"
                )
                continue

            seen = await db.is_season_seen(guild.id, s.game_slug, s.season_key)
            if seen:
                self.bot.logger.debug(
                    f"guild={guild.id} game={s.game_slug} season_key={s.season_key} skip=already_seen"
                )
                continue

            # Determine state: upcoming if start time in future, started otherwise.
            is_upcoming = bool(s.starts_at and s.starts_at > now)

            # If already started a long time (>1 day) ago and not seen (initial bootstrap) mark as seen silently.
            if not is_upcoming and s.starts_at and now - s.starts_at > timedelta(days=1):
                await db.mark_season_seen(guild.id, s.game_slug, s.season_key)
                self.bot.logger.info(
                    f"guild={guild.id} game={s.game_slug} season_key={s.season_key} action=bootstrap_mark past_days>1"
                )
                continue

            if is_upcoming:
                # Always operate in event-only mode: attempt to create an event for upcoming seasons.
                self.bot.logger.info(
                    f"guild={guild.id} game={s.game_slug} season_key={s.season_key} action=create_event starts_at={s.starts_at}"
                )
                created = await self._create_event_for_season(guild, s)
                if created:
                    await db.mark_season_seen(guild.id, s.game_slug, s.season_key)
                    self.bot.logger.info(
                        f"guild={guild.id} game={s.game_slug} season_key={s.season_key} action=event_created"
                    )
                else:
                    # Leave unmarked so we retry next poll in case of temporary failure (permissions, outage, etc.).
                    self.bot.logger.warning(
                        f"guild={guild.id} game={s.game_slug} season_key={s.season_key} action=event_failed will_retry=1"
                    )
                    pass
            else:
                # Season already started; since we only create events for future starts we mark it as seen silently.
                await db.mark_season_seen(guild.id, s.game_slug, s.season_key)
                self.bot.logger.info(
                    f"guild={guild.id} game={s.game_slug} season_key={s.season_key} action=mark_seen started_already"
                )
    # Message/embed sending removed: bot now operates strictly in scheduled-event mode.

    async def _create_event_for_season(self, guild: discord.Guild, s: Season) -> bool:
        # Create an external scheduled event if start time is in the future; allow any future start
        now = discord.utils.utcnow()
        start = s.starts_at
        if not start or start <= now:
            return False
        end = start + timedelta(hours=2)
        name = f"{s.game_name}: {s.title}"
        description = s.url or "New season tracked by aRPG Timeline"
        try:
            await guild.create_scheduled_event(
                name=name,
                start_time=start,
                end_time=end,
                privacy_level=discord.PrivacyLevel.guild_only,
                entity_type=discord.EntityType.external,
                location="aRPG Timeline",
                description=description,
            )
            return True
        except Exception as e:
            self.bot.logger.error(
                f"guild={guild.id} game={s.game_slug} season_key={s.season_key} action=create_event_error error={e}"
            )
            return False

    # ------------- Commands (guild owner only) -------------
    def _ensure_guild_owner(self, interaction: discord.Interaction) -> Tuple[bool, Optional[str]]:
        if not interaction.guild:
            return False, "This command can only be used in a server."
        if interaction.user.id != interaction.guild.owner_id:
            return False, "Only the server owner can use this command."
        return True, None


    @app_commands.command(name="arpg-enable", description="Enable or disable all season notifications")
    @app_commands.describe(enabled="Enable or disable all notifications for this server")
    async def set_enabled(self, interaction: discord.Interaction, enabled: bool):
        """
        Globally enable or disable all season notifications for this guild.

        Disabling keeps configuration (channel, mode, toggles) intact but suppresses new season announcements.

        :param interaction: The application command interaction context.
        :param enabled: True to enable notifications, False to disable.
        """
        ok, err = self._ensure_guild_owner(interaction)
        if not ok:
            await interaction.response.send_message(err, ephemeral=False)
            return
        await self.bot.database.set_guild_enabled(interaction.guild_id, 1 if enabled else 0)  # type: ignore[arg-type]
        await interaction.response.send_message(f"Notifications {'enabled' if enabled else 'disabled'}.", ephemeral=False)


    @app_commands.command(name="arpg-toggle-game", description="Interactively enable/disable games (default is off)")
    async def toggle_game(self, interaction: discord.Interaction):
        """
        Interactive dropdown to toggle games on/off. All games are OFF by default unless explicitly enabled.

        Provides pagination plus Enable All / Disable All bulk actions.

        :param interaction: The application command interaction context.
        """
        ok, err = self._ensure_guild_owner(interaction)
        if not ok:
            await interaction.response.send_message(err, ephemeral=True)
            return

        try:
            games = await self.api.get_cached_games()
        except Exception:
            games = []
        if not games:
            await interaction.response.send_message("No games available (cache empty). Try again later.", ephemeral=True)
            return

        toggles = await self.bot.database.get_guild_games(interaction.guild_id)  # type: ignore[arg-type]

        class GameToggleView(discord.ui.View):
            def __init__(self, parent: discord.Interaction, games_list: List[Season], toggle_map: dict[str, int]):
                super().__init__(timeout=120)
                self.parent = parent
                self.games_list = games_list
                self.toggle_map = toggle_map
                self.page = 0
                self.page_size = 25
                self.select: Optional[discord.ui.Select] = None
                self.build_select()
                self.update_buttons_state()

            # --- Building ---
            def build_options(self) -> List[discord.SelectOption]:
                start = self.page * self.page_size
                segment = self.games_list[start:start + self.page_size]
                opts: List[discord.SelectOption] = []
                for g in segment:
                    enabled = self.toggle_map.get(g.slug, 0)
                    emoji = "✅" if enabled else "❌"
                    desc = f"{g.slug} - {'on' if enabled else 'off'}"
                    opts.append(discord.SelectOption(label=g.name[:100], value=g.slug, description=desc[:100], emoji=emoji))
                return opts

            def build_select(self) -> None:
                if self.select:
                    self.remove_item(self.select)
                view_ref = self
                options = self.build_options()

                class GameSelect(discord.ui.Select):
                    def __init__(self):
                        super().__init__(placeholder="Select a game to toggle", min_values=1, max_values=1, options=options)

                    async def callback(self_inner, i: discord.Interaction):  # type: ignore[override]
                        slug = self_inner.values[0]
                        current = view_ref.toggle_map.get(slug, 0)
                        new_val = 0 if current else 1
                        await view_ref.parent.client.database.set_guild_game(view_ref.parent.guild_id, slug, new_val)  # type: ignore[arg-type]
                        view_ref.toggle_map[slug] = new_val
                        view_ref.build_select()
                        view_ref.update_buttons_state()
                        await i.response.edit_message(content=f"Toggled {slug}: {'enabled' if new_val else 'disabled'}", view=view_ref)

                self.select = GameSelect()
                self.add_item(self.select)

            def update_buttons_state(self):
                total_pages = (len(self.games_list) - 1) // self.page_size
                for child in self.children:
                    if isinstance(child, discord.ui.Button):
                        if child.custom_id == "prev_page":
                            child.disabled = self.page <= 0
                        elif child.custom_id == "next_page":
                            child.disabled = self.page >= total_pages

            # --- Buttons ---
            @discord.ui.button(label="Prev", style=discord.ButtonStyle.secondary, custom_id="prev_page", row=1)
            async def prev_page(self, i: discord.Interaction, button: discord.ui.Button):  # type: ignore[override]
                if button.disabled:
                    await i.response.defer()
                    return
                if self.page > 0:
                    self.page -= 1
                    self.build_select()
                    self.update_buttons_state()
                await i.response.edit_message(content=f"Select a game to toggle (page {self.page + 1})", view=self)

            @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary, custom_id="next_page", row=1)
            async def next_page(self, i: discord.Interaction, button: discord.ui.Button):  # type: ignore[override]
                if button.disabled:
                    await i.response.defer()
                    return
                total_pages = (len(self.games_list) - 1) // self.page_size
                if self.page < total_pages:
                    self.page += 1
                    self.build_select()
                    self.update_buttons_state()
                await i.response.edit_message(content=f"Select a game to toggle (page {self.page + 1})", view=self)

            @discord.ui.button(label="Enable All", style=discord.ButtonStyle.success, custom_id="enable_all", row=2)
            async def enable_all(self, i: discord.Interaction, button: discord.ui.Button):  # type: ignore[override]
                for g in self.games_list:
                    await self.parent.client.database.set_guild_game(self.parent.guild_id, g.slug, 1)  # type: ignore[arg-type]
                    self.toggle_map[g.slug] = 1
                self.build_select()
                self.update_buttons_state()
                await i.response.edit_message(content="Enabled all games.", view=self)

            @discord.ui.button(label="Disable All", style=discord.ButtonStyle.danger, custom_id="disable_all", row=2)
            async def disable_all(self, i: discord.Interaction, button: discord.ui.Button):  # type: ignore[override]
                for g in self.games_list:
                    await self.parent.client.database.set_guild_game(self.parent.guild_id, g.slug, 0)  # type: ignore[arg-type]
                    self.toggle_map[g.slug] = 0
                self.build_select()
                self.update_buttons_state()
                await i.response.edit_message(content="Disabled all games.", view=self)

        view = GameToggleView(interaction, games, toggles)
        page_info = "" if len(games) <= 25 else f" (page 1 of {((len(games)-1)//25)+1})"
        await interaction.response.send_message(f"Select a game to toggle{page_info}:", view=view, ephemeral=True)

    @app_commands.command(name="arpg-status", description="Show current ARPG notification settings")
    async def status(self, interaction: discord.Interaction):
        """
        Display the current notification configuration for this guild: enablement state and per-game toggle states.

        :param interaction: The application command interaction context.
        """
        if not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=False)
            return
        settings = await self.bot.database.get_guild_settings(interaction.guild_id)  # type: ignore[arg-type]
        toggles = await self.bot.database.get_guild_games(interaction.guild_id)  # type: ignore[arg-type]
        # Fetch full game list to show explicit ON/OFF for each (default OFF if missing)
        try:
            games = await self.api.get_cached_games()
        except Exception:
            games = []
        embed = discord.Embed(title="aRPG Notifications Settings", color=0x00AA88)
        embed.add_field(name="Enabled", value=str(bool(settings.get("notifications_enabled", 1))), inline=True)
        if games:
            lines = []
            for g in games:
                val = toggles.get(g.slug, 0)
                lines.append(f"{g.slug}: {'on' if val else 'off'}")
            embed.add_field(name="Game Toggles", value="\n".join(lines)[:1024], inline=False)
        else:
            embed.add_field(name="Game Toggles", value="(No games cached)", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=False)

    @app_commands.command(name="arpg-seasons", description="List currently active ARPG seasons")
    async def list_seasons(self, interaction: discord.Interaction):
        """Paginated list of currently active seasons (10 per page) using cached API data.

        Each page shows start/end relative times plus patch notes & timeline links when available.
        """
        await interaction.response.defer(ephemeral=False, thinking=True)
        try:
            seasons = await self.fetch_active_seasons()
        except Exception as e:
            self.bot.logger.error(f"Failed to load seasons: {e}")
            await interaction.followup.send(f"Error: {e}", ephemeral=False)
            return
        if not seasons:
            await interaction.followup.send("No active seasons found.", ephemeral=False)
            return

        per_page = 10

        class SeasonsPager(discord.ui.View):
            def __init__(self, outer: 'ARPGTimeline', items: List[Season]):
                super().__init__(timeout=180)
                self.outer = outer
                self.items = items
                self.page = 0
                self.per_page = per_page
                self._update_buttons_state()

            def build_embed(self) -> discord.Embed:
                embed = discord.Embed(title="Active ARPG Seasons", color=discord.Color.blurple())
                start_index = self.page * self.per_page
                slice_ = self.items[start_index:start_index + self.per_page]
                for s in slice_:
                    start_str = discord.utils.format_dt(s.starts_at, style="R") if s.starts_at else "?"
                    end_str = discord.utils.format_dt(s.ends_at, style="R") if getattr(s, "ends_at", None) else "TBD"
                    field_name = f"{s.game_name}: {s.title}" if s.title else s.game_name
                    parts = [f"Start: {start_str}", f"End: {end_str}"]
                    if getattr(s, "patch_notes_url", None):
                        parts.append(f"[Patch Notes]({s.patch_notes_url})")
                    if s.url:
                        parts.append(f"[Timeline]({s.url})")
                    embed.add_field(name=field_name, value=" | ".join(parts), inline=False)
                total_pages = (len(self.items) - 1) // self.per_page + 1
                embed.set_footer(text=f"Page {self.page + 1}/{total_pages} • {len(self.items)} seasons")
                return embed

            def _update_buttons_state(self):
                total_pages = (len(self.items) - 1) // self.per_page
                for child in self.children:
                    if isinstance(child, discord.ui.Button):
                        if child.custom_id == 'prev_seasons':
                            child.disabled = self.page <= 0
                        elif child.custom_id == 'next_seasons':
                            child.disabled = self.page >= total_pages

            @discord.ui.button(label='Prev', style=discord.ButtonStyle.secondary, custom_id='prev_seasons')
            async def prev(self, i: discord.Interaction, button: discord.ui.Button):  # type: ignore[override]
                if button.disabled:
                    await i.response.defer()
                    return
                if self.page > 0:
                    self.page -= 1
                    self._update_buttons_state()
                await i.response.edit_message(embed=self.build_embed(), view=self)

            @discord.ui.button(label='Next', style=discord.ButtonStyle.secondary, custom_id='next_seasons')
            async def next(self, i: discord.Interaction, button: discord.ui.Button):  # type: ignore[override]
                if button.disabled:
                    await i.response.defer()
                    return
                total_pages = (len(self.items) - 1) // self.per_page
                if self.page < total_pages:
                    self.page += 1
                    self._update_buttons_state()
                await i.response.edit_message(embed=self.build_embed(), view=self)

            async def on_timeout(self) -> None:  # type: ignore[override]
                for child in self.children:
                    if isinstance(child, discord.ui.Button):
                        child.disabled = True
                # We cannot edit if message deleted; ignore errors
                try:
                    await self.message.edit(view=self)  # type: ignore[attr-defined]
                except Exception:
                    pass

        view = SeasonsPager(self, seasons)
        embed = view.build_embed()
        sent = await interaction.followup.send(embed=embed, view=view, ephemeral=False)
        view.message = sent  # type: ignore[attr-defined]


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ARPGTimeline(bot))
