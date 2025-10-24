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
from data.fragchart import fragchart


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
    fragsRemaining = fragTotal - fragSpent
    return [fragSpent, fragsRemaining]


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
                                                  "boost3": 0,
                                                  "boost4": 0,
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
    async def fragupdate(self, interaction: discord.Interaction, origin: int, enhance1: int, enhance2: int, enhance3: int,
                         enhance4: int, boost1: int, boost2: int, boost3: int, boost4: int, common: int):
        user_data = fragData[str(interaction.user.id)]
        user_data["origin"] = origin
        user_data["enhance1"] = enhance1
        user_data["enhance2"] = enhance2
        user_data["enhance3"] = enhance3
        user_data["enhance4"] = enhance4
        user_data["boost1"] = boost1
        user_data["boost2"] = boost2
        user_data["boost3"] = boost3
        user_data["boost4"] = boost4
        user_data["common"] = common
        with open("data.json", "w") as json_file:
            json.dump(fragData, json_file, indent=4)
        await interaction.response.send_message(f"{interaction.user.name} has successfully updated.")

    @app_commands.command(name="fraginfo", description="Show user info")
    async def fraginfo(self, interaction: discord.Interaction):
        try:
            user_data = fragData[str(interaction.user.id)]
        except KeyError:
            await interaction.response.send_message("No user registered")
        else:
            fragdatalist = [
                fragcalc("origin", user_data["origin"]),
                fragcalc("enhance", user_data["enhance1"]),
                fragcalc("enhance", user_data["enhance2"]),
                fragcalc("enhance", user_data["enhance3"]),
                fragcalc("enhance", user_data["enhance4"]),
                fragcalc("boost", user_data["boost1"]),
                fragcalc("boost", user_data["boost2"]),
                fragcalc("boost", user_data["boost3"]),
                fragcalc("boost", user_data["boost4"]),
                fragcalc("common", user_data["common"])
            ]
            totalSpent = 0
            totalRemaining = 0
            for skill in fragdatalist:
                totalSpent += skill[0]
                totalRemaining += skill[1]
            embed = discord.Embed(title=f"User Name - {interaction.user.name}", colour=discord.Colour.dark_teal())
            embed.add_field(
                name="**★ ORIGIN ★**",
                value=f"""
                        **Origin**: {user_data["origin"]} | **Frags Spent**: {fragdatalist[0][0]} | **Frags Remaining**: {fragdatalist[0][1]}\n\u200b""",
                inline=False)
            embed.add_field(
                name="**★ V-ENHANCE CORES ★**",
                value=f"""
                **Enhance 1**: {user_data["enhance1"]} | **Frags Spent**: {fragdatalist[1][0]} | **Frags Remaining**: {fragdatalist[1][1]}\n
                **Enhance 2**: {user_data["enhance2"]} | **Frags Spent**: {fragdatalist[2][0]} | **Frags Remaining**: {fragdatalist[2][1]}\n
                **Enhance 3**: {user_data["enhance3"]} | **Frags Spent**: {fragdatalist[3][0]} | **Frags Remaining**: {fragdatalist[3][1]}\n
                **Enhance 4**: {user_data["enhance4"]} | **Frags Spent**: {fragdatalist[4][0]} | **Frags Remaining**: {fragdatalist[4][1]}\n\u200b""",
                inline=False)
            embed.add_field(
                name="**★ MASTERY CORES ★**",
                value=f"""
                **Mastery 1**: {user_data["boost1"]} | **Frags Spent**: {fragdatalist[5][0]} | **Frags Remaining**: {fragdatalist[5][1]}\n
                **Mastery 2**: {user_data["boost2"]} | **Frags Spent**: {fragdatalist[6][0]} | **Frags Remaining**: {fragdatalist[6][1]}\n
                **Mastery 3**: {user_data["boost1"]} | **Frags Spent**: {fragdatalist[5][0]} | **Frags Remaining**: {fragdatalist[5][1]}\n
                **Mastery 4**: {user_data["boost2"]} | **Frags Spent**: {fragdatalist[6][0]} | **Frags Remaining**: {fragdatalist[6][1]}\n\u200b""",
                inline=False)
            embed.add_field(
                name="**★ COMMON CORES ★**",
                value=f"""
                **Common**: {user_data["common"]} | **Frags Spent**: {fragdatalist[7][0]} | **Frags Remaining**: {fragdatalist[7][1]}\n\u200b""",
                inline=False)
            embed.add_field(
                name="**★ TOTALS ★**",
                value=f"""
                **Total Spent**: {totalSpent} Fragments\n
                **Total Remaining**: {totalRemaining} Fragments""",
                inline=False)

            await interaction.response.send_message(embed=embed)


async def setup(bot):
    await bot.add_cog(FragCalc(bot))