import discord
from discord.ext import commands

import asyncio
import aiohttp
import random
import logging
from cogs.utilities import jservice_get_json, is_valid_clue

class BrowserCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.CATEGORIES_COUNT = 10
        self.CATEGORIES_AMOUNT = 18420
        self.session = aiohttp.ClientSession(loop=bot.loop)


    async def __before_invoke(self, ctx):
        logging.info("{0.guild} #{0.channel.id}, {0.author}: {0.content}".format(ctx.message))

    def total_categories_pages(self):
        return (self.CATEGORIES_AMOUNT - 1) // self.CATEGORIES_COUNT + 1

    async def categories_embed(self, page):
        categories_json = await jservice_get_json(self.session, 'api/categories',
                                         {'count': self.CATEGORIES_COUNT,
                                          'offset': (page - 1) * self.CATEGORIES_COUNT})
        result = discord.Embed(title='The categories are:\n',
                               colour=discord.Colour.blue())
        for category in categories_json:
            result.add_field(name=category['title'].upper(), inline=False,
                             value='Clues: {clues_count} Id: {id}'.format(**category))
        result.set_footer(text=f'Page {page} of {self.total_categories_pages()}\n')
        return result

    async def categories_page(self, page):
        categories_json = await jservice_get_json(self.session, 'api/categories',
                             {'count': self.CATEGORIES_COUNT,
                              'offset': (page - 1) * self.CATEGORIES_COUNT})
        result = 'The categories are:\n'
        for clue in categories_json:
            # line is too long >:(
            clue['title'] = clue['title'].upper()
            result += '**{title}**    Clues: {clues_count} Id: {id}\n'.format(**clue)
        result += f'Page {page} of {self.total_categories_pages()}\n'
        return result

    @commands.command()
    async def categories(self, ctx):
        """
        Browse all the categories
        """
        page = 1
        browse_reactions = ['⏪', '◀', '▶', '⏩', '🔢']
        embed = await self.categories_embed(page)
        msg = await ctx.send(embed=embed)

        for reaction in browse_reactions:
            await msg.add_reaction(reaction)

        def reactioncheck(reaction, user):
            return (reaction.message.id == msg.id and 
                    user.id == ctx.author.id and 
                    str(reaction.emoji) in browse_reactions)
        def messagecheck(message):
            return (message.channel == ctx.channel and
                    message.author.id == ctx.author.id)

        while True:
            try:
                reaction, user = await self.bot.wait_for('reaction_add',
                                                         timeout=30.0,
                                                         check=reactioncheck)
            except asyncio.TimeoutError:
                break

            oldpage = page
            if reaction.emoji == browse_reactions[0]:
                page = max(page - 10, 1)
            elif reaction.emoji == browse_reactions[1]:
                page = max(page - 1, 1)
            elif reaction.emoji == browse_reactions[2]:
                page = min(page + 1, self.total_categories_pages())
            elif reaction.emoji == browse_reactions[3]:
                page = min(page + 10, self.total_categories_pages())
            elif reaction.emoji == browse_reactions[4]:
                await ctx.send(f'Please say a number between 1 and like... {self.total_categories_pages()}')
                try:
                    message = await self.bot.wait_for('message', timeout=10.0,
                                                      check=messagecheck)
                except asyncio.TimeoutError:
                    break
                else:
                    try:
                        newpage = int(message.content)
                    except ValueError:
                        await ctx.send('A number.')
                    else:
                        if newpage < 1:
                            await ctx.send('A number greater than zero.')
                        elif newpage > self.total_categories_pages():
                            await ctx.send("I don't think there are that many categories")
                        else:
                            page = newpage
            try:
                await msg.remove_reaction(reaction, user)
            except (discord.Forbidden, discord.NotFound) as e:
                pass
            if oldpage != page:
                embed = await self.categories_embed(page)
                await msg.edit(embed=embed)

        for reaction in browse_reactions:
            await msg.remove_reaction(reaction, self.bot.user)


    @commands.command()
    async def category(self, ctx, cid=None, value=None):
        """
        `category <id>` will get you the category with that id, don't use an id to get a random id.
        """
        if cid is None:
            cid = random.randint(1,self.CATEGORIES_AMOUNT)
        try:
            cid = int(cid)
        except ValueError:
            await ctx.send("That's not a valid id number.")
            return


        if value is not None:
            try:
                value = int(value)
            except:
                await ctx.send("The value, if you're using it, needs to be a number.")
                return
            title = ""
            if value != 0:
                category = await jservice_get_json(self.session, 'api/clues',
                                       {'category':cid, 'value':value})
                if category is None:
                    await ctx.send('The search has arrived at an unknown error.')
                    return
                category = [clue for clue in category if is_valid_clue(clue)]
                if not category:
                    await ctx.send('There are no valid clues for this category with this value.')
                    return
                title = category[0]['category']['title']
            else:
                category = await jservice_get_json(self.session, 'api/category',
                                             {'id':cid})
                if category is None:
                    await ctx.send('The search has arrived at an unknown error.')
                title = category['title']
                category = [clue for clue in category['clues']
                            if not clue['value'] and is_valid_clue(clue)]
            if not category:
                await ctx.send('There are no valid clues for this category with this value.')
                return
            result = f'The clues for **{title}** for ${value} are:\n'
            for clue in category:
                result += '`{id}`, '.format(**clue)
            result = result[:-2]
            result += ".\n"
            await ctx.send(result)
            return


        category = await jservice_get_json(self.session, 'api/category', {'id':cid})
        if not category or not category['title']:
            await ctx.send(f"There's no category with id {cid}.")
            return
        
        valuesdict = {}
        validclues = 0

        for i, clue in enumerate(category['clues']):
            if not is_valid_clue(clue):
                continue
            validclues += 1
            value = clue['value'] or 0
            if value not in valuesdict:
                valuesdict[value] = [i]
            else:
                valuesdict[value].append(i)

        category['title'] = category['title'].upper()
        result = (f'There are **{validclues}** valid clues for ' +
                 '**{title}** `{id}`\n'.format(**category))

        for k, v in sorted(valuesdict.items()):
            result += f'For **${k}**: '
            if len(v) < 4:
                for c in v:
                    result += '`{}`, '.format(category['clues'][c]['id'])
                result = result[:-2]
                result += ".\n"
            else:
                result += f'{len(v)} clues.\n'
        if 0 in valuesdict:
            result += "Unknown values are taken with a value of 0\n"
        await ctx.send(result)

def setup(bot):
    bot.add_cog(BrowserCog(bot))
