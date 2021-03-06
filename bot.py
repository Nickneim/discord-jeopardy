import discord
from discord.ext import commands
import logging

import os
import sys
import traceback

initial_extensions = ['cogs.browser',
                      'cogs.game',
                      'cogs.owner',
                      'cogs.others',
                      'cogs.role',
                      'cogs.autodelete']

intents = discord.Intents.default()
intents.members = True
logging.basicConfig(filename='jeopardy.log', level=logging.INFO)
prefix = ("t.", "T.")
bot = commands.Bot(command_prefix=prefix, intents=intents)

if __name__ == '__main__':
    for extension in initial_extensions:
        try:
            bot.load_extension(extension)
        except Exception as e:
            print(f'Failed to load extension {extension}.', file=sys.stderr)
            traceback.print_exc()

@bot.event
async def on_ready():
    """http://discordpy.readthedocs.io/en/rewrite/api.html#discord.on_ready"""

    print(f'\n\nLogged in as: {bot.user.name} - {bot.user.id}\nVersion: {discord.__version__}\n')
    
    print(f'Successfully logged in and booted...!')

bot.run(os.environ['TOKEN'], bot=True, reconnect=True)
