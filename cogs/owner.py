import discord
from discord import app_commands
from discord.ext import commands
from discord.ext.commands import Context
import math
import sys
import platform
import asyncio
import time
from datetime import datetime, timezone

try:
    import psutil  # type: ignore
except Exception:  # pragma: no cover - fallback if psutil not installed
    psutil = None  # type: ignore


class Owner(commands.Cog, name="owner"):
    def __init__(self, bot) -> None:
        self.bot = bot

    @commands.command(
        name="sync",
        description="Synchonizes the slash commands.",
    )
    @app_commands.describe(scope="The scope of the sync. Can be `global` or `guild`")
    @commands.is_owner()
    async def sync(self, context: Context, scope: str) -> None:
        """
        Synchronize (register) the bot's slash commands with Discord.

        Use 'global' to push commands application-wide (can take up to an hour to propagate) or 'guild' for an immediate sync limited to the current server.

        :param context: The invocation context (owner-only).
        :param scope: Either 'global' or 'guild' specifying the sync target.
        """

        if scope == "global":
            await context.bot.tree.sync()
            embed = discord.Embed(
                description="Slash commands have been globally synchronized.",
                color=0xBEBEFE,
            )
            await context.send(embed=embed)
            return
        elif scope == "guild":
            context.bot.tree.copy_global_to(guild=context.guild)
            await context.bot.tree.sync(guild=context.guild)
            embed = discord.Embed(
                description="Slash commands have been synchronized in this guild.",
                color=0xBEBEFE,
            )
            await context.send(embed=embed)
            return
        embed = discord.Embed(
            description="The scope must be `global` or `guild`.", color=0xE02B2B
        )
        await context.send(embed=embed)

    # ---------------- Stats Command ----------------
    @app_commands.command(name="stats", description="Show comprehensive bot statistics (owner only)")
    async def stats(self, interaction: discord.Interaction) -> None:
        """Display a comprehensive statistics panel about the bot.

        Includes:
        - Guild / user counts
        - Uptime
        - Command & extension counts
        - Process resource usage (CPU, memory, handles)
        - Python / discord.py versions
        - Database row counts for aRPG tables (best-effort)
        """
        # Owner gate (since app_commands doesn't inherit commands.is_owner decorator directly)
        if not await self.bot.is_owner(interaction.user):
            embed = discord.Embed(
                title="🔒 Access Denied",
                description="This command is restricted to bot owners only.",
                color=0xE02B2B
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        await interaction.response.defer(ephemeral=True)

        now = datetime.now(timezone.utc)
        start_time = getattr(self.bot, "start_time", None)
        uptime_delta = (now - start_time) if start_time else None
        if uptime_delta:
            days, rem = divmod(int(uptime_delta.total_seconds()), 86400)
            hours, rem = divmod(rem, 3600)
            minutes, seconds = divmod(rem, 60)
            uptime_str = f"{days}d {hours}h {minutes}m {seconds}s"
        else:
            uptime_str = "N/A"

        total_guilds = len(self.bot.guilds)
        total_members = sum(g.member_count or 0 for g in self.bot.guilds)
        unique_users = len({m.id for g in self.bot.guilds for m in g.members}) if total_guilds <= 50 else "~>50 guilds (skip)"
        total_commands = len(self.bot.tree.get_commands())
        loaded_cogs = len(self.bot.cogs)

        # Resource stats
        proc = None
        cpu_percent = mem_percent = rss_mb = vms_mb = threads = "N/A"
        if psutil:
            try:
                proc = psutil.Process()
                with proc.oneshot():
                    cpu_percent = f"{proc.cpu_percent(interval=0.1):.1f}%"
                    meminfo = proc.memory_full_info() if hasattr(proc, "memory_full_info") else proc.memory_info()
                    rss_mb = f"{meminfo.rss / 1024 / 1024:.1f} MB"
                    vms_mb = f"{meminfo.vms / 1024 / 1024:.1f} MB"
                    mem_percent = f"{proc.memory_percent():.1f}%"
                    threads = str(proc.num_threads())
            except Exception:
                pass

        py_ver = platform.python_version()
        dpy_ver = discord.__version__
        shard_info = f"{self.bot.shard_id} / {self.bot.shard_count}" if getattr(self.bot, "shard_count", None) else "N/A"

        # Database row counts (best effort). We only query if connection exists.
        db_stats_lines = []
        db = getattr(self.bot, "database", None)
        if db and getattr(db, "connection", None):
            try:
                async with db.connection.execute("SELECT COUNT(*) FROM guild_settings") as cur:
                    gs = (await cur.fetchone())[0]
                async with db.connection.execute("SELECT COUNT(*) FROM guild_games") as cur:
                    gg = (await cur.fetchone())[0]
                async with db.connection.execute("SELECT COUNT(*) FROM season_cache") as cur:
                    sc = (await cur.fetchone())[0]
                async with db.connection.execute("SELECT COUNT(*) FROM api_tokens") as cur:
                    at = (await cur.fetchone())[0]
                async with db.connection.execute("SELECT COUNT(*) FROM api_cache") as cur:
                    ac = (await cur.fetchone())[0]
                db_stats_lines.append(f"📊 **Guild Settings:** {gs:,}")
                db_stats_lines.append(f"🎮 **Game Toggles:** {gg:,}")
                db_stats_lines.append(f"🗓️ **Season Cache:** {sc:,}")
                db_stats_lines.append(f"🔑 **API Tokens:** {at:,}")
                db_stats_lines.append(f"💾 **API Cache:** {ac:,}")
            except Exception as e:  # table maybe missing
                db_stats_lines.append(f"❌ Error: {e}")
        else:
            db_stats_lines.append("⚠️ Database not ready")

        # Create main embed
        embed = discord.Embed(
            title="📊 Bot Statistics Dashboard",
            description=f"Comprehensive statistics for **{self.bot.user.display_name}**",
            color=0x5865F2,
            timestamp=now
        )
        
        embed.set_author(
            name=f"{self.bot.user.display_name} Stats",
            icon_url=self.bot.user.avatar.url if self.bot.user.avatar else None
        )

        # Basic stats (first row)
        embed.add_field(name="⏱️ Uptime", value=f"```{uptime_str}```", inline=True)
        embed.add_field(name="🏠 Servers", value=f"```{total_guilds:,}```", inline=True)
        embed.add_field(name="👥 Total Users", value=f"```{total_members:,}```", inline=True)
        
        # More stats (second row)
        embed.add_field(name="👤 Unique Users", value=f"```{unique_users}```", inline=True)
        embed.add_field(name="⚡ Commands", value=f"```{total_commands:,}```", inline=True)
        embed.add_field(name="🔧 Loaded Cogs", value=f"```{loaded_cogs:,}```", inline=True)

        # Technical info (third row)
        embed.add_field(name="🔀 Shards", value=f"```{shard_info}```", inline=True)
        embed.add_field(name="📡 Latency", value=f"```{round(self.bot.latency * 1000):,}ms```", inline=True)
        embed.add_field(name="🧵 Threads", value=f"```{threads}```", inline=True)

        # System resources
        embed.add_field(
            name="💻 System Resources",
            value=f"**CPU Usage:** {cpu_percent}\n**Memory:** {mem_percent} ({rss_mb})\n**Virtual Memory:** {vms_mb}",
            inline=True
        )

        # Versions
        embed.add_field(
            name="📋 Version Info",
            value=f"**Python:** {py_ver}\n**discord.py:** {dpy_ver}\n**Platform:** {platform.system()} {platform.release()}",
            inline=True
        )

        # Database stats
        embed.add_field(
            name="🗄️ Database Statistics",
            value="\n".join(db_stats_lines) if db_stats_lines else "No data available",
            inline=True
        )

        embed.set_footer(
            text=f"Statistics generated for {interaction.user.display_name}",
            icon_url=interaction.user.avatar.url if interaction.user.avatar else None
        )

        await interaction.followup.send(embed=embed, ephemeral=True)

    # ---------------- Sync Commands ----------------
    @app_commands.command(name="sync", description="Sync application commands to Discord (owner only)")
    async def sync(self, interaction: discord.Interaction) -> None:
        """Sync the app command tree to Discord.

        This will push all registered application commands (slash commands)
        to Discord so they become available for use.
        """
        if not await self.bot.is_owner(interaction.user):
            embed = discord.Embed(
                title="🔒 Access Denied",
                description="This command is restricted to bot owners only.",
                color=0xE02B2B
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        await interaction.response.defer(ephemeral=True)

        embed = discord.Embed(
            title="⚡ Command Sync Starting",
            description="Syncing application commands to Discord...",
            color=0xFEE75C
        )
        embed.set_author(
            name="Command Synchronization",
            icon_url=self.bot.user.avatar.url if self.bot.user.avatar else None
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

        try:
            start_time = time.time()
            synced_cmds = await self.bot.tree.sync()
            sync_duration = (time.time() - start_time) * 1000  # Convert to ms

            embed = discord.Embed(
                title="✅ Command Sync Complete",
                description=f"Successfully synced **{len(synced_cmds)}** application commands to Discord.",
                color=0x57F287
            )
            
            embed.add_field(
                name="📊 Sync Details",
                value=f"**Commands Synced:** {len(synced_cmds)}\n**Duration:** {sync_duration:.1f}ms\n**Status:** Active",
                inline=True
            )
            
            embed.add_field(
                name="⏰ Propagation Time",
                value="Commands may take up to **1 hour** to appear in all servers due to Discord's caching.",
                inline=True
            )

            if synced_cmds:
                cmd_list = [f"• `/{cmd.name}` - {getattr(cmd, 'description', 'No description')}" for cmd in synced_cmds[:10]]
                if len(synced_cmds) > 10:
                    cmd_list.append(f"• ... and {len(synced_cmds) - 10} more commands")
                
                embed.add_field(
                    name="🔧 Synced Commands",
                    value="\n".join(cmd_list),
                    inline=False
                )

            embed.set_footer(
                text=f"Executed by {interaction.user.display_name}",
                icon_url=interaction.user.avatar.url if interaction.user.avatar else None
            )
            embed.timestamp = datetime.now(timezone.utc)

            await interaction.edit_original_response(embed=embed)
        except Exception as e:
            embed = discord.Embed(
                title="❌ Command Sync Failed",
                description=f"An error occurred while syncing commands:",
                color=0xE02B2B
            )
            embed.add_field(
                name="🚨 Error Details",
                value=f"```\n{type(e).__name__}: {e}\n```",
                inline=False
            )
            embed.add_field(
                name="💡 Troubleshooting",
                value="• Check bot permissions\n• Verify bot is properly authenticated\n• Try again in a few minutes",
                inline=False
            )
            embed.set_footer(text="Sync failed - check logs for more details")
            embed.timestamp = datetime.now(timezone.utc)
            
            await interaction.edit_original_response(embed=embed)

    @app_commands.command(name="app-unsync", description="Clear all application commands from Discord (owner only)")
    async def app_unsync(self, interaction: discord.Interaction) -> None:
        """Clear all application commands from Discord.

        WARNING: This will remove all slash commands from Discord.
        They will no longer be available until you sync again.
        """
        if not await self.bot.is_owner(interaction.user):
            embed = discord.Embed(
                title="🔒 Access Denied",
                description="This command is restricted to bot owners only.",
                color=0xE02B2B
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        await interaction.response.defer(ephemeral=True)

        embed = discord.Embed(
            title="⚠️ Command Unsync Starting",
            description="Clearing all application commands from Discord...",
            color=0xFEE75C
        )
        embed.add_field(
            name="🚨 Warning",
            value="This will remove **ALL** slash commands from Discord until you sync again.",
            inline=False
        )
        embed.set_author(
            name="Command Removal",
            icon_url=self.bot.user.avatar.url if self.bot.user.avatar else None
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

        try:
            start_time = time.time()
            self.bot.tree.clear_commands()
            await self.bot.tree.sync()
            unsync_duration = (time.time() - start_time) * 1000  # Convert to ms

            embed = discord.Embed(
                title="✅ Command Unsync Complete",
                description="Successfully cleared all application commands from Discord.",
                color=0x57F287
            )
            
            embed.add_field(
                name="📊 Unsync Details",
                value=f"**Duration:** {unsync_duration:.1f}ms\n**Status:** Commands Removed\n**Effect:** Immediate",
                inline=True
            )
            
            embed.add_field(
                name="🔄 Next Steps",
                value="Use `/sync` command to restore application commands when ready.",
                inline=True
            )

            embed.add_field(
                name="⚠️ Important Note",
                value="All slash commands are now **disabled** until you run `/sync` again.",
                inline=False
            )

            embed.set_footer(
                text=f"Executed by {interaction.user.display_name}",
                icon_url=interaction.user.avatar.url if interaction.user.avatar else None
            )
            embed.timestamp = datetime.now(timezone.utc)

            await interaction.edit_original_response(embed=embed)
        except Exception as e:
            embed = discord.Embed(
                title="❌ Command Unsync Failed",
                description=f"An error occurred while clearing commands:",
                color=0xE02B2B
            )
            embed.add_field(
                name="🚨 Error Details",
                value=f"```\n{type(e).__name__}: {e}\n```",
                inline=False
            )
            embed.add_field(
                name="💡 Troubleshooting",
                value="• Check bot permissions\n• Verify bot connectivity\n• Try again in a few minutes",
                inline=False
            )
            embed.set_footer(text="Unsync failed - check logs for more details")
            embed.timestamp = datetime.now(timezone.utc)
            
            await interaction.edit_original_response(embed=embed)

    @commands.command(
        name="unsync",
        description="Unsynchonizes the slash commands.",
    )
    @app_commands.describe(
        scope="The scope of the sync. Can be `global`, `current_guild` or `guild`"
    )
    @commands.is_owner()
    async def unsync(self, context: Context, scope: str) -> None:
        """
        Remove previously registered slash commands.

        'global' clears and re-syncs an empty global command set. 'guild' clears only the current guild's overrides.

        :param context: The invocation context (owner-only).
        :param scope: One of 'global' or 'guild'.
        """

        if scope == "global":
            context.bot.tree.clear_commands(guild=None)
            await context.bot.tree.sync()
            embed = discord.Embed(
                description="Slash commands have been globally unsynchronized.",
                color=0xBEBEFE,
            )
            await context.send(embed=embed)
            return
        elif scope == "guild":
            context.bot.tree.clear_commands(guild=context.guild)
            await context.bot.tree.sync(guild=context.guild)
            embed = discord.Embed(
                description="Slash commands have been unsynchronized in this guild.",
                color=0xBEBEFE,
            )
            await context.send(embed=embed)
            return
        embed = discord.Embed(
            description="The scope must be `global` or `guild`.", color=0xE02B2B
        )
        await context.send(embed=embed)

    # ---------------- Cog Management Commands ----------------
    @app_commands.command(name="load", description="Load a cog extension (owner only)")
    @app_commands.describe(cog="The name of the cog to load (e.g., 'general', 'arpg_timeline')")
    async def load(self, interaction: discord.Interaction, cog: str) -> None:
        """Dynamically load an extension (cog) by name.

        Expects the python module to live under the 'cogs' package (e.g. 'general').
        """
        if not await self.bot.is_owner(interaction.user):
            embed = discord.Embed(
                title="🔒 Access Denied",
                description="This command is restricted to bot owners only.",
                color=0xE02B2B
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        await interaction.response.defer(ephemeral=True)

        embed = discord.Embed(
            title="⚡ Loading Cog",
            description=f"Loading cog `{cog}`...",
            color=0xFEE75C
        )
        embed.set_author(
            name="Cog Management",
            icon_url=self.bot.user.avatar.url if self.bot.user.avatar else None
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

        try:
            await self.bot.load_extension(f"cogs.{cog}")
            
            embed = discord.Embed(
                title="✅ Cog Loaded Successfully",
                description=f"Successfully loaded the `{cog}` cog.",
                color=0x57F287
            )
            
            embed.add_field(
                name="📦 Cog Details",
                value=f"**Name:** {cog}\n**Status:** Loaded\n**Module:** `cogs.{cog}`",
                inline=True
            )
            
            # Try to get cog info if it exists
            loaded_cog = self.bot.get_cog(cog.title())
            if loaded_cog:
                commands_count = len(loaded_cog.get_commands())
                app_commands_count = len(loaded_cog.get_app_commands())
                embed.add_field(
                    name="🔧 Commands",
                    value=f"**Prefix Commands:** {commands_count}\n**Slash Commands:** {app_commands_count}\n**Total:** {commands_count + app_commands_count}",
                    inline=True
                )
            
            embed.add_field(
                name="⚠️ Important",
                value="You may need to run `/sync` to register new slash commands with Discord.",
                inline=False
            )

            embed.set_footer(
                text=f"Executed by {interaction.user.display_name}",
                icon_url=interaction.user.avatar.url if interaction.user.avatar else None
            )
            embed.timestamp = datetime.now(timezone.utc)

            await interaction.edit_original_response(embed=embed)
        except Exception as e:
            embed = discord.Embed(
                title="❌ Failed to Load Cog",
                description=f"Could not load the `{cog}` cog.",
                color=0xE02B2B
            )
            embed.add_field(
                name="🚨 Error Details",
                value=f"```\n{type(e).__name__}: {e}\n```",
                inline=False
            )
            embed.add_field(
                name="💡 Troubleshooting",
                value=f"• Check if `cogs/{cog}.py` exists\n• Verify syntax in the cog file\n• Check imports and dependencies\n• Review logs for detailed error info",
                inline=False
            )
            embed.set_footer(text="Load failed - check logs for more details")
            embed.timestamp = datetime.now(timezone.utc)
            
            await interaction.edit_original_response(embed=embed)

    @app_commands.command(name="unload", description="Unload a cog extension (owner only)")
    @app_commands.describe(cog="The name of the cog to unload (e.g., 'general', 'arpg_timeline')")
    async def unload(self, interaction: discord.Interaction, cog: str) -> None:
        """Unload (detach) a previously loaded extension (cog)."""
        if not await self.bot.is_owner(interaction.user):
            embed = discord.Embed(
                title="🔒 Access Denied",
                description="This command is restricted to bot owners only.",
                color=0xE02B2B
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        await interaction.response.defer(ephemeral=True)

        # Check if it's the owner cog
        if cog.lower() == "owner":
            embed = discord.Embed(
                title="⚠️ Cannot Unload Owner Cog",
                description="You cannot unload the owner cog as it contains essential management commands.",
                color=0xFEE75C
            )
            embed.add_field(
                name="💡 Alternative",
                value="Use `/reload owner` if you need to refresh the owner cog.",
                inline=False
            )
            return await interaction.followup.send(embed=embed, ephemeral=True)

        embed = discord.Embed(
            title="⚡ Unloading Cog",
            description=f"Unloading cog `{cog}`...",
            color=0xFEE75C
        )
        embed.set_author(
            name="Cog Management",
            icon_url=self.bot.user.avatar.url if self.bot.user.avatar else None
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

        try:
            await self.bot.unload_extension(f"cogs.{cog}")
            
            embed = discord.Embed(
                title="✅ Cog Unloaded Successfully",
                description=f"Successfully unloaded the `{cog}` cog.",
                color=0x57F287
            )
            
            embed.add_field(
                name="📦 Cog Details",
                value=f"**Name:** {cog}\n**Status:** Unloaded\n**Module:** `cogs.{cog}`",
                inline=True
            )
            
            embed.add_field(
                name="⚠️ Important",
                value="Commands from this cog are no longer available until you reload it.",
                inline=True
            )

            embed.set_footer(
                text=f"Executed by {interaction.user.display_name}",
                icon_url=interaction.user.avatar.url if interaction.user.avatar else None
            )
            embed.timestamp = datetime.now(timezone.utc)

            await interaction.edit_original_response(embed=embed)
        except Exception as e:
            embed = discord.Embed(
                title="❌ Failed to Unload Cog",
                description=f"Could not unload the `{cog}` cog.",
                color=0xE02B2B
            )
            embed.add_field(
                name="🚨 Error Details",
                value=f"```\n{type(e).__name__}: {e}\n```",
                inline=False
            )
            embed.add_field(
                name="💡 Troubleshooting",
                value=f"• Check if the `{cog}` cog is actually loaded\n• Verify the cog name is correct\n• Review logs for detailed error info",
                inline=False
            )
            embed.set_footer(text="Unload failed - check logs for more details")
            embed.timestamp = datetime.now(timezone.utc)
            
            await interaction.edit_original_response(embed=embed)

    @app_commands.command(name="reload", description="Reload a cog extension (owner only)")
    @app_commands.describe(cog="The name of the cog to reload (e.g., 'general', 'arpg_timeline')")
    async def reload(self, interaction: discord.Interaction, cog: str) -> None:
        """Reload a cog in-place (unload then load) to apply code changes without restarting the bot."""
        if not await self.bot.is_owner(interaction.user):
            embed = discord.Embed(
                title="🔒 Access Denied",
                description="This command is restricted to bot owners only.",
                color=0xE02B2B
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        await interaction.response.defer(ephemeral=True)

        embed = discord.Embed(
            title="🔄 Reloading Cog",
            description=f"Reloading cog `{cog}`...",
            color=0xFEE75C
        )
        embed.set_author(
            name="Cog Management",
            icon_url=self.bot.user.avatar.url if self.bot.user.avatar else None
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

        try:
            start_time = time.time()
            await self.bot.reload_extension(f"cogs.{cog}")
            reload_duration = (time.time() - start_time) * 1000  # Convert to ms
            
            embed = discord.Embed(
                title="✅ Cog Reloaded Successfully",
                description=f"Successfully reloaded the `{cog}` cog.",
                color=0x57F287
            )
            
            embed.add_field(
                name="📦 Cog Details",
                value=f"**Name:** {cog}\n**Status:** Reloaded\n**Duration:** {reload_duration:.1f}ms",
                inline=True
            )
            
            # Try to get cog info if it exists
            loaded_cog = self.bot.get_cog(cog.title())
            if loaded_cog:
                commands_count = len(loaded_cog.get_commands())
                app_commands_count = len(loaded_cog.get_app_commands())
                embed.add_field(
                    name="🔧 Commands",
                    value=f"**Prefix Commands:** {commands_count}\n**Slash Commands:** {app_commands_count}\n**Total:** {commands_count + app_commands_count}",
                    inline=True
                )
            
            embed.add_field(
                name="💡 Code Changes Applied",
                value="All code changes in the cog file have been applied without restarting the bot.",
                inline=False
            )

            embed.set_footer(
                text=f"Executed by {interaction.user.display_name}",
                icon_url=interaction.user.avatar.url if interaction.user.avatar else None
            )
            embed.timestamp = datetime.now(timezone.utc)

            await interaction.edit_original_response(embed=embed)
        except Exception as e:
            embed = discord.Embed(
                title="❌ Failed to Reload Cog",
                description=f"Could not reload the `{cog}` cog.",
                color=0xE02B2B
            )
            embed.add_field(
                name="🚨 Error Details",
                value=f"```\n{type(e).__name__}: {e}\n```",
                inline=False
            )
            embed.add_field(
                name="💡 Troubleshooting",
                value=f"• Check syntax in `cogs/{cog}.py`\n• Verify imports and dependencies\n• Try `/unload {cog}` then `/load {cog}`\n• Review logs for detailed error info",
                inline=False
            )
            embed.set_footer(text="Reload failed - check logs for more details")
            embed.timestamp = datetime.now(timezone.utc)
            
            await interaction.edit_original_response(embed=embed)

    # ---------------- Bot Control Commands ----------------
    @app_commands.command(name="shutdown", description="Gracefully shutdown the bot (owner only)")
    async def shutdown(self, interaction: discord.Interaction) -> None:
        """Gracefully close the bot's connection to Discord and exit the process."""
        if not await self.bot.is_owner(interaction.user):
            embed = discord.Embed(
                title="🔒 Access Denied",
                description="This command is restricted to bot owners only.",
                color=0xE02B2B
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        embed = discord.Embed(
            title="🛑 Bot Shutdown Initiated",
            description="The bot is shutting down gracefully...",
            color=0xE02B2B
        )
        
        embed.add_field(
            name="📊 Final Statistics",
            value=f"**Servers:** {len(self.bot.guilds)}\n**Users:** {sum(g.member_count or 0 for g in self.bot.guilds)}\n**Uptime:** {getattr(self.bot, 'start_time', 'Unknown')}",
            inline=True
        )
        
        embed.add_field(
            name="👋 Farewell",
            value="Thank you for using the bot! The connection will be closed shortly.",
            inline=True
        )
        
        embed.set_footer(
            text=f"Shutdown initiated by {interaction.user.display_name}",
            icon_url=interaction.user.avatar.url if interaction.user.avatar else None
        )
        embed.timestamp = datetime.now(timezone.utc)

        await interaction.response.send_message(embed=embed, ephemeral=True)
        
        # Give a brief moment for the message to send
        await asyncio.sleep(1)
        await self.bot.close()

    @app_commands.command(name="say", description="Make the bot say a message (owner only)")
    @app_commands.describe(message="The message that should be sent by the bot")
    async def say(self, interaction: discord.Interaction, message: str) -> None:
        """Echo a plain text message using the bot account (utility / quick testing helper)."""
        if not await self.bot.is_owner(interaction.user):
            embed = discord.Embed(
                title="🔒 Access Denied",
                description="This command is restricted to bot owners only.",
                color=0xE02B2B
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        if len(message) > 2000:
            embed = discord.Embed(
                title="📝 Message Too Long",
                description="Discord messages cannot exceed 2000 characters.",
                color=0xFEE75C
            )
            embed.add_field(
                name="📊 Character Count",
                value=f"**Your Message:** {len(message)} characters\n**Discord Limit:** 2000 characters\n**Exceeded By:** {len(message) - 2000} characters",
                inline=False
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        # Send confirmation to owner
        embed = discord.Embed(
            title="📨 Message Sent",
            description="Your message has been sent successfully.",
            color=0x57F287
        )
        embed.add_field(
            name="📝 Message Preview",
            value=f"```\n{message[:500]}{'...' if len(message) > 500 else ''}\n```",
            inline=False
        )
        embed.add_field(
            name="📊 Details",
            value=f"**Length:** {len(message)} characters\n**Channel:** {interaction.channel.mention if interaction.channel else 'Unknown'}",
            inline=False
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        
        # Send the actual message
        await interaction.followup.send(content=message)

    @app_commands.command(name="embed", description="Make the bot send an embed message (owner only)")
    @app_commands.describe(message="The message content for the embed description")
    async def embed(self, interaction: discord.Interaction, message: str) -> None:
        """Send an embed with the supplied message as its description."""
        if not await self.bot.is_owner(interaction.user):
            embed = discord.Embed(
                title="🔒 Access Denied",
                description="This command is restricted to bot owners only.",
                color=0xE02B2B
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        if len(message) > 4096:
            embed = discord.Embed(
                title="📝 Embed Description Too Long",
                description="Discord embed descriptions cannot exceed 4096 characters.",
                color=0xFEE75C
            )
            embed.add_field(
                name="📊 Character Count",
                value=f"**Your Message:** {len(message)} characters\n**Discord Limit:** 4096 characters\n**Exceeded By:** {len(message) - 4096} characters",
                inline=False
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        # Send confirmation to owner
        confirmation_embed = discord.Embed(
            title="📨 Embed Sent",
            description="Your embed message has been sent successfully.",
            color=0x57F287
        )
        confirmation_embed.add_field(
            name="📝 Content Preview",
            value=f"```\n{message[:500]}{'...' if len(message) > 500 else ''}\n```",
            inline=False
        )
        confirmation_embed.add_field(
            name="📊 Details",
            value=f"**Length:** {len(message)} characters\n**Channel:** {interaction.channel.mention if interaction.channel else 'Unknown'}",
            inline=False
        )
        await interaction.response.send_message(embed=confirmation_embed, ephemeral=True)
        
        # Send the actual embed
        user_embed = discord.Embed(
            description=message,
            color=0x5865F2
        )
        user_embed.set_footer(
            text=f"Sent by {interaction.user.display_name}",
            icon_url=interaction.user.avatar.url if interaction.user.avatar else None
        )
        await interaction.followup.send(embed=user_embed)


async def setup(bot) -> None:
    await bot.add_cog(Owner(bot))
