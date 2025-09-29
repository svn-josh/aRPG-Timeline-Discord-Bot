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
        # Preflight permission check: bot must have Manage Events
        me = guild.me or guild.get_member(self.bot.user.id)  # type: ignore[arg-type]
        if not me:
            self.bot.logger.warning(
                f"guild={guild.id} game={s.game_slug} season_key={s.season_key} action=event_precheck member_not_found"
            )
            return False
        perms = getattr(me, "guild_permissions", None)
        if not perms or not getattr(perms, "manage_events", False):
            self.bot.logger.warning(
                f"guild={guild.id} game={s.game_slug} season_key={s.season_key} action=event_precheck missing_permission=manage_events"
            )
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
        except discord.Forbidden as e:
            self.bot.logger.error(
                f"guild={guild.id} game={s.game_slug} season_key={s.season_key} action=create_event_forbidden detail=Missing_Permissions error={e}"
            )
            return False
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

    def _check_bot_permissions(self, guild: discord.Guild) -> Tuple[bool, Optional[str]]:
        """Check if the bot has the necessary permissions to create events."""
        me = guild.me or guild.get_member(self.bot.user.id)  # type: ignore[arg-type]
        if not me:
            return False, "Bot member not found in guild."
        
        perms = getattr(me, "guild_permissions", None)
        if not perms or not getattr(perms, "manage_events", False):
            return False, (
                "⚠️ **Missing Permission: Manage Events**\n\n"
                "The bot needs the **Manage Events** permission to create Discord scheduled events for upcoming seasons.\n\n"
                "**How to fix:**\n"
                "1. Go to Server Settings → Roles\n"
                "2. Find the bot's role\n"
                "3. Enable **Manage Events** permission\n"
                "4. Or re-invite the bot with proper permissions\n\n"
                "Without this permission, the bot will keep retrying but events won't be created."
            )
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
        
        # Check permissions if enabling notifications
        if enabled and interaction.guild:
            perm_ok, perm_msg = self._check_bot_permissions(interaction.guild)
            if not perm_ok:
                embed = discord.Embed(
                    title="Permission Check Failed",
                    description=perm_msg,
                    color=0xE02B2B
                )
                await interaction.response.send_message(embed=embed, ephemeral=False)
                return
        
        await self.bot.database.set_guild_enabled(interaction.guild_id, 1 if enabled else 0)  # type: ignore[arg-type]
        response_msg = f"Notifications {'enabled' if enabled else 'disabled'}."
        if enabled:
            response_msg += " ✅ Bot has required permissions!"
        await interaction.response.send_message(response_msg, ephemeral=False)


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
                        
                        # Check permissions if enabling a game
                        if new_val == 1 and i.guild:
                            # Get the ARPG cog to access permission check method
                            arpg_cog = view_ref.parent.client.get_cog('arpg')
                            if arpg_cog and hasattr(arpg_cog, '_check_bot_permissions'):
                                perm_ok, perm_msg = arpg_cog._check_bot_permissions(i.guild)
                                if not perm_ok:
                                    embed = discord.Embed(
                                        title="Permission Check Failed",
                                        description=perm_msg,
                                        color=0xE02B2B
                                    )
                                    await i.response.send_message(embed=embed, ephemeral=True)
                                    return
                        
                        await view_ref.parent.client.database.set_guild_game(view_ref.parent.guild_id, slug, new_val)  # type: ignore[arg-type]
                        view_ref.toggle_map[slug] = new_val
                        view_ref.build_select()
                        view_ref.update_buttons_state()
                        
                        response_msg = f"Toggled {slug}: {'enabled' if new_val else 'disabled'}"
                        if new_val == 1:
                            response_msg += " ✅ Bot has required permissions!"
                        await i.response.edit_message(content=response_msg, view=view_ref)

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
                # Check permissions before enabling all games
                if i.guild:
                    arpg_cog = self.parent.client.get_cog('arpg')
                    if arpg_cog and hasattr(arpg_cog, '_check_bot_permissions'):
                        perm_ok, perm_msg = arpg_cog._check_bot_permissions(i.guild)
                        if not perm_ok:
                            embed = discord.Embed(
                                title="Permission Check Failed",
                                description=perm_msg,
                                color=0xE02B2B
                            )
                            await i.response.send_message(embed=embed, ephemeral=True)
                            return
                
                for g in self.games_list:
                    await self.parent.client.database.set_guild_game(self.parent.guild_id, g.slug, 1)  # type: ignore[arg-type]
                    self.toggle_map[g.slug] = 1
                self.build_select()
                self.update_buttons_state()
                await i.response.edit_message(content="Enabled all games. ✅ Bot has required permissions!", view=self)

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
        
        # Check if notifications are enabled
        notifications_enabled = bool(settings.get("notifications_enabled", 1))
        
        # Create main embed
        embed = discord.Embed(
            title="⚙️ aRPG Notification Settings",
            color=0x00AA88 if notifications_enabled else 0xE02B2B,
            timestamp=discord.utils.utcnow()
        )
        
        # Add server info
        embed.set_author(
            name=f"{interaction.guild.name}",
            icon_url=interaction.guild.icon.url if interaction.guild.icon else None
        )
        
        # Overall status
        status_emoji = "🟢" if notifications_enabled else "🔴"
        status_text = "**ENABLED**" if notifications_enabled else "**DISABLED**"
        embed.add_field(
            name="📡 Notification Status",
            value=f"{status_emoji} {status_text}",
            inline=True
        )
        
        # Check permissions
        perm_ok, _ = self._check_bot_permissions(interaction.guild)
        perm_emoji = "✅" if perm_ok else "⚠️"
        perm_text = "All permissions OK" if perm_ok else "Missing permissions"
        embed.add_field(
            name="🔐 Bot Permissions",
            value=f"{perm_emoji} {perm_text}",
            inline=True
        )
        
        # Game count summary
        if games:
            enabled_count = sum(1 for g in games if toggles.get(g.slug, 0))
            total_count = len(games)
            embed.add_field(
                name="🎮 Games Enabled",
                value=f"**{enabled_count}** / **{total_count}** games",
                inline=True
            )
        
        # Detailed game list
        if games:
            # Separate enabled and disabled games
            enabled_games = []
            disabled_games = []
            
            for g in games:
                val = toggles.get(g.slug, 0)
                game_name = g.name if hasattr(g, 'name') and g.name else g.slug.replace('-', ' ').title()
                
                if val:
                    enabled_games.append(f"✅ **{game_name}**")
                else:
                    disabled_games.append(f"❌ {game_name}")
            
            # Add enabled games field
            if enabled_games:
                enabled_text = "\n".join(enabled_games)
                if len(enabled_text) > 1024:
                    enabled_text = enabled_text[:1000] + "\n... (truncated)"
                embed.add_field(
                    name="🟢 Enabled Games",
                    value=enabled_text,
                    inline=True
                )
            else:
                embed.add_field(
                    name="🟢 Enabled Games",
                    value="*No games enabled*",
                    inline=True
                )
            
            # Add disabled games field (first 10 only to save space)
            if disabled_games:
                disabled_text = "\n".join(disabled_games[:10])
                if len(disabled_games) > 10:
                    disabled_text += f"\n*... and {len(disabled_games) - 10} more*"
                embed.add_field(
                    name="🔴 Disabled Games",
                    value=disabled_text,
                    inline=True
                )
            
            # Add empty field for layout (3 columns)
            embed.add_field(name="\u200b", value="\u200b", inline=True)
        else:
            embed.add_field(
                name="🎮 Game Status",
                value="⚠️ *No games cached - try again later*",
                inline=False
            )
        
        # Add helpful footer
        if not notifications_enabled:
            embed.add_field(
                name="💡 Quick Start",
                value="Use `/arpg-enable true` to enable notifications",
                inline=False
            )
        elif not perm_ok:
            embed.add_field(
                name="💡 Action Required",
                value="Use `/arpg-check-permissions` for setup instructions",
                inline=False
            )
        elif games and not any(toggles.get(g.slug, 0) for g in games):
            embed.add_field(
                name="💡 Next Step",
                value="Use `/arpg-toggle-game` to enable specific games",
                inline=False
            )
        
        embed.set_footer(
            text="aRPG Timeline Bot • Tip: Use /arpg-toggle-game to configure games",
            icon_url=self.bot.user.avatar.url if self.bot.user.avatar else None
        )
        
        await interaction.response.send_message(embed=embed, ephemeral=False)

    @app_commands.command(name="arpg-check-permissions", description="Check if the bot has required permissions")
    async def check_permissions(self, interaction: discord.Interaction):
        """
        Check if the bot has the necessary permissions to create Discord scheduled events.

        :param interaction: The application command interaction context.
        """
        if not interaction.guild:
            embed = discord.Embed(
                title="❌ Server Required",
                description="This command can only be used in a server.",
                color=0xE02B2B
            )
            await interaction.response.send_message(embed=embed, ephemeral=False)
            return
        
        perm_ok, perm_msg = self._check_bot_permissions(interaction.guild)
        
        # Get bot member info for display
        bot_member = interaction.guild.me or interaction.guild.get_member(self.bot.user.id)
        
        if perm_ok:
            embed = discord.Embed(
                title="✅ Permission Check Complete",
                description="**All systems operational!** The bot is properly configured and ready to create Discord events for upcoming aRPG seasons.",
                color=0x00AA88,
                timestamp=discord.utils.utcnow()
            )
            
            embed.set_author(
                name=f"Checking {self.bot.user.display_name}",
                icon_url=self.bot.user.avatar.url if self.bot.user.avatar else None
            )
            
            embed.add_field(
                name="🔐 Required Permissions",
                value="✅ **Manage Events** - Create scheduled events\n✅ **Send Messages** - Send notifications\n✅ **Embed Links** - Rich message formatting",
                inline=False
            )
            
            embed.add_field(
                name="⚡ What Happens Next",
                value="• Bot will create Discord events for new seasons\n• Events appear in your server's Events tab\n• Members get notified based on their settings",
                inline=False
            )
            
            embed.add_field(
                name="🎮 Ready to Configure",
                value="Use `/arpg-toggle-game` to choose which games to track!",
                inline=False
            )
            
        else:
            embed = discord.Embed(
                title="⚠️ Permission Issues Detected",
                description="The bot is missing required permissions. Events cannot be created until this is fixed.",
                color=0xE02B2B,
                timestamp=discord.utils.utcnow()
            )
            
            embed.set_author(
                name=f"Checking {self.bot.user.display_name}",
                icon_url=self.bot.user.avatar.url if self.bot.user.avatar else None
            )
            
            embed.add_field(
                name="❌ Missing Permission",
                value="**Manage Events** - Required to create Discord scheduled events",
                inline=False
            )
            
            embed.add_field(
                name="🔧 How to Fix",
                value="**Option 1: Update Bot Role**\n1. Go to Server Settings → Roles\n2. Find the bot's role\n3. Enable **Manage Events** permission\n\n**Option 2: Re-invite Bot**\nRe-invite with proper permissions using a new invite link.",
                inline=False
            )
            
            embed.add_field(
                name="⚠️ Current Impact",
                value="• Season notifications are enabled but **events won't be created**\n• Bot will keep retrying every 5 minutes\n• Check logs for repeated failure messages",
                inline=False
            )
        
        # Add role info if available
        if bot_member and bot_member.roles:
            top_role = bot_member.top_role
            embed.add_field(
                name="🏷️ Bot Role",
                value=f"**{top_role.name}** (Position: {top_role.position})",
                inline=True
            )
        
        embed.set_footer(
            text=f"Checked in {interaction.guild.name} • aRPG Timeline Bot",
            icon_url=interaction.guild.icon.url if interaction.guild.icon else None
        )
        
        await interaction.response.send_message(embed=embed, ephemeral=False)

    @app_commands.command(name="arpg-seasons", description="List currently active ARPG seasons")
    async def list_seasons(self, interaction: discord.Interaction):
        """Paginated list of currently active seasons (8 per page) using cached API data.

        Each page shows start/end relative times plus patch notes & timeline links when available.
        """
        await interaction.response.defer(ephemeral=False, thinking=True)
        try:
            seasons = await self.fetch_active_seasons()
        except Exception as e:
            self.bot.logger.error(f"Failed to load seasons: {e}")
            
            error_embed = discord.Embed(
                title="❌ Failed to Load Seasons",
                description=f"Unable to fetch season data from aRPG Timeline API.\n\n**Error:** `{e}`",
                color=0xE02B2B,
                timestamp=discord.utils.utcnow()
            )
            error_embed.add_field(
                name="🔄 Try Again",
                value="This is usually a temporary issue. Try the command again in a few moments.",
                inline=False
            )
            error_embed.set_footer(text="aRPG Timeline Bot")
            
            await interaction.followup.send(embed=error_embed, ephemeral=False)
            return
            
        if not seasons:
            no_seasons_embed = discord.Embed(
                title="📅 No Active Seasons",
                description="No aRPG seasons are currently active or upcoming.\n\nThis could mean all current seasons have ended, or new seasons haven't been announced yet.",
                color=0xFFA500,
                timestamp=discord.utils.utcnow()
            )
            no_seasons_embed.add_field(
                name="🔄 Check Back Later",
                value="New seasons are announced regularly. Try checking again later or visit [aRPG Timeline](https://arpg-timeline.com) for updates.",
                inline=False
            )
            no_seasons_embed.set_footer(text="aRPG Timeline Bot")
            
            await interaction.followup.send(embed=no_seasons_embed, ephemeral=False)
            return

        per_page = 5  # Reduced for better visual layout

        class SeasonsPager(discord.ui.View):
            def __init__(self, outer: 'ARPGTimeline', items: List[Season]):
                super().__init__(timeout=180)
                self.outer = outer
                self.items = items
                self.page = 0
                self.per_page = per_page
                self._update_buttons_state()

            def build_embed(self) -> discord.Embed:
                embed = discord.Embed(
                    title="🎮 Active aRPG Seasons",
                    description="Currently active and upcoming Action RPG seasons",
                    color=0x5865F2,  # Discord blurple
                    timestamp=discord.utils.utcnow()
                )
                
                start_index = self.page * self.per_page
                slice_ = self.items[start_index:start_index + self.per_page]
                
                for i, s in enumerate(slice_, 1):
                    # Determine season status
                    now = discord.utils.utcnow()
                    if s.starts_at and s.starts_at > now:
                        status_emoji = "🔮"  # Upcoming
                        status_text = "Upcoming"
                    elif s.ends_at and s.ends_at < now:
                        status_emoji = "✅"  # Ended
                        status_text = "Ended"
                    else:
                        status_emoji = "🔥"  # Active
                        status_text = "Active"
                    
                    # Format dates
                    start_str = discord.utils.format_dt(s.starts_at, style="R") if s.starts_at else "Unknown"
                    end_str = discord.utils.format_dt(s.ends_at, style="R") if getattr(s, "ends_at", None) else "TBD"
                    
                    # Build field name with status
                    field_name = f"{status_emoji} **{s.game_name}**"
                    if s.title and s.title != s.game_name:
                        field_name += f": {s.title}"
                    
                    # Build field value with better formatting
                    field_lines = [
                        f"📅 **Starts:** {start_str}",
                        f"🏁 **Ends:** {end_str}",
                        f"🎯 **Status:** {status_text}"
                    ]
                    
                    # Add links if available
                    links = []
                    if getattr(s, "patch_notes_url", None):
                        links.append(f"[📝 Patch Notes]({s.patch_notes_url})")
                    if s.url:
                        links.append(f"[🌐 Timeline]({s.url})")
                    
                    if links:
                        field_lines.append(f"🔗 {' • '.join(links)}")
                    
                    embed.add_field(
                        name=field_name,
                        value="\n".join(field_lines),
                        inline=False
                    )
                
                # Add summary info
                total_pages = (len(self.items) - 1) // self.per_page + 1
                
                # Count seasons by status
                now = discord.utils.utcnow()
                active_count = sum(1 for s in self.items if not s.starts_at or (s.starts_at <= now and (not getattr(s, 'ends_at', None) or getattr(s, 'ends_at', None) > now)))
                upcoming_count = sum(1 for s in self.items if s.starts_at and s.starts_at > now)
                
                embed.set_footer(
                    text=f"Page {self.page + 1}/{total_pages} • {len(self.items)} total ({active_count} active, {upcoming_count} upcoming) • aRPG Timeline",
                    icon_url=self.outer.bot.user.avatar.url if self.outer.bot.user.avatar else None
                )
                
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
