import os
import certifi

# Configure Python to use certifi's trusted CA certificates
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


# test function for polling, need to make custom functionality
# either assign roles when creating poll to restrict voting, also status tracking of voted already
@bot.command()
async def poll(ctx, *, question):
    embed = discord.Embed(title="Poll!", description=question, color=discord.Color.green())
    poll_message = await ctx.send(embed=embed)
    await poll_message.add_reaction("👍")
    await poll_message.add_reaction("👎")

#TODO: create a custom poll look into custom UI elements
#TODO: think about the best way to do a poll, what will be returned? - Message in server when poll expires


bot.run(token, log_handler=handler, log_level=logging.DEBUG)