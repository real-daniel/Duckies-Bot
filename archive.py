currentsummoner = {}
riotAPIKey = "RGAPI-5287faaf-f999-46f5-a47c-0ad116e0a7bc"

@bot.command()
async def loadsummoner(ctx, name):
    global currentsummoner
    response = requests.get(f"https://na1.api.riotgames.com/lol/summoner/v4/summoners/by-name/{name}?api_key={riotAPIKey}")
    currentsummoner = response.json()
    if response.status_code == 200:
        await ctx.send("Summoner loaded successfully!")
    else:
        await ctx.send("Error.")


@bot.command()
async def summonerlevel(ctx):
    await ctx.send(currentsummoner["summonerLevel"])


@bot.command()
async def summonerpuuid(ctx):
    await ctx.send(currentsummoner["puuid"])


@bot.command()
async def summonerid(ctx):
    await ctx.send(currentsummoner["id"])


@bot.command()
async def summoneraccountid(ctx):
    await ctx.send(currentsummoner["accountId"])


@bot.command()
async def matchhistory(ctx, puuid):
    response = requests.get(f"https://americas.api.riotgames.com/lol/match/v5/matches/by-puuid/{puuid}/ids?start=0&count=20&api_key={riotAPIKey}")
    await ctx.send(response.json())


@bot.command()
async def davidtest(ctx, name, count=20):
    response = requests.get(
        f"https://na1.api.riotgames.com/lol/summoner/v4/summoners/by-name/{name}?api_key={riotAPIKey}")
    summoner = response.json()
    if response.status_code == 200:
        await ctx.send("Summoner loaded successfully!")
        puuid = summoner["puuid"]
        matches = requests.get(
            f"https://americas.api.riotgames.com/lol/match/v5/matches/by-puuid/{puuid}/ids?start=0&count={count}&api_key={riotAPIKey}").json()
        damageValues = []
        for match in range(0, count):
            currentmatch = requests.get(f"https://americas.api.riotgames.com/lol/match/v5/matches/{matches[match]}?api_key={riotAPIKey}").json()
            try:
                for player in currentmatch["info"]["participants"]:
                    if player["summonerName"].lower() == name.lower():
                        damageValues.append(player["totalDamageDealtToChampions"])
            except KeyError:
                continue
        average = round(sum(damageValues)/len(damageValues))
        if average > 20000:
            await ctx.send(f"You have passed the David Test, with an average damage of {average} in the last {len(damageValues)} games!")
        else:
            await ctx.send(f"Bro you are so shit you did {average} in the last {len(damageValues)} games lmao lost to David...")
    else:
        await ctx.send("No summoner with that name.")


@bot.command()
async def kyscheck(ctx, name, count=20):
    response = requests.get(
        f"https://na1.api.riotgames.com/lol/summoner/v4/summoners/by-name/{name}?api_key={riotAPIKey}")
    summoner = response.json()
    if response.status_code == 200:
        await ctx.send("Summoner loaded successfully!")
        puuid = summoner["puuid"]
        matches = requests.get(
            f"https://americas.api.riotgames.com/lol/match/v5/matches/by-puuid/{puuid}/ids?start=0&count={count}&api_key={riotAPIKey}").json()
        kysCount = []
        for match in range(0, count):
            currentmatch = requests.get(
                f"https://americas.api.riotgames.com/lol/match/v5/matches/{matches[match]}?api_key={riotAPIKey}").json()
            try:
                for player in currentmatch["info"]["participants"]:
                    if player["summonerName"].lower() == name.lower():
                        try:
                            kysCount.append(player["baitPings"])
                        except KeyError:
                            pass
            except KeyError:
                continue
        total = sum(kysCount)
        await ctx.send(f"You have KYS pinged {total} times in {len(kysCount)} games.")
    else:
        await ctx.send("No summoner with that name.")


@bot.command()
async def csgap(ctx, name, count=20):
    response = requests.get(
        f"https://na1.api.riotgames.com/lol/summoner/v4/summoners/by-name/{name}?api_key={riotAPIKey}")
    summoner = response.json()
    if response.status_code == 200:
        await ctx.send("Summoner loaded successfully!")
        puuid = summoner["puuid"]
        matches = requests.get(
            f"https://americas.api.riotgames.com/lol/match/v5/matches/by-puuid/{puuid}/ids?start=0&count={count}&api_key={riotAPIKey}").json()
        cscount = []
        for match in range(0, count):
            currentmatch = requests.get(
                f"https://americas.api.riotgames.com/lol/match/v5/matches/{matches[match]}?api_key={riotAPIKey}").json()
            try:
                for player in currentmatch["info"]["participants"]:
                    if player["summonerName"].lower() == name.lower():
                        cscount.append(player["challenges"]["maxCsAdvantageOnLaneOpponent"])
            except KeyError:
                continue
        total = round(sum(cscount)/len(cscount))
        await ctx.send(f"Your average max CS gap is {total} in {len(cscount)} games.")
    else:
        await ctx.send("No summoner with that name.")


@bot.event
async def on_message(message):
    message.content = message.content.lower()
    if message.content == "ni":
        if random.randint(1,100) == 1:
            msg = await message.channel.send("gger")
            await asyncio.sleep(2)
            await msg.delete()
            await message.delete()
        else:
            msg = await message.channel.send("ce")
            await asyncio.sleep(2)
            await msg.delete()
            await message.delete()
    elif message.content == "nigger":
        await message.channel.send("NO RACISM!!!!!!!!!!!!!!!!!!!!")
        await message.channel.send("NO RACISM!!!!!!!!!!!!!!!!!!!!")
        await message.channel.send("NO RACISM!!!!!!!!!!!!!!!!!!!!")
        await message.channel.send("NO RACISM!!!!!!!!!!!!!!!!!!!!")
        await message.channel.send("NO RACISM!!!!!!!!!!!!!!!!!!!!")
        await message.delete()
    elif "wap" in message.content:
        await message.channel.send("kys")
    await bot.process_commands(message)
