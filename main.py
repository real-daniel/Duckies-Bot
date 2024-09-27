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
import mysql.connector
from mysql.connector import errorcode

NewIntents = discord.Intents.default()
NewIntents.message_content = True
NewIntents.presences = True
NewIntents.members = True
NewIntents.reactions = True

load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')
SQLUSER = os.getenv('MYSQL_USER')
SQLPASS = os.getenv('MYSQL_PASS')
SQLDATABASE = os.getenv("MYSQL_DATABASE")
SQLHOST = os.getenv('MYSQL_HOST')

client = discord.Client(intents=NewIntents)
bot = commands.Bot(command_prefix="--", intents=NewIntents)






def load_data():
    try:
        # check if the file exists and is not empty
        if os.path.exists("data.json") and os.path.getsize("data.json") > 0:
            with open("data.json", "r") as f:
                return json.load(f)
        else:
            # return an empty dictionary if the file is empty or doesn't exist
            return {}
    except json.JSONDecodeError:
        # if the file is corrupted or not valid JSON, initialize as an empty dictionary
        return {}


# initialize fragdata
fragdata = load_data()


#calc frags
def fragcalc(skill, level):
    currentSkill = fragchart[skill]
    fragSpent = 0
    fragTotal = 0
    start = 1 if skill == "origin" else 0
    end = level
    for i in range (start, end):
        fragSpent += currentSkill[i][1]
    for i in range (start, 30):
        fragTotal += currentSkill[i][1]
    return [fragSpent, fragTotal]


@bot.event
async def on_ready():
    print(f'{bot.user} has connected to Discord!')


# sync commands to current server
@bot.command()
async def synccmd(ctx):
    if ctx.author.id == 188775984357572608:
        synced = await ctx.bot.tree.sync(guild=discord.Object(id=ctx.guild.id))
        await ctx.send(
            f"Synced {len(synced)} commands to the current server"
        )


# global sync
@bot.command()
async def globalsync(ctx):
    if ctx.author.id == 188775984357572608:
        synced = await ctx.bot.tree.sync()
        await ctx.send(
            f"Synced {len(synced)} commands globally"
        )


# registers current user for tracking fragments
@bot.tree.command(name="fragregister", description="Register your user for frag tracking")
async def fragregister(interaction: discord.Interaction, userclass: str):
    if interaction.user.id not in fragdata.keys():
        fragdata[str(interaction.user.id)] = {"class": userclass,
                                         "origin": 0,
                                         "enhance1": 0,
                                         "enhance2": 0,
                                         "enhance3": 0,
                                         "enhance4": 0,
                                         "boost1": 0,
                                         "boost2": 0,
                                         "common": 0,
                                         }
        with open("data.json", "w") as json_file:
            json.dump(fragdata, json_file, indent=4)
        await interaction.response.send_message(
            f"{interaction.user.name} has successfully registered as a(n) {userclass}!")
    else:

        await interaction.response.send_message(
            f"{interaction.user.name} is already a registered user. Please use /fragupdate to update your info!")

    try:
        # SQL Database Cursor
        cnx = mysql.connector.connect(user=SQLUSER, password=SQLPASS,
                                      host=SQLHOST,
                                      collation='utf8mb4_unicode_ci',
                                      database=SQLDATABASE)
        print("Connected to Database.")

        # Create the cursor object to be able to execute commands in the SQL Database.
        cursor = cnx.cursor(buffered=True)
        # Get the user data from the database
        cursor.execute(f"SELECT * FROM `users` WHERE discordID = '{interaction.user.id}'")
        print(cursor.rowcount)

        # Confirm the user exists and add him if he does not.
        if cursor.rowcount == 0:
            cursor.reset()
            user_to_add = f"INSERT INTO `users`(`discordID`, `class`) VALUES ('{interaction.user.id}','{userclass}')"
            cursor.execute(user_to_add)
            cnx.commit()
            cnx.close()
        else:
            await interaction.response.send_message(
                f"{interaction.user.name} is already a registered user. Please use /fragupdate to update your info!")
    except mysql.connector.Error as err:
        if err.errno == errorcode.ER_ACCESS_DENIED_ERROR:
            print("Something is wrong with your user name or password")
        elif err.errno == errorcode.ER_BAD_DB_ERROR:
            print("Database does not exist")
        else:
            print(err)
    else:
        cnx.close()











# updates current user's fragcount
@bot.tree.command(name="fragupdate", description="Update your frag count")
async def fragupdate(interaction: discord.Interaction, origin: int, enhance1: int, enhance2: int, enhance3: int,
                     enhance4: int, boost1: int, boost2: int, common: int):
    user_data = fragdata[str(interaction.user.id)]
    user_data["origin"] = origin
    user_data["enhance1"] = enhance1
    user_data["enhance2"] = enhance2
    user_data["enhance3"] = enhance3
    user_data["enhance4"] = enhance4
    user_data["boost1"] = boost1
    user_data["boost2"] = boost2
    user_data["common"] = common
    with open("data.json", "w") as json_file:
        json.dump(fragdata, json_file, indent=4)
    await interaction.response.send_message(f"{interaction.user.name} has successfully updated.")


# displays user info
@bot.tree.command(name="fraginfo", description="Show user info")
async def fraginfo(interaction: discord.Interaction):
    user_data = fragdata[str(interaction.user.id)]
    fragdatalist = [
        fragcalc("origin", user_data["origin"])[0],
        fragcalc("enhance", user_data["enhance1"])[0],
        fragcalc("enhance", user_data["enhance2"])[0],
        fragcalc("enhance", user_data["enhance3"])[0],
        fragcalc("enhance", user_data["enhance4"])[0],
        fragcalc("boost", user_data["boost1"])[0],
        fragcalc("boost", user_data["boost2"])[0],
        fragcalc("common", user_data["common"])[0]
    ]
    embed = discord.Embed(
        colour=discord.Colour.dark_teal(),
        title=f"User Info - {interaction.user.name}",
        description=f"""
        Class: {user_data["class"]}\n
        Origin: {user_data["origin"]} | Frags Spent: {fragdatalist[0]}\n
        Enhance 1: {user_data["enhance1"] } | Frags Spent: {fragdatalist[1]}\n
        Enhance 2: {user_data["enhance2"]}  | Frags Spent: {fragdatalist[2]}\n
        Enhance 3: {user_data["enhance3"]} | Frags Spent: {fragdatalist[3]}\n
        Enhance 4: {user_data["enhance4"]} | Frags Spent: {fragdatalist[4]}\n
        Boost 1: {user_data["boost1"]} | Frags Spent: {fragdatalist[5]}\n
        Boost 2: {user_data["boost2"]} | Frags Spent: {fragdatalist[6]}\n
        Common: {user_data["common"]} | Frags Spent: {fragdatalist[7]}\n
        Total Frags Spent: {sum(fragdatalist)}
"""
    )
    await interaction.response.send_message(embed=embed)


bot.run(TOKEN)
