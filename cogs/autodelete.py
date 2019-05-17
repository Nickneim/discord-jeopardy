import discord
import asyncio
import sqlite3
from datetime import datetime, timedelta
from discord.ext import commands


class TimeConverter(commands.RoleConverter):

    @staticmethod
    def split_numbers(s):
        split = []
        checking_number = True
        start = 0
        end = 0
        for c in s:
            if (c.isnumeric() and not checking_number) or (not c.isnumeric() and checking_number):
                split.append(s[start:end])
                checking_number = not checking_number
                start = end
            end += 1
        split.append(s[start:])
        return split

    time_intervals = {
        "second": 1,
        "minute": 60,
        "hour": 60*60,
        "day": 24*60*60,
        "week": 7*24*60*60,
        "year": 365*24*60,
        }
    for interval, time in list(time_intervals.items()):
        time_intervals[interval[0]] = time
        time_intervals[interval + 's'] = time

    async def convert(self, ctx, argument):
        arguments = [x.strip().lower() for x in self.split_numbers(argument)]
        arguments = [x for x in arguments if x]
        seconds = 0
        number = 0
        time_interval = 1
        checking_number = True
        for x in arguments:
            if checking_number:
                try:
                    number = int(x)
                except ValueError:
                    raise commands.BadArgument(f"'{x}' is not a number.")
            else:
                try:
                    time_interval = self.time_intervals[x]
                except KeyError:
                    raise commands.BadArgument(f"'{x}' is not a valid time interval.")
                seconds += number * time_interval
            checking_number = not checking_number
        if not checking_number:
            raise commands.BadArgument(f"You must associate a time interval to '{number}'.")
        return seconds


