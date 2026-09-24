"""
Bot/scheduling.py

A shared, per-server calendar:

  !calendar
    -> sends a day-select dropdown + prev/next month buttons
    -> picking a day opens a Modal for event title + time (HH:MM)
    -> on submit, the event is saved to the "events" table for this
       server (guild) and announced in the channel for everyone to see

  !events
    -> lists the server's upcoming shared events

  !cancelevent <event_id>
    -> deletes an event, only if you were the one who created it

Wire it into main.py with:

    from Bot.scheduling import setup_scheduling
    setup_scheduling(bot)
"""

import asyncio
import calendar
import re
from datetime import datetime

import discord
from discord.ext import commands

from Bot.database import save_event, get_upcoming_events, delete_event

MONTH_NAMES = [
    "", "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]

TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")  # HH:MM, 24-hour


class TimeModal(discord.ui.Modal, title="When should this happen?"):
    """Popup form for title + time, shown after a day is picked."""

    def __init__(self, guild_id: int, year: int, month: int, day: int):
        super().__init__()
        self.guild_id = guild_id
        self.year = year
        self.month = month
        self.day = day

        self.event_title = discord.ui.TextInput(
            label="Event title",
            placeholder="e.g. Movie night",
            max_length=100,
            required=True,
        )
        self.event_time = discord.ui.TextInput(
            label="Time (24-hour, HH:MM)",
            placeholder="e.g. 19:30",
            max_length=5,
            required=True,
        )
        self.event_notes = discord.ui.TextInput(
            label="Notes (optional)",
            style=discord.TextStyle.paragraph,
            required=False,
        )

        self.add_item(self.event_title)
        self.add_item(self.event_time)
        self.add_item(self.event_notes)

    async def on_submit(self, interaction: discord.Interaction):
        match = TIME_RE.match(self.event_time.value.strip())
        if not match:
            await interaction.response.send_message(
                "That doesn't look like a valid time. Use 24-hour HH:MM, e.g. `19:30`.",
                ephemeral=True,
            )
            return

        hour, minute = int(match.group(1)), int(match.group(2))
        scheduled_dt = datetime(self.year, self.month, self.day, hour, minute)

        event_id = await asyncio.to_thread(
            save_event,
            str(self.guild_id),
            str(interaction.user.id),
            self.event_title.value,
            scheduled_dt,
            self.event_notes.value or None,
        )

        embed = discord.Embed(
            title="📅 Event scheduled",
            description=self.event_title.value,
            color=discord.Color.blurple(),
        )
        embed.add_field(name="When", value=scheduled_dt.strftime("%A, %B %d %Y at %H:%M"))
        if self.event_notes.value:
            embed.add_field(name="Notes", value=self.event_notes.value, inline=False)
        embed.set_footer(text=f"Event #{event_id} • scheduled by {interaction.user.display_name}")

        # Non-ephemeral: everyone in the channel sees the new shared event.
        await interaction.response.send_message(embed=embed)


class DaySelect(discord.ui.Select):
    """Dropdown listing every day in the currently displayed month."""

    def __init__(self, guild_id: int, year: int, month: int):
        self.guild_id = guild_id
        self.year = year
        self.month = month

        days_in_month = calendar.monthrange(year, month)[1]
        options = [
            discord.SelectOption(
                label=f"{day} ({calendar.day_abbr[calendar.weekday(year, month, day)]})",
                value=str(day),
            )
            for day in range(1, days_in_month + 1)
        ][:25]  # Discord caps select menus at 25 options

        super().__init__(
            placeholder=f"Pick a day in {MONTH_NAMES[month]} {year}...",
            options=options,
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        day = int(self.values[0])
        # Opening a modal must be the *first* response to this interaction.
        await interaction.response.send_modal(TimeModal(self.guild_id, self.year, self.month, day))


class CalendarView(discord.ui.View):
    """The calendar popup: day dropdown + month navigation.

    Any member of the server can open one with !calendar, but once open,
    only the person who ran the command can drive that particular picker
    (prevents multiple people fighting over the same message). The event
    it produces is still saved to the shared, server-wide calendar.
    """

    def __init__(self, owner_id: int, guild_id: int, year: int, month: int):
        super().__init__(timeout=120)
        self.owner_id = owner_id
        self.guild_id = guild_id
        self.year = year
        self.month = month
        self._build_items()

    def _build_items(self):
        self.clear_items()
        self.add_item(DaySelect(self.guild_id, self.year, self.month))
        self.add_item(self.prev_month)
        self.add_item(self.next_month)
        self.add_item(self.cancel)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "This calendar isn't yours — run `!calendar` to get your own.",
                ephemeral=True,
            )
            return False
        return True

    def _shift_month(self, delta: int):
        self.month += delta
        if self.month > 12:
            self.month = 1
            self.year += 1
        elif self.month < 1:
            self.month = 12
            self.year -= 1

    async def _refresh(self, interaction: discord.Interaction):
        self._build_items()
        embed = discord.Embed(
            title="Schedule an event",
            description=f"**{MONTH_NAMES[self.month]} {self.year}**\nPick a day below.",
            color=discord.Color.green(),
        )
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="◀ Prev month", style=discord.ButtonStyle.secondary, row=1)
    async def prev_month(self, interaction: discord.Interaction, button: discord.ui.Button):
        self._shift_month(-1)
        await self._refresh(interaction)

    @discord.ui.button(label="Next month ▶", style=discord.ButtonStyle.secondary, row=1)
    async def next_month(self, interaction: discord.Interaction, button: discord.ui.Button):
        self._shift_month(1)
        await self._refresh(interaction)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger, row=1)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Scheduling cancelled.", embed=None, view=None)
        self.stop()


