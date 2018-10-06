import discord
from discord.ext import commands
from difflib import SequenceMatcher

class OthersCog:
    def __init__(self, bot):
        self.bot = bot
        self.owner = None

    async def __before_invoke(self, ctx):
        print("{0.guild} #{0.channel.id}, {0.author}: {0.content}".format(ctx.message))

    @commands.command()
    async def compare(self, ctx, a:str, b:str):
        await ctx.send(SequenceMatcher(None, a, b).ratio())

    @commands.command()
    async def duece(self, ctx):
        await ctx.send("duece is great, everyone else is ok :)")

    @commands.command()
    async def ping(self, ctx):
        """Get the latency to Nico's far away computer"""
        # Get the latency of the bot
        latency = self.bot.latency # Included in the Discord.py library
        # Send it to the user
        await ctx.send("{:.3f}s".format(latency))

    @commands.command()
    async def nicochat(self, ctx, *, msg:str):
        if self.owner is None:
            appinfo = await self.bot.application_info()
            self.owner = appinfo.owner
        print(self.owner)
        await self.owner.send("{0.guild} #{0.channel.id}, {0.author}: {1}".format(ctx.message, msg))

    @commands.command(hidden=True)
    @commands.is_owner()
    async def sendchannel(self, ctx, channelid:int, *, downmessage:str):
        c = self.bot.get_channel(channelid)
        try:
            await c.send(downmessage)
        except (discord.HTTPException, discord.Forbidden) as e:
            print(e)

def setup(bot):
    bot.add_cog(OthersCog(bot))
