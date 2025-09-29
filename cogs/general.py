import platform
import discord
from discord import app_commands
from discord.ext import commands


class FeedbackForm(discord.ui.Modal, title="Feeedback"):
    feedback = discord.ui.TextInput(
        label="What do you think about this bot?",
        style=discord.TextStyle.long,
        placeholder="Type your answer here...",
        required=True,
        max_length=256,
    )

    async def on_submit(self, interaction: discord.Interaction):
        self.interaction = interaction
        self.answer = str(self.feedback)
        self.stop()


class General(commands.Cog, name="general"):
    def __init__(self, bot) -> None:
        self.bot = bot
        self.context_menu_user = app_commands.ContextMenu(
            name="Grab ID", callback=self.grab_id
        )
        self.bot.tree.add_command(self.context_menu_user)
        self.context_menu_message = app_commands.ContextMenu(
            name="Remove spoilers", callback=self.remove_spoilers
        )
        self.bot.tree.add_command(self.context_menu_message)

    # Message context menu command
    async def remove_spoilers(
        self, interaction: discord.Interaction, message: discord.Message
    ) -> None:
        """
        Removes the spoilers from the message. This command requires the MESSAGE_CONTENT intent to work properly.

        :param interaction: The application command interaction.
        :param message: The message that is being interacted with.
        """
        spoiler_attachment = None
        for attachment in message.attachments:
            if attachment.is_spoiler():
                spoiler_attachment = attachment
                break
        embed = discord.Embed(
            title="Message without spoilers",
            description=message.content.replace("||", ""),
            color=0xBEBEFE,
        )
        if spoiler_attachment is not None:
            embed.set_image(url=attachment.url)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # User context menu command
    async def grab_id(
        self, interaction: discord.Interaction, user: discord.User
    ) -> None:
        """
        Grabs the ID of the user.

        :param interaction: The application command interaction.
        :param user: The user that is being interacted with.
        """
        embed = discord.Embed(
            description=f"The ID of {user.mention} is `{user.id}`.",
            color=0xBEBEFE,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="help", description="List all available slash commands grouped by cog")
    async def help(self, interaction: discord.Interaction) -> None:
        """Display all slash commands grouped under their cog names (owner-only cogs hidden for non-owners)."""
        is_owner = await self.bot.is_owner(interaction.user)
        
        embed = discord.Embed(
            title="🔧 Bot Commands",
            description="All available slash commands organized by category",
            color=0x5865F2,
            timestamp=discord.utils.utcnow()
        )
        
        embed.set_author(
            name=f"{self.bot.user.display_name} Help",
            icon_url=self.bot.user.avatar.url if self.bot.user.avatar else None
        )
        
        grouped = {}
        total_commands = 0
        
        # Iterate over application commands in the tree
        for cmd in self.bot.tree.get_commands():
            if isinstance(cmd, app_commands.ContextMenu):
                continue
            # Owner-only filtering (heuristic via checks attribute on bound command)
            if not is_owner:
                checks = getattr(cmd, "checks", [])
                if any(getattr(ch, "__name__", "").startswith("is_owner") for ch in checks):
                    continue
            
            total_commands += 1
            cog_name = getattr(getattr(cmd, 'binding', None), '__cog_name__', None) or 'Other'
            
            # Map cog names to prettier display names with emojis
            display_names = {
                'general': '🔧 General',
                'arpg': '🎮 aRPG Timeline',
                'owner': '👑 Owner Only',
                'other': '📦 Other'
            }
            
            label = display_names.get(cog_name.lower(), f"📦 {cog_name.title()}")
            line = f"`/{cmd.name}` - {(cmd.description or '').partition('\n')[0]}"
            grouped.setdefault(label, []).append(line)
        
        # Add command count to description
        embed.description = f"All available slash commands organized by category\n\n**{total_commands} commands available** {'(including owner commands)' if is_owner else ''}"
        
        # Stable order: General first, then aRPG, then alphabetical
        ordered = []
        priority_order = ['🔧 General', '🎮 aRPG Timeline', '👑 Owner Only']
        
        for priority in priority_order:
            if priority in grouped:
                ordered.append(priority)
        
        remaining = sorted(k for k in grouped.keys() if k not in priority_order)
        ordered.extend(remaining)
        
        for section in ordered:
            lines = sorted(grouped[section])
            block = "\n".join(lines)
            if len(block) > 950:
                # truncate to avoid exceeding field limits
                truncated = []
                total = 0
                for l in lines:
                    ln = len(l) + 1
                    if total + ln > 930:
                        truncated.append("... (truncated)")
                        break
                    truncated.append(l)
                    total += ln
                block = "\n".join(truncated)
            embed.add_field(name=section, value=block, inline=False)
        
        # Add helpful footer
        embed.add_field(
            name="💡 Getting Started",
            value="• Use `/arpg-status` to check your notification settings\n• Use `/arpg-toggle-game` to configure which games to track\n• Use `/feedback` to send suggestions to the bot developers",
            inline=False
        )
        
        embed.set_footer(
            text=f"Use /command_name for detailed help • {self.bot.user.display_name}",
            icon_url=interaction.guild.icon.url if interaction.guild and interaction.guild.icon else None
        )
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="ping", description="Check bot latency")
    async def ping(self, interaction: discord.Interaction) -> None:
        """
        Check the bot's current websocket latency to Discord.

        :param interaction: The application command interaction context.
        """
        # Calculate round trip time
        import time
        start_time = time.perf_counter()
        
        embed = discord.Embed(
            title="🏓 Pong!",
            color=0x00AA88,
            timestamp=discord.utils.utcnow()
        )
        
        # Get latency info
        ws_latency = round(self.bot.latency * 1000, 1)
        
        # Color code based on latency
        if ws_latency < 100:
            latency_emoji = "🟢"
            latency_status = "Excellent"
            embed.color = 0x00AA88
        elif ws_latency < 200:
            latency_emoji = "🟡"
            latency_status = "Good"
            embed.color = 0xFFAA00
        else:
            latency_emoji = "🔴"
            latency_status = "Poor"
            embed.color = 0xE02B2B
        
        embed.add_field(
            name="📡 WebSocket Latency",
            value=f"{latency_emoji} **{ws_latency}ms**\n*{latency_status} connection*",
            inline=True
        )
        
        # Add bot status info
        embed.add_field(
            name="🤖 Bot Status",
            value=f"✅ **Online**\n*Ready and operational*",
            inline=True
        )
        
        # Add server count (if not too many)
        if len(self.bot.guilds) <= 100:
            embed.add_field(
                name="🌐 Servers",
                value=f"📊 **{len(self.bot.guilds)}** servers\n*Currently serving*",
                inline=True
            )
        
        embed.set_footer(
            text=f"Response time will be calculated after sending • {self.bot.user.display_name}",
            icon_url=self.bot.user.avatar.url if self.bot.user.avatar else None
        )
        
        await interaction.response.send_message(embed=embed)
        
        # Calculate and edit with response time
        end_time = time.perf_counter()
        response_time = round((end_time - start_time) * 1000, 1)
        
        # Update embed with response time
        embed.add_field(
            name="⚡ Response Time",
            value=f"🚀 **{response_time}ms**\n*Message round trip*",
            inline=True
        )
        
        embed.set_footer(
            text=f"All systems operational • {self.bot.user.display_name}",
            icon_url=self.bot.user.avatar.url if self.bot.user.avatar else None
        )
        
        await interaction.edit_original_response(embed=embed)

    @app_commands.command(name="invite", description="Get the bot invite link")
    async def invite(self, interaction: discord.Interaction) -> None:
        """
        Provide a link for inviting the bot to another server via a direct message (falls back to ephemeral response if DMs are closed).

        :param interaction: The application command interaction context.
        """
        embed = discord.Embed(
            title="🤖 Invite Me to Your Server!",
            description=f"Add **{self.bot.user.display_name}** to your Discord server to track aRPG seasons and get notifications about new content!",
            color=0x5865F2,
            timestamp=discord.utils.utcnow()
        )
        
        embed.set_thumbnail(url=self.bot.user.avatar.url if self.bot.user.avatar else None)
        
        # Add features info
        embed.add_field(
            name="✨ What You Get",
            value="🎮 **Season Tracking** - Never miss a new aRPG season\n📅 **Discord Events** - Automatic event creation\n⚙️ **Customizable** - Choose which games to track\n🔔 **Smart Notifications** - Only get notified about what matters",
            inline=False
        )
        
        # Add the invite button
        embed.add_field(
            name="🔗 Invite Link",
            value=f"**[Click here to invite {self.bot.user.display_name}!]({self.bot.invite_link})**\n\n*The bot will be added with all necessary permissions for optimal functionality.*",
            inline=False
        )
        
        embed.add_field(
            name="🚀 Quick Setup",
            value="1. Click the invite link above\n2. Select your server and authorize\n3. Use `/arpg-status` to check configuration\n4. Use `/arpg-toggle-game` to choose games",
            inline=False
        )
        
        embed.set_footer(
            text=f"Thanks for using {self.bot.user.display_name}! 💜",
            icon_url=interaction.user.avatar.url if interaction.user.avatar else None
        )
        
        try:
            await interaction.user.send(embed=embed)
            
            # Success response
            success_embed = discord.Embed(
                title="📬 Invite Sent!",
                description=f"I've sent the invite link to your DMs! Check your private messages to add **{self.bot.user.display_name}** to your server.",
                color=0x00AA88
            )
            success_embed.set_footer(text="Didn't receive it? Check if your DMs are open!")
            
            await interaction.response.send_message(embed=success_embed, ephemeral=True)
        except discord.Forbidden:
            # Fallback for closed DMs
            embed.title = "🤖 Add Me to Your Server!"
            embed.add_field(
                name="📬 DMs Closed?",
                value="*Your DMs appear to be closed, so here's the invite link:*",
                inline=False
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="support", description="Get support server invite link")
    async def support(self, interaction: discord.Interaction) -> None:
        """
        Send the invite link to the bot's official support server via DM (falls back to ephemeral response if DMs are closed).

        :param interaction: The application command interaction context.
        """
        embed = discord.Embed(
            title="🆘 Need Help?",
            description=f"Join the **{self.bot.user.display_name}** support server for help, updates, and community discussions!",
            color=0x7289DA,
            timestamp=discord.utils.utcnow()
        )
        
        embed.set_thumbnail(url=self.bot.user.avatar.url if self.bot.user.avatar else None)
        
        embed.add_field(
            name="💬 What You'll Find",
            value="🔧 **Technical Support** - Get help with setup and issues\n📢 **Bot Updates** - Latest news and feature announcements\n💡 **Feature Requests** - Suggest new improvements\n🎮 **aRPG Community** - Discuss your favorite games",
            inline=False
        )
        
        embed.add_field(
            name="🔗 Join the Server",
            value="**[Click here to join our support server!](https://discord.gg/MA4eGN9Hbu)**\n\n*Get instant help from our community and development team.*",
            inline=False
        )
        
        embed.add_field(
            name="🚀 Quick Help",
            value="**Common Commands:**\n• `/arpg-status` - Check your settings\n• `/arpg-check-permissions` - Verify bot permissions\n• `/feedback` - Send feedback directly to developers",
            inline=False
        )
        
        embed.set_footer(
            text="We're here to help! 🤝",
            icon_url=interaction.user.avatar.url if interaction.user.avatar else None
        )
        
        try:
            await interaction.user.send(embed=embed)
            
            # Success response
            success_embed = discord.Embed(
                title="📬 Support Info Sent!",
                description=f"I've sent the support server link to your DMs! Join us for help and community discussions.",
                color=0x00AA88
            )
            success_embed.add_field(
                name="🔗 Direct Link",
                value="[discord.gg/MA4eGN9Hbu](https://discord.gg/MA4eGN9Hbu)",
                inline=False
            )
            
            await interaction.response.send_message(embed=success_embed, ephemeral=True)
        except discord.Forbidden:
            # Fallback for closed DMs
            embed.title = "🆘 Join Our Support Server!"
            embed.add_field(
                name="📬 DMs Closed?",
                value="*Your DMs appear to be closed, so here's the direct link:*",
                inline=False
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(
        name="feedback", description="Submit feedback for the bot developers"
    )
    async def feedback(self, interaction: discord.Interaction) -> None:
        """
        Open a modal where you can submit feedback directly to the bot owner.

        The feedback text (up to 256 characters) is forwarded privately to the application owner.

        :param interaction: The application command interaction context.
        """
        feedback_form = FeedbackForm()
        await interaction.response.send_modal(feedback_form)

        await feedback_form.wait()
        interaction = feedback_form.interaction
        
        # Beautiful thank you message
        thank_you_embed = discord.Embed(
            title="💜 Thank You for Your Feedback!",
            description="Your feedback has been successfully submitted to the development team. We appreciate you taking the time to help improve the bot!",
            color=0x5865F2,
            timestamp=discord.utils.utcnow()
        )
        
        thank_you_embed.add_field(
            name="📝 What Happens Next",
            value="• Your feedback will be reviewed by the development team\n• Important suggestions may be implemented in future updates\n• You may receive a response if we need clarification",
            inline=False
        )
        
        thank_you_embed.add_field(
            name="🔗 Stay Connected",
            value="Join our support server with `/support` for:\n• Feature discussions\n• Update announcements\n• Community feedback",
            inline=False
        )
        
        thank_you_embed.set_footer(
            text=f"Feedback submitted by {interaction.user.display_name} • aRPG Timeline Bot",
            icon_url=interaction.user.avatar.url if interaction.user.avatar else None
        )
        
        await interaction.response.send_message(embed=thank_you_embed)

        # Enhanced feedback notification to owner
        app_owner = (await self.bot.application_info()).owner
        feedback_embed = discord.Embed(
            title="💬 New User Feedback",
            description=f"**From:** {interaction.user} ({interaction.user.mention})\n**User ID:** `{interaction.user.id}`",
            color=0x5865F2,
            timestamp=discord.utils.utcnow()
        )
        
        feedback_embed.add_field(
            name="📝 Feedback Content",
            value=f"```\n{feedback_form.answer}\n```",
            inline=False
        )
        
        # Add server context if available
        if interaction.guild:
            feedback_embed.add_field(
                name="🏠 Server Context",
                value=f"**Server:** {interaction.guild.name}\n**Server ID:** `{interaction.guild.id}`\n**Member Count:** {interaction.guild.member_count or 'Unknown'}",
                inline=True
            )
        else:
            feedback_embed.add_field(
                name="🏠 Server Context",
                value="*Submitted via DMs*",
                inline=True
            )
        
        # Add user info
        feedback_embed.add_field(
            name="👤 User Info",
            value=f"**Created:** {discord.utils.format_dt(interaction.user.created_at, style='R')}\n**Type:** {'Bot' if interaction.user.bot else 'User'}",
            inline=True
        )
        
        feedback_embed.set_thumbnail(url=interaction.user.avatar.url if interaction.user.avatar else None)
        
        try:
            await app_owner.send(embed=feedback_embed)
        except discord.Forbidden:
            # Owner DMs closed; log and optionally post in a designated channel if configured later
            if hasattr(self.bot, "logger"):
                self.bot.logger.warning("Could not DM owner with feedback (DMs closed).")
        except Exception as e:
            if hasattr(self.bot, "logger"):
                self.bot.logger.error(f"Failed to forward feedback to owner: {e}")


async def setup(bot) -> None:
    await bot.add_cog(General(bot))