class AutoDeleteCog(commands.Cog):

    def __init__(self, bot):
        self.bot = bot
        self.database = sqlite3.connect('database.db')
        self.channels = {}
        self.db_cursor = self.database.cursor()
        self.db_cursor.execute("""CREATE TABLE IF NOT EXISTS autodelete
                                  (channel integer, guild integer, time_interval integer)""")
        self.database.commit()


    @commands.command(hidden=True)
    @commands.is_owner()
    async def restart_autodelete(self, ctx):
        for task in self.channels.values():
            task.cancel()
        self.channels = {}
        for (channel_id, time_interval) in self.db_cursor.execute(
            "SELECT channel, time_interval FROM autodelete"):
            channel = self.bot.get_channel(channel_id)
            if channel is None:
                await ctx.send(f"Couldn't find channel {channel_id}.")
                continue
            task = asyncio.create_task(self.autodelete_task(channel, time_interval))
            self.channels[channel.id] = task
            await ctx.send(f"Restarting autodelete in channel {channel} ({channel.guild}) with time_interval {time_interval}")
        await asyncio.gather(*self.channels.values())


    @commands.command(hidden=True)
    @commands.is_owner()
    async def delete_message(self, ctx, channel :discord.TextChannel, message_id : int):
        if not channel:
            await ctx.send("Couldn't find channel.")
        try:
            message = await channel.fetch_message(message_id)
        except discord.NotFound:
            await ctx.send("Couldn't find message.")
        except discord.Forbidden:
            await ctx.send("Not allowed to get message")
        
        try:
            await message.delete()
        except discord.Forbidden:
            await ctx.send("Not allowed to delete the mesage.")
        await ctx.send("Deleted.")


    @staticmethod
    def seconds_to_string(seconds):
        time_intervals = [
            ("year", 365*24*60*60),
            ("week", 7*24*60*60),
            ("day", 24*60*60),
            ("hour", 60*60),
            ("minute", 60)
            ]
        s = ""
        for name, interval in time_intervals:
            amount = seconds // interval
            seconds -= interval * amount
            if amount == 1:
                s += f"1 {name}, "
            elif amount:
                s += f"{amount} {name}s, "
        if seconds == 1:
            s += "1 second"
        elif seconds:
            s += f"{seconds} seconds"
        elif s:  # time > 0 but remaining seconds = 0, remove trailing ", "
            s = s[:-2]
        else:  # time = 0
            s = "0 seconds"
        return s


    @commands.command()
    async def autodelete(self, ctx, *, time_interval : TimeConverter):
        """
        Automatically delete any messages older than the time_interval in
        this channel.
        time_interval takes amounts of time like '2 weeks' or '1 hour 1m'
        """
        if time_interval < 1:
            return await ctx.send("That time interval is too short.")
        if not ctx.author.permissions_in(ctx.channel).manage_messages:
            return await ctx.send("You can't manage messages in this channel.")

        await ctx.send("Are you sure you want to delete everything older than " +
                        self.seconds_to_string(time_interval) + "? If so, say Confirm.")
        def is_confirmation(message):
            return (message.channel == ctx.channel
                    and ctx.author == message.author
                    and message.content == "Confirm")
        try:
            await self.bot.wait_for('message', timeout=15.0, check=is_confirmation)
        except asyncio.TimeoutError:
            return await ctx.send("Did not receive confirmation.")

        await ctx.send("Deleting previous messages.")
        messages_before = datetime.utcnow()-timedelta(seconds=time_interval)
        try:
            messages_deleted = await ctx.channel.purge(limit=None,
                                                       before=messages_before)
            messages_deleted = len(messages_deleted)
        except discord.Forbidden:
            return await ctx.send("Not allowed to delete messages.")
        except discord.HTTPException:
            return await ctx.send("Something went wrong deleting messages.")
        await ctx.send(f"Deleted {messages_deleted} messages before the time interval.")


        if ctx.channel.id in self.channels:
            self.channels[ctx.channel.id].cancel()
            del self.channels[ctx.channel.id]
        self.db_cursor.execute("DELETE FROM autodelete WHERE channel={} AND guild={}".format(
                                ctx.channel.id, ctx.guild.id))
        self.db_cursor.execute("INSERT INTO autodelete VALUES ({}, {}, {})".format(
                               ctx.channel.id, ctx.guild.id, time_interval))
        self.database.commit()
        task = asyncio.create_task(self.autodelete_task(ctx.channel, time_interval))
        self.channels[ctx.channel.id] = task
        
        await task
        

    async def autodelete_task(self, channel, time_interval):
        try:
            while True:
                # delete messages older than the time_interval
                messages_before = datetime.utcnow()-timedelta(seconds=time_interval)
                try:
                    await channel.purge(limit=None, before=messages_before)
                except discord.Forbidden:
                    return await channel.send("Not allowed to delete messages.")
                
                await asyncio.sleep(60)
                # next_message = time until next deletion
                next_message = None
                async for message in channel.history(after=messages_before, limit=1, oldest_first=True):
                    next_message = (message.created_at - messages_before).total_seconds()
                if next_message is None:
                    next_message = time_interval

                await asyncio.sleep(next_message)

        except asyncio.CancelledError:
            await channel.send("Cancelled previous autodelete.")

    @autodelete.error
    async def autodelete_error(self, ctx, error):
        if isinstance(error, commands.BadArgument):
            return await ctx.send(str(error))
        elif isinstance(error, commands.MissingRequiredArgument):
            return await ctx.send(f"Usage: {ctx.invoked_with} <time>")
        raise error
    
    @commands.command(aliases=["stop_autodelete"])
    async def cancel_autodelete(self, ctx):
        if not ctx.author.permissions_in(ctx.channel).manage_messages:
            return await ctx.send("You can't manage messages in this channel.")
        if ctx.channel.id in self.channels:
            self.channels[ctx.channel.id].cancel()
            del self.channels[ctx.channel.id]
        else:
            await ctx.send("Autodelete isn't active in this channel.")
        self.db_cursor.execute("DELETE FROM autodelete WHERE channel={} AND guild={}".format(
                                ctx.channel.id, ctx.guild.id))
        self.database.commit()


def setup(bot):
    bot.add_cog(AutoDeleteCog(bot))
