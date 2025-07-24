import os
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
import requests
import asyncio
import random
import json
from fragchart import fragchart

NewIntents = discord.Intents.default()
NewIntents.message_content = True
NewIntents.presences = True
NewIntents.members = True
NewIntents.reactions = True

load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

client = discord.Client(intents=NewIntents)
bot = commands.Bot(command_prefix="--", intents=NewIntents)


@bot.event
async def on_ready():
    print(f'{bot.user} has connected to Discord!')
    for filename in os.listdir('./cogs'):
        if filename.endswith('.py'):
            await bot.load_extension(f'cogs.{filename[:-3]}')
            print(f"{filename} loaded!")

# sync commands to current server
@bot.command()
async def synccmd(ctx):
    if ctx.author.id == 188775984357572608:
        synced = await bot.tree.sync(guild=discord.Object(id=ctx.guild.id))
        await ctx.send(
            f"Synced {len(synced)} commands to the current server"
        )

# global sync
@bot.command()
async def globalsync(ctx):
    if ctx.author.id == os.getenv('OWNER_ID'):
        synced = await bot.tree.sync()
        await ctx.send(
            f"Synced {len(synced)} commands globally"
        )

# run token
bot.run(TOKEN)