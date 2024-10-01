import json
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
fragData = load_data()

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


class FragCalc(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="fragregister", description="register your user for tracking frags")
    async def fragregister(self, interaction: discord.Interaction, userclass: str):
        if interaction.user.id not in fragData.keys():
            fragData[str(interaction.user.id)] = {"class": userclass,
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
                json.dump(fragData, json_file, indent=4)
            await interaction.response.send_message(
                f"{interaction.user.name} has successfully registered as a(n) {userclass}!"
            )
        else:

            await interaction.response.send_message(
                f"{interaction.user.name} is already a registered user. Please use /fragupdate to update your info!"
            )

    @app_commands.command(name="fragupdate", description="Update your frag count")
    async def fragupdate(interaction: discord.Interaction, origin: int, enhance1: int, enhance2: int, enhance3: int,
                         enhance4: int, boost1: int, boost2: int, common: int):
        user_data = fragData[str(interaction.user.id)]
        user_data["origin"] = origin
        user_data["enhance1"] = enhance1
        user_data["enhance2"] = enhance2
        user_data["enhance3"] = enhance3
        user_data["enhance4"] = enhance4
        user_data["boost1"] = boost1
        user_data["boost2"] = boost2
        user_data["common"] = common
        with open("data.json", "w") as json_file:
            json.dump(fragData, json_file, indent=4)
        await interaction.response.send_message(f"{interaction.user.name} has successfully updated.")

@app_commands.command(name="fraginfo", description="Show user info")
async def fraginfo(interaction: discord.Interaction):
    user_data = fragData[str(interaction.user.id)]
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

    embed = discord.Embed(title=f"User Name - {interaction.user.name}", colour=discord.Colour.dark_teal())
    embed.add_field(name="V-Enhance Cores", value=f"""Enhance 1: {user_data["enhance1"] } | Frags Spent: {fragdatalist[1]}\nEnhance 2: {user_data["enhance2"]}  | Frags Spent: {fragdatalist[2]}\nEnhance 3: {user_data["enhance3"]} | Frags Spent: {fragdatalist[3]}\nEnhance 4: {user_data["enhance4"]} | Frags Spent: {fragdatalist[4]}""", inline=True)
    embed.add_field(name="Mastery Cores", value=f"""Boost 1: {user_data["boost1"]} | Frags Spent: {fragdatalist[5]}\nBoost 2: {user_data["boost2"]} | Frags Spent: {fragdatalist[6]}""", inline=True)
    embed.add_field(name="Common Cores", value=f"""Common: {user_data["common"]} | Frags Spent: {fragdatalist[7]}""", inline=True)
    embed.add_field(name="Total Spent", value=f"""{sum(fragdatalist)} Fragments""")

    await interaction.response.send_message(embed=embed)


async def setup(bot):
    await bot.add_cog(FragCalc(bot))