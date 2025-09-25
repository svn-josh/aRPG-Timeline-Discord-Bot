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
        embed = discord.Embed(title="Help", description="Slash Commands", color=0xBEBEFE)
        grouped = {}
        # Iterate over application commands in the tree
        for cmd in self.bot.tree.get_commands():
            if isinstance(cmd, app_commands.ContextMenu):
                continue
            # Owner-only filtering (heuristic via checks attribute on bound command)
            if not is_owner:
                checks = getattr(cmd, "checks", [])
                if any(getattr(ch, "__name__", "").startswith("is_owner") for ch in checks):
                    continue
            cog_name = getattr(getattr(cmd, 'binding', None), '__cog_name__', None) or 'Other'
            label = cog_name.title()
            line = f"/{cmd.name} - {(cmd.description or '').partition('\n')[0]}"
            grouped.setdefault(label, []).append(line)
        # Stable order: General first, then alphabetical
        ordered = []
        if 'General' in grouped:
            ordered.append('General')
        remaining = sorted(k for k in grouped.keys() if k != 'General')
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
            embed.add_field(name=section, value=f"```\n{block}```", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="ping", description="Check bot latency")
    async def ping(self, interaction: discord.Interaction) -> None:
        """
        Check the bot's current websocket latency to Discord.

        :param interaction: The application command interaction context.
        """
        embed = discord.Embed(
            title="🏓 Pong!",
            description=f"The bot latency is {round(self.bot.latency * 1000)}ms.",
            color=0xBEBEFE,
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="invite", description="Get the bot invite link")
    async def invite(self, interaction: discord.Interaction) -> None:
        """
        Provide a link for inviting the bot to another server via a direct message (falls back to ephemeral response if DMs are closed).

        :param interaction: The application command interaction context.
        """
        embed = discord.Embed(
            description=f"Invite me by clicking [here]({self.bot.invite_link}).",
            color=0xD75BF4,
        )
        try:
            await interaction.user.send(embed=embed)
            await interaction.response.send_message("I sent you a private message!", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="support", description="Get support server invite link")
    async def support(self, interaction: discord.Interaction) -> None:
        """
        Send the invite link to the bot's official support server via DM (falls back to ephemeral response if DMs are closed).

        :param interaction: The application command interaction context.
        """
        embed = discord.Embed(
            description=f"Join the support server for the bot by clicking [here](https://discord.gg/MA4eGN9Hbu).",
            color=0xD75BF4,
        )
        try:
            await interaction.user.send(embed=embed)
            await interaction.response.send_message("I sent you a private message!", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(
        name="feedback", description="Submit a feedback for the owners of the bot"
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
        await interaction.response.send_message(
            embed=discord.Embed(
                description="Thank you for your feedback, the owners have been notified about it.",
                color=0xBEBEFE,
            )
        )

        app_owner = (await self.bot.application_info()).owner
        feedback_embed = discord.Embed(
            title="New Feedback",
            description=(
                f"{interaction.user} (<@{interaction.user.id}>) has submitted a new feedback:\n```\n{feedback_form.answer}\n```"
            ),
            color=0xBEBEFE,
        )
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