def setup_scheduling(bot: commands.Bot):
    """Call this once from main.py to register the calendar commands."""

    @bot.command(name="calendar")
    async def open_calendar(ctx: commands.Context):
        if ctx.guild is None:
            await ctx.send("The shared calendar only works inside a server, not DMs.")
            return

        now = datetime.now()
        embed = discord.Embed(
            title="Schedule an event",
            description=f"**{MONTH_NAMES[now.month]} {now.year}**\nPick a day below.",
            color=discord.Color.green(),
        )
        view = CalendarView(ctx.author.id, ctx.guild.id, now.year, now.month)
        await ctx.send(embed=embed, view=view)

    @bot.command(name="events")
    async def list_events(ctx: commands.Context):
        if ctx.guild is None:
            await ctx.send("The shared calendar only works inside a server, not DMs.")
            return

        rows = await asyncio.to_thread(get_upcoming_events, str(ctx.guild.id))

        if not rows:
            await ctx.send("No upcoming events scheduled. Use `!calendar` to add one.")
            return

        embed = discord.Embed(title="📅 Upcoming events", color=discord.Color.blurple())
        for event_id, creator_id, title, event_datetime, notes in rows:
            when = datetime.fromisoformat(event_datetime).strftime("%a %b %d, %H:%M")
            creator = ctx.guild.get_member(int(creator_id))
            creator_name = creator.display_name if creator else "Unknown"
            value = f"{when} • added by {creator_name}"
            if notes:
                value += f"\n{notes}"
            embed.add_field(name=f"#{event_id} — {title}", value=value, inline=False)

        embed.set_footer(text="Use !cancelevent <id> to remove one you created.")
        await ctx.send(embed=embed)

    @bot.command(name="cancelevent")
    async def cancel_event(ctx: commands.Context, event_id: int = None):
        if ctx.guild is None:
            await ctx.send("The shared calendar only works inside a server, not DMs.")
            return

        if event_id is None:
            await ctx.send("Usage: `!cancelevent <event_id>` (see the ID with `!events`)")
            return

        deleted = await asyncio.to_thread(
            delete_event, event_id, str(ctx.guild.id), str(ctx.author.id)
        )

        if deleted:
            await ctx.send(f"Event #{event_id} cancelled.")
        else:
            await ctx.send(
                f"Couldn't cancel event #{event_id} — "
                "it doesn't exist, or you weren't the one who created it."
            )