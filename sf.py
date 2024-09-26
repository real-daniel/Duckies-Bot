import os
import discord
from discord.ext import commands
from dotenv import load_dotenv
# import requests
import asyncio
import random

riotAPIKey = "RGAPI-5287faaf-f999-46f5-a47c-0ad116e0a7bc"
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


@bot.command()
async def sfcalc(ctx, lv, start, end, sg=False, sc=False):
    sg = bool(sg)
    sc = bool(sc)
    lv = int(lv)
    start = int(start)
    end = int(end)
    sfstats = {
        0: {
            "success": 95,
            "fail": 5,
            "destroy": 0,
            "decrease": False
        },
        1: {
            "success": 90,
            "fail": 10,
            "destroy": 0,
            "decrease": False
        },
        2: {
            "success": 85,
            "fail": 15,
            "destroy": 0,
            "decrease": False
        },
        3: {
            "success": 85,
            "fail": 15,
            "destroy": 0,
            "decrease": False
        },
        4: {
            "success": 80,
            "fail": 20,
            "destroy": 0,
            "decrease": False
        },
        5: {
            "success": 75,
            "fail": 25,
            "destroy": 0,
            "decrease": False
        },
        6: {
            "success": 70,
            "fail": 30,
            "destroy": 0,
            "decrease": False
        },
        7: {
            "success": 65,
            "fail": 35,
            "destroy": 0,
            "decrease": False
        },
        8: {
            "success": 60,
            "fail": 40,
            "destroy": 0,
            "decrease": False
        },
        9: {
            "success": 55,
            "fail": 45,
            "destroy": 0,
            "decrease": False
        },
        10: {
            "success": 50,
            "fail": 50,
            "destroy": 0,
            "decrease": False
        },
        11: {
            "success": 45,
            "fail": 55,
            "destroy": 0,
            "decrease": False
        },
        12: {
            "success": 40,
            "fail": 60,
            "destroy": 0,
            "decrease": False
        },
        13: {
            "success": 35,
            "fail": 65,
            "destroy": 0,
            "decrease": False
        },
        14: {
            "success": 30,
            "fail": 70,
            "destroy": 0,
            "decrease": False
        },
        15: {
            "success": 30,
            "fail": 67.9,
            "destroy": 2.1,
            "decrease": False
        },
        16: {
            "success": 30,
            "fail": 67.9,
            "destroy": 2.1,
            "decrease": True
        },
        17: {
            "success": 30,
            "fail": 67.9,
            "destroy": 2.1,
            "decrease": True
        },
        18: {
            "success": 30,
            "fail": 67.2,
            "destroy": 2.8,
            "decrease": True
        },
        19: {
            "success": 30,
            "fail": 67.2,
            "destroy": 2.8,
            "decrease": True
        },
        20: {
            "success": 30,
            "fail": 63,
            "destroy": 7,
            "decrease": False
        },
        21: {
            "success": 30,
            "fail": 63,
            "destroy": 7,
            "decrease": True
        },
        22: {
            "success": 3,
            "fail": 77.6,
            "destroy": 19.4,
            "decrease": True
        },
        23: {
            "success": 2,
            "fail": 68.6,
            "destroy": 29.4,
            "decrease": True
        },
        24: {
            "success": 1,
            "fail": 59.4,
            "destroy": 39.6,
            "decrease": True
        }
    }
    totalCosts = []
    totalBooms = []
    for _ in range(1000):
        # THIS SETS ALL THE VARIALBES TO FRESH STARTING VALUES FOR EACH NEW ITERATION ZI WANG CHANG
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
        print(totalCost, start, currentStar, boomCount)
    avgCost = round(sum(totalCosts) / len(totalCosts), 2)
    avgBooms = round(sum(totalBooms) / len(totalBooms), 2)
    print(avgCost, avgBooms)
    await ctx.send(f"{avgCost}, {avgBooms}")


bot.run(TOKEN)
