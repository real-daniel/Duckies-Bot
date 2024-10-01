import os
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
# import requests
import asyncio
import random
from sfstats import sfstats


class SfCalc(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="sfcalc", description="returns starforce stats for specified equipment. for safeguard/starcatch, write yes/no")
    async def sfcalc(self, interaction: discord.Interaction, lv: int, start:int, end:int, sg: str, sc: str, trials: int = 1000):
        sg = True if sg.lower() == "yes" else False
        sc = True if sc.lower() == "yes" else False
        lv = int(lv)
        start = int(start)
        end = int(end)
        totalCosts = []
        totalBooms = []
        for _ in range(trials):
            # THIS SETS ALL THE VARIABLES TO FRESH STARTING VALUES FOR EACH NEW ITERATION ZI WANG CHANG
            currentStar = start
            isDone = currentStar == end
            boomCount = 0
            totalCost = 0
            failPity = 0
            while not isDone:
                # THIS ROLLS THE 0-100 FLOAT ZI WANG CHANG
                roll = round(random.uniform(0, 100), 2)
                # THIS GETS THE ODDS FOR THE CURRENT STAR ZI WANG CHANG
                currentSuccess = sfstats[currentStar]["success"]
                currentFail = sfstats[currentStar]["success"] + sfstats[currentStar]["fail"]
                currentDestroy = sfstats[currentStar]["success"] + sfstats[currentStar]["fail"] + sfstats[currentStar][
                    "destroy"]
                currentDecrease = sfstats[currentStar]["decrease"]
                # THIS SETS THE COST BASED ON THE CURRENT STAR ZI WANG CHANG
                if 0 <= currentStar <= 9:
                    cost = 100 * round((lv ** 3) * (currentStar + 1) / 2500 + 10)
                elif currentStar == 10:
                    cost = 100 * round((lv ** 3) * (currentStar + 1) ** 2.7 / 40000 + 10)
                elif currentStar == 11:
                    cost = 100 * round((lv ** 3) * (currentStar + 1) ** 2.7 / 22000 + 10)
                elif currentStar == 12:
                    cost = 100 * round((lv ** 3) * (currentStar + 1) ** 2.7 / 15000 + 10)
                elif currentStar == 13:
                    cost = 100 * round((lv ** 3) * (currentStar + 1) ** 2.7 / 11000 + 10)
                elif currentStar == 14:
                    cost = 100 * round((lv ** 3) * (currentStar + 1) ** 2.7 / 7500 + 10)
                elif 15 <= currentStar <= 24:
                    cost = 100 * round((lv ** 3) * (currentStar + 1) ** 2.7 / 20000 + 10)
                # THIS ADDS THE COST TO THE TOTAL FOR THIS TAP ZI WANG CHANG
                totalCost += cost
                # 2 fail pity
                if failPity >= 2:
                    currentSuccess = 100
                    failPity = 0
                # THIS CHECKS THE ROLL AND DETERMINES THE OUTCOME ACCORDINGLY ZI WANG CHANG
                if roll <= currentSuccess:
                    currentStar += 1
                    failPity = 0
                elif currentSuccess < roll <= currentFail:
                    if currentDecrease:
                        currentStar += -1
                        failPity += 1
                    else:
                        failPity = 0
                elif currentFail < roll <= currentDestroy:
                    currentStar = 12
                    boomCount += 1
                # THIS CHECKS IF GOAL HAS BEEN REACHED ZI WANG CHANG
                isDone = currentStar == end
            # THIS ADDS COST AND BOOMS TO LIST OF TOTALS FOR STAT CALCULATIONS ZI WANG CHANG
            totalCosts.append(totalCost)
            totalBooms.append(boomCount)
        avgCost = round(sum(totalCosts) / len(totalCosts), 2)
        tmedianCost = totalCosts[int(len(totalCosts) / 2)]
        avgBooms = round(sum(totalBooms) / len(totalBooms), 2)
        medianBooms = totalBooms[int(len(totalCosts) / 2)]
        await interaction.response.send_message(f"{avgCost}, {avgBooms}")


async def setup(bot):
    await bot.add_cog(SfCalc(bot))