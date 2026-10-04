import asyncio
import os
import certifi

from Bot.api import get_owned_games
from Bot.database import save_user, replace_user_games, initialize_database

# Configure Python to use certifi's trusted CA certificates
# workaround for SSL certificate validation error
os.environ["SSL_CERT_FILE"] = certifi.where()

import discord
from discord.ext import commands
import logging
from dotenv import load_dotenv

# loading the env file
load_dotenv()

# extracting the token from file
token = os.getenv('DISCORD_TOKEN')

# setting up logging for bot
handler = logging.FileHandler(filename='discord.log', encoding='utf-8', mode='w')

# enabling intents, adjust to add functionality
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

# prefix for commands !, so !hello for example
bot = commands.Bot(command_prefix='!', intents=intents)

@bot.event
async def on_ready():
    print(f"The {bot.user.name} is ready")


# greets the new member of the server
@bot.event
async def on_member_join(member):
    await member.send(f"Welcome to the server{member.name}!")


@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    # continue handling messages
    await bot.process_commands(message)


# simple echo command of Hello {username}
@bot.command()
async def hello(ctx):
    await ctx.send(f"Hello {ctx.author.name}!")

# command for storing a users initial game library, must be run for user to have games attached to them
@bot.command(name="library")
async def library(ctx, steam_id: str = None):

    # message to inform how to use command
    if steam_id is None:
        await ctx.send("Usage: !library <steamid>")
        return

    # simple error handling for improper steam id
    if not steam_id.isdigit():
        await ctx.send("Please enter a valid Steam ID.")
        return

    if len(steam_id) != 17:
        await ctx.send("Please enter a valid Steam ID.")
        return

    await ctx.send("Retrieving your Steam library. Please wait...")

    # running the api request to fetch games list
    games = await asyncio.to_thread(get_owned_games,steam_id)

    if games is None:
        await ctx.send(
            "Failed to retrieve your Steam library. "
            "Please try again later."
        )
        return

    # successful API response with no games.
    if not games:
        await ctx.send(
            "No games were returned. "
            "Your profile may be private, "
            "or your library may be empty."
        )
        return

    discord_id = str(ctx.author.id)
    display_name = ctx.author.display_name

    # save the Discord user and Steam ID. maybe don't even need display name
    await asyncio.to_thread(save_user,discord_id, steam_id, display_name)

    # replace the users existing library.
    await asyncio.to_thread(replace_user_games,discord_id, games)

    # display amount of games added to library
    await ctx.send(f"Successfully saved {len(games)} games" f"for {ctx.author.mention}!")


# test function for polling, need to make custom functionality
# assign roles when creating poll to restrict voting?
@bot.command()
async def poll(ctx, *, question):

    embed = discord.Embed(title="Poll!", description=question, color=discord.Color.green())
    poll_message = await ctx.send(embed=embed)

    await poll_message.add_reaction("👍")
    await poll_message.add_reaction("👎")

#TODO create a poll from participants
#TODO send message to participants after poll has expired with results.
# TODO create a command that shows commands and explanations

if __name__ == "__main__":
    bot.run(token)
    initialize_database()
