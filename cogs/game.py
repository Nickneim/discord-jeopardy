import discord
from discord.ext import commands

import aiohttp
import asyncio
import random
import re
from difflib import SequenceMatcher
from datetime import datetime
from .utilities import *

tag_re = re.compile(r'<[^>]*>')
between_parentheses_re = re.compile(r'\([^\)]*\)')
parentheses_re = re.compile(r'[()]')
non_letters_re = re.compile(r'\W')
answer_start_re = re.compile(r"^wh(?:at|ere|o)(?: is|'s|s| are) +")
answer_starts = ("what is ", "what's ", "whats ", "what are ",
                 "where is ", "where's ", "wheres " "where are ",
                 "who is ", "who's ", "whos ", "who are ",
                 "skip clue")


def get_possible_answers(answer):
    answer = tag_re.sub('', answer).strip().lower()
    if answer[0] == "(":
        return [between_parentheses_re.sub('', answer), parentheses_re.sub('', answer)]
    elif answer[-1] == ")":
        start = answer.find("(")
        if answer[start+1:].startswith("or "):
            return [answer[:start], answer[start+4:-1]]
        else:
            return [between_parentheses_re.sub('', answer), parentheses_re.sub('', answer)]
    else:
        return [answer]

def is_correct_answer(correct_answers, answer, number_clue, similarity_ratio=0.65):
    close_answer = False
    for i, correct in enumerate(correct_answers):
        if number_clue[i]:
            try:
                if int(answer) == correct:
                    return True
            except ValueError:
                pass
        elif similarity_ratio <= SequenceMatcher(None, correct, answer).ratio():
            return True
        elif not close_answer:
            for word in answer.split():
                if not re.search(rf'\b{re.escape(word)}\b', correct):
                    break
            else:
                close_answer = True
    if close_answer:
        return None
    else:
        return False

def add_jeopardy_clues(jdict, index, category, clues, info=True):
    for i,clue in enumerate(clues):
        clue['value'] = 100*(i+1)*(2 if index > 5 else 1)
        clue['answered'] = False
    jdict['categories'][index] = category
    jdict['clues'][index] = clues
    if not info:
        return ""
    result = "Added "
    for (i, clue) in enumerate(jdict['clues'][index]):
        if i != 4:
            result += f"`{clue['id']}`, "
        elif index < 6:
            result += f"and `{clue['id']}` (`{category['id']}`) to **Jeopardy!**\n"
        else:
            result += f"and `{clue['id']}` (`{category['id']}`) to **Double Jeopardy!**\n"
    return result

def clue_in_jeopardy(jdict, clue_id):
    for category in jdict['clues']:
        if category is None:
            continue
        for clue in category:
            if clue['id'] == clue_id:
                return True
    return jdict['final'] and jdict['final']['id'] == clue_id

def same_id(a, b):
    return a and b and a['id'] == b['id']

def id_to_index(items, item_id):
    for i, item in enumerate(items):
        if item and item['id'] == item_id:
            return i
    return None

def score_to_text(score):
    if score < 0:
        return f'-${-score}'
    else:
        return f'${score}'

def get_clue_index(jdict, clue):
    category_index = id_to_index(jdict['categories'], clue['category_id'])
    clue_index = id_to_index(jdict['clues'][category_index], clue['id'])
    return category_index, clue_index

def get_next_clue(jdict, category_index, clue_index):
    if jdict['round'] >= 3:
        return None
    i = 0
    while i < 30:
        i += 1
        clue_index += 1
        if clue_index == 5:
            clue_index = 0
            category_index += 1
            category_index %= 6
            if jdict['round'] == 2:
                category_index += 6
        if not jdict['clues'][category_index][clue_index]['answered']:
            return jdict['clues'][category_index][clue_index]
    return None

def get_board(jdict):
    result = ""
    for i, category in enumerate(jdict['clues']):
        if jdict['round'] == 1 and i >= 6:
            break
        elif jdict['round'] == 2 and i < 6:
            continue
        result += f"**{jdict['categories'][i]['title'].upper()}** `{jdict['categories'][i]['id']}`: "
        for j, clue in enumerate(category):
            if j == 4:
                result += ", and "
            elif j != 0:
                result += ", "
            result += "{0}${1}{0}".format(('~~' if clue['answered'] else '**'), clue['value'])
            if j == 4:
                result += ".\n"
    leader = id_to_index(jdict['players'], jdict['leader'])
    leader = jdict['players'][leader]['name']
    result += f"The current leader is {leader}.\n" 
    return result

async def award_points(ctx, players, player_id, points):
    index = id_to_index(players, player_id)
    players[index]['score'] += points
    await ctx.send(f"That gets you{' down' if points < 0 else ''} to {score_to_text(players[index]['score'])}.")

async def mark_as_answered(ctx, jdict, clue):
    clue['answered'] = True
    jdict['answered'] += 1
    if jdict['round'] == 3:
        jdict['round'] += 1
    if jdict['answered'] == 30:
        if jdict['round'] == 1:
            await ctx.send("And that takes us to the **Double Jeopardy!** round.")
        elif jdict['round'] == 2:
            await ctx.send("And that takes us to the **Final Jeopardy!** round. Use the `jeopardy final` command to start it.")
        jdict['round'] += 1
        jdict['answered'] = 0

class GameCog:

    async def __before_invoke(self, ctx):
        print("{0.guild} #{0.channel.id}, ({1.hour}:{1.minute}:{1.second}) {0.author}: {0.content}".format(ctx.message, datetime.now()))

    def __init__(self, bot):
        self.bot = bot
        self.similarity_ratio = 0.65
        self.channels = {}
        self.session = aiohttp.ClientSession(loop=bot.loop)
        random.seed()


    def get_channel(self, channel):
        if channel not in self.channels:
            print("Defining channel", channel)
            self.channels[channel] = {
                'button mode' : False,
                'infinite mode' : True,
                'clean mode' : False,
                'active' : False,
                'id' : channel,
                'jeopardy' : {'categories' : [None]*12,
                              'clues' : [None]*12,
                              'final' : None,
                              'daily doubles' : [None, None, None],
                              'active' : False,
                              'modifying' : False,
                              'players' : [],
                              'leader' : None,
                              'answered' : 0,
                              'round' : 0,
                              'time limit' : 60.0*60.0,
                              'start time' : None}
            }
        return self.channels[channel]

    async def button_check(self, question, button_leader_ids, players=None):
        def reactioncheck(reaction, user):
            return (reaction.message.id == question.id and
                    user.id != self.bot.user.id and
                    user.id not in button_leader_ids and
                    (not players or (id_to_index(players, user.id) is not None)) and
                    reaction.emoji == '🔴')

        await question.add_reaction('🔴')

        try:
            reaction, user = await self.bot.wait_for('reaction_add', timeout=15.0, check=reactioncheck)
        except asyncio.TimeoutError:
            return None, None
        return user.id, user.display_name

    async def get_random_clue(self):
        while True:
            clue = await jservice_get_json(self.session, 'api/random')
            clue = clue[0]
            if is_valid_clue(clue):
                return clue

    async def end_jeopardy_clue(self, ctx, jdict, question, clue):
        await mark_as_answered(ctx, jdict, clue)
        if jdict['round'] == 1:
            if (jdict['time limit']/2 - (datetime.utcnow() - jdict['start time']).total_seconds() < 0):
                await ctx.send("We're out of time for **Jeopardy!** Moving on to **Double Jeopardy!**")
                jdict['round'] = 2
                jdict['answered'] = 0
        elif jdict['round'] == 2:
            if (jdict['time limit'] - (datetime.utcnow() - jdict['start time']).total_seconds() < 0):
                await ctx.send("We're out of time for **Double Jeopardy!** Moving on to **Final Jeopardy!**")
                jdict['round'] = 3
                jdict['answered'] = 0
        if jdict['round'] == 3:
            return
        def reaction_check(reaction, user):
            return (reaction.message.id == question.id and
                    user.id == jdict['leader'] and
                    reaction.emoji in ['⬇', '⏭', '📋'])
        for bot_reaction in ['⬇', '⏭', '📋']:
            await question.add_reaction(bot_reaction)
        try:
            reaction, user = await self.bot.wait_for('reaction_add', timeout=20.0, check=reaction_check)
        except asyncio.TimeoutError:
            for bot_reaction in ['⬇', '⏭', '📋']:
                await question.remove_reaction(bot_reaction, self.bot.user)
            return
        for bot_reaction in ['⬇', '⏭', '📋']:
            await question.remove_reaction(bot_reaction, self.bot.user)
        if reaction.emoji == '📋':
            await ctx.send(get_board(jdict))
            return
        category_index, clue_index = get_clue_index(jdict, clue)
        next_clue = None
        if reaction.emoji == '⬇':
            next_clue = get_next_clue(jdict, category_index, clue_index)
        elif reaction.emoji == '⏭':
            next_clue = get_next_clue(jdict, category_index, 4)
        if next_clue is None:
            await ctx.send("Something went wrong...")
            return
        clue = next_clue
        category_index, clue_index = get_clue_index(jdict, clue)
        title = jdict['categories'][category_index]['title']
        if clue['id'] in jdict['daily doubles']:
            await self.daily_double(ctx, clue, title)
        else:
            await self.play(ctx, clue, title, jeopardy_mode=True)


    async def play(self, ctx, clue, title, jeopardy_mode=False):
        channel = self.get_channel(ctx.channel.id)
        if channel['active']:
            await ctx.send("There's already an active question in this channel.")
            return
        channel['active'] = True
        clue['airdate'] = clue['airdate'][5:7] + "/" + clue['airdate'][2:4]
        question = (f'The category is **{title.upper()}** for $' +
                    '{value}: `{category_id}:{id}` ({airdate})\n```\n{question}```'.format(**clue))

        question = await ctx.send(question)
        correct_answers = get_possible_answers(clue['answer'])
        # correctanswer = tag_re.sub('', clue['answer']).lower()
        # correctanswer = between_parentheses_re.sub('', correctanswer)
        number_clue = [True for x in correct_answers]
        for i, answer in enumerate(correct_answers):
            try:
                n = int(answer)
            except ValueError:
                number_clue[i] = False
            else:
                correct_answers[i] = n

        # correctanswer = non_letters_re.sub('', correctanswer)

        button_leader_id = None
        incorrect_answer_ids = []
        be_specific = False

        def is_valid_answer(message):
            return (message.channel == ctx.channel and
                    message.content.lower().startswith(answer_starts) and
                    (not (channel['button mode'] or jeopardy_mode) or button_leader_id == message.author.id) and
                    (not jeopardy_mode or not message.content.lower().startswith("skip clue")))

        question_start = datetime.utcnow()
        while True:
            if jeopardy_mode and not be_specific:
                button_leader_id, button_leader_name = await self.button_check(question, incorrect_answer_ids, channel['jeopardy']['players'])
                if button_leader_id is not None:
                    question = await ctx.send(f"{button_leader_name}, what's your answer?")
                else:
                    question = await ctx.send(f"Time's up! The correct response was **{clue['answer']}**.")
                    break
                remainingtime = 15.0
            elif jeopardy_mode and be_specific:
                remainingtime = 15.0
            elif channel['button mode']:
                button_leader_id, button_leader_name = await self.button_check(question, [button_leader_id])
                if button_leader_id is not None:
                    question = await ctx.send(f"{button_leader_name}, what's your answer?")
                else:
                    question = await ctx.send(f"Time's up! The correct response was **{clue['answer']}**.")
                    break
                remainingtime = 15.0
            else:
                remainingtime = max(0.5, 52.5 - (datetime.utcnow() - question_start).total_seconds())

            try:
                answer = await self.bot.wait_for('message', timeout=remainingtime,
                                                 check=is_valid_answer)
            except asyncio.TimeoutError:
                be_specific = False
                if jeopardy_mode:
                    incorrect_answer_ids.append(button_leader_id)
                if channel['button mode'] or (jeopardy_mode and len(incorrect_answer_ids) < len(channel['jeopardy']['players'])):
                    question = await ctx.send("Time's up, somebody else?")
                    if jeopardy_mode:
                        await award_points(ctx, channel['jeopardy']['players'], button_leader_id, -clue['value'])
                    continue
                else:
                    question = await ctx.send("Time's up! The correct response was "
                                              f"**{clue['answer']}**.")
                    if jeopardy_mode:
                        await award_points(ctx, channel['jeopardy']['players'], button_leader_id, -clue['value'])
                    break



            answertext = answer.content.lower()
            if answertext.startswith("skip clue"):
                question = await ctx.send("Ok.")
                break

            answertext = answer_start_re.sub('', answertext, 1)

            result = is_correct_answer(correct_answers, answertext, number_clue)
            if result:
                question = await ctx.send("That's correct, {}. The correct response was **{}**.".format(
                                 answer.author.display_name, clue['answer']))
                if jeopardy_mode:
                    await award_points(ctx, channel['jeopardy']['players'], button_leader_id, clue['value'])
                    channel['jeopardy']['leader'] = button_leader_id
                break
            else:
                if result is None and not be_specific:
                    question = await ctx.send("Be more specific, {}.".format(
                                          answer.author.display_name))
                    if jeopardy_mode:
                        be_specific = True
                else:
                    if jeopardy_mode:
                        be_specific = False
                        incorrect_answer_ids.append(button_leader_id)
                        if len(incorrect_answer_ids) == len(channel['jeopardy']['players']):
                            question = await ctx.send(f"That's incorrect, {answer.author.display_name}. The correct response was **{clue['answer']}**.")
                            await award_points(ctx, channel['jeopardy']['players'], button_leader_id, -clue['value'])
                            break
                    question = await ctx.send(f"That's incorrect, {answer.author.display_name}.",
                                              delete_after=(2.0 if channel['clean mode'] else None))
                    if jeopardy_mode:
                        await award_points(ctx, channel['jeopardy']['players'], button_leader_id, -clue['value'])
                if channel['clean mode']:
                    try:
                        await answer.delete()
                    except discord.Forbidden:
                        pass
        channel['active'] = False
        if jeopardy_mode:
            await self.end_jeopardy_clue(ctx, channel['jeopardy'], question, clue)
            return
        if not channel['infinite mode']:
            return
        def reactioncheck(reaction, user):
            return (reaction.message.id == question.id and
                    user.id != self.bot.user.id and
                    reaction.emoji == '🔄')
        await question.add_reaction("🔄")
        try:
            reaction, user = await self.bot.wait_for('reaction_add', timeout=20.0, check=reactioncheck)
        except asyncio.TimeoutError:
            await question.remove_reaction("🔄", self.bot.user)
        else:
            await question.remove_reaction("🔄", self.bot.user)
            ctx.message.author = user
            ctx.message.content = "repeat clue"
            await self.clue.invoke(ctx)

    async def daily_double(self, ctx, clue, title):
        channel = self.get_channel(ctx.channel.id)
        if channel['active']:
            await ctx.send("There's already an active question in this channel.")
            return
        channel['active'] = True
        leader = id_to_index(channel['jeopardy']['players'], channel['jeopardy']['leader'])
        leader = channel['jeopardy']['players'][leader]
        max_bet = max(leader['score'], (500 if channel['jeopardy']['round'] == 1 else 1000))
        def is_valid_bet(message):
            return (message.channel == ctx.channel and
                    leader['id'] == message.author.id and
                    message.content.lower().startswith("bet ")
                    )

        await ctx.send(f"You've found one of the Daily Doubles! Make a `bet` between $5 and ${max_bet}")

        while True:
            answer = await self.bot.wait_for('message', check=is_valid_bet)
            bet = answer.content[4:]
            try:
                bet = int(bet)
            except ValueError:
                await ctx.send("That's not a valid bet.")
            else:
                if 5 <= bet <= max_bet:
                    break
                await ctx.send(f"Bet between $5 and ${max_bet}")
        await ctx.send(f"You've bet ${bet}.")
        clue['airdate'] = clue['airdate'][5:7] + "/" + clue['airdate'][2:4]
        question = (f'The category is **{title.upper()}** for $' +
                    '{value}: `{category_id}:{id}` ({airdate})\n```\n{question}```'.format(**clue))

        await ctx.send(question)
        correct_answers = get_possible_answers(clue['answer'])
        number_clue = [True for x in correct_answers]
        for i, answer in enumerate(correct_answers):
            try:
                n = int(answer)
            except ValueError:
                number_clue[i] = False
            else:
                correct_answers[i] = n
        def is_valid_answer(message):
            return (message.channel == ctx.channel and
                    message.content.lower().startswith(answer_starts) and
                    leader['id'] == message.author.id and
                    not message.content.lower().startswith("skip clue"))

        be_specific = False

        while True:
            try:
                answer = await self.bot.wait_for('message',
                                                 timeout=(15.0 if be_specific else 30.0),
                                                 check=is_valid_answer)
            except asyncio.TimeoutError:
                await award_points(ctx, channel['jeopardy']['players'], leader['id'], -bet)
                question = await ctx.send("Time's up! The correct response was "
                                    f"**{clue['answer']}**.")
            else:
                answertext = answer_start_re.sub('', answer.content.lower(), 1)
                result = is_correct_answer(correct_answers, answertext, number_clue)
                if result:
                    question = await ctx.send("That's correct, {}. The correct response was **{}**.".format(
                                     answer.author.display_name, clue['answer']))
                    await award_points(ctx, channel['jeopardy']['players'], leader['id'], bet)
                elif result is None and not be_specific:
                    await ctx.send(f"Be more specific, {answer.author.display_name}.")
                    be_specific = True
                    continue
                else:
                    question = await ctx.send(f"That's incorrect, {answer.author.display_name}. The correct response was **{clue['answer']}**.")
                    await award_points(ctx, channel['jeopardy']['players'], leader['id'], -bet)
                break
        channel['active'] = False
        await self.end_jeopardy_clue(ctx, channel['jeopardy'], question, clue)


    @commands.command()
    async def clue(self, ctx, clue_id=None):
        """Gets a random clue or a clue with a specific id"""
        if self.get_channel(ctx.channel.id)['active']:
            await ctx.send("There's already an active question in this channel.")
            return
        if clue_id is None:
            clue = await self.get_random_clue()
            title = clue['category']['title']
        else:
            try:
                clue_id = int(clue_id)
            except ValueError:
                await ctx.send("The id, if you're using it, needs to be a number.")
                return
            clue = await jservice_get_json(self.session, 'clues/{}.json'.format(clue_id))
            if not clue:
                await ctx.send("There's no clue with that id.")
                return
            if not is_valid_clue(clue, True, True):
                await ctx.send("That doesn't seem to be a valid clue.")
                return
            title = await jservice_get_json(self.session, 'categories/{}.json'.format(clue['category_id']))
            title = title['title']

        await self.play(ctx, clue, title)

    @commands.command()
    async def find(self, ctx, cid='any', value='any'):
        """
        `find <category> <value>` gets a random clue from a certain category and with a certain value.
        Use 'any' or a number for each of the arguments.
        """
        
        if self.get_channel(ctx.channel.id)['active']:
            await ctx.send("There's already an active question in this channel.")
            return
        cid = cid.strip().lower()
        value = value.strip().lower()

        if cid != 'any':
            try:
                cid = int(cid)
            except ValueError:
                await ctx.send("The category needs to be a number or `any`.")
                return
        if value != 'any':
            try:
                value = int(value)
            except ValueError:
                await ctx.send("The value needs to be a number or `any`.")
                return
        if cid == 'any' and value == 'any':
            clue = await self.get_random_clue()
            title = clue['category']['title']
        elif cid == 'any': # and value!='any'
            await ctx.send("You currently can't search for a value with no category specified, sorry.")
        elif value == 'any' or value == 0: # and cid!='any'
            category = await jservice_get_json(self.session, 'api/category',
                                         {'id':cid})
            if not category:
                await ctx.send("That doesn't seem to be a valid category.")
                return
            validclues = [x for x in category['clues'] if is_valid_clue(x) and (value or not x['value'])]
            if not validclues:
                if value == 0:
                    await ctx.send("This category doesn't seem to have any valid clues with unknown value.")
                else:
                    await ctx.send("This category doesn't seem to have any valid clues.")
                return
            title = category['title']
            clue = random.choice(validclues)
        else:   # cid != 'any' and value != 'any'
            category = await jservice_get_json(self.session, 'api/clues', {'category':cid, 'value':value})
            if category is None:
                await ctx.send("The search arrived to an unknown error.")
                return
            validclues = [x for x in category if is_valid_clue(x)]
            if not validclues:
                await ctx.send("No valid clues with that category and value were found.")
                return
            clue = random.choice(validclues)
            title = clue['category']['title']

        await self.play(ctx, clue, title)

    async def togglemode(self, ctx, mode):
        channel = self.get_channel(ctx.channel.id)
        channel[mode] = not channel[mode]
        if channel[mode]:
            await ctx.send(f"{mode.capitalize()} is now active")
        else:
            await ctx.send(f"{mode.capitalize()} is now inactive")

    @commands.command()
    async def buttonmode(self, ctx):
        """Toggle buttonmode"""
        await self.togglemode(ctx, 'button mode')

    @commands.command()
    async def infinitemode(self, ctx):
        """Toggle infinite mode"""
        await self.togglemode(ctx, 'infinite mode')

    @commands.command()
    async def cleanmode(self, ctx):
        """Toggle clean mode"""
        await self.togglemode(ctx, 'clean mode')

    @commands.group()
    async def jeopardy(self, ctx):
        pass

    async def is_active_jeopardy(self, ctx, warning=True):
        if self.get_channel(ctx.channel.id)['jeopardy']['active']:
            if warning:
                await ctx.send("There's already an active game. Wait until the current game is done!")
            return True
        return False
    
    async def is_modifying_jeopardy(self, ctx, warning=True):
        if self.get_channel(ctx.channel.id)['jeopardy']['modifying']:
            if warning:
                await ctx.send("The game is currently being modified. Wait until the current step is done!")
            return True
        return False

    async def get_random_category(self, jdict):
        while True:
            category_id = random.randint(1,18420)
            if id_to_index(jdict['categories'], category_id):
                continue
            category = await jservice_get_json(self.session, 'api/category', {'id':category_id})
            if not category:
                continue
            valid_clues = [x for x in category['clues'] if is_valid_clue(x) and not same_id(jdict['final'], x)]
            for i in range(len(valid_clues)-1, 0, -1):
                question = valid_clues[i]['question'].lower()
                for j in range(0, i):
                    if question == valid_clues[j]['question'].lower():
                        valid_clues.pop(i)
                        break
            if len(valid_clues) >= 5:
                break
        clues = [None]*5
        for i in range(5):
            clues[i] = valid_clues.pop(random.randint(0,len(valid_clues)-1))
        del category['clues']
        return category, clues

    @jeopardy.command()
    async def autoadd(self, ctx):
        if await self.is_active_jeopardy(ctx) or await self.is_modifying_jeopardy(ctx):
            return
        jdict = self.get_channel(ctx.channel.id)['jeopardy']
        jdict['modifying'] = True
        category_id = None
        try:
            category_index = jdict['categories'].index(None)
        except ValueError:
            await ctx.send("There are already 12 categories! You need to remove one before you add one.")
        else:
            category, clues = await self.get_random_category(jdict)
            result = add_jeopardy_clues(jdict, category_index, category, clues)
            await ctx.send(result)
        jdict['modifying'] = False

    @jeopardy.command()
    async def autofill(self, ctx):
        if await self.is_active_jeopardy(ctx) or await self.is_modifying_jeopardy(ctx):
            return
        jdict = self.get_channel(ctx.channel.id)['jeopardy']
        jdict['modifying'] = True
        while None in jdict['categories']:
            category_index = jdict['categories'].index(None)
            category, clues = await self.get_random_category(jdict)
            result = add_jeopardy_clues(jdict, category_index, category, clues)
            await ctx.send(result)
        await ctx.send("There are no more categories to add.")
        jdict['modifying'] = False

    @jeopardy.command()
    async def add(self, ctx, clue1:int, clue2:int, clue3:int, clue4:int, clue5:int):
        if await self.is_active_jeopardy(ctx) or await self.is_modifying_jeopardy(ctx):
            return
        jdict = self.get_channel(ctx.channel.id)['jeopardy']
        jdict['modifying'] = True
        category_id = None
        try:
            category_index = jdict['categories'].index(None)
        except ValueError:
            await ctx.send("There are already 12 categories! You need to remove one before you add one.")
            jdict['modifying'] = False
            return
        clue_ids = [clue1, clue2, clue3, clue4, clue5]
        for clue_id in clue_ids:
            if clue_ids.count(clue_id) > 1:
                await ctx.send("You can't add the same clue more than once!")
            elif clue_in_jeopardy(jdict, clue_id):
                await ctx.send(f"{clue_id} has already been added.")
            else:
                continue
            jdict['modifying'] = False
            return
        clues = [None]*5
        for i, clue_id in enumerate(clue_ids):
            clue = await jservice_get_json(self.session, f'clues/{clue_id}.json')
            if not clue or not is_valid_clue(clue):
                await ctx.send(f"Clue number {i+1} (`{clue_id}`) is not a valid clue.")
                break
            if i == 0:
                category_id = clue['category_id']
                if id_to_index(jdict['categories'], category_id) is not None:
                    await ctx.send('That category has already been added.')
                    break
                category = await jservice_get_json(self.session, f'categories/{category_id}.json')
                if not (category and category['id'] and category['title']):
                    await ctx.send("Somehow that isn't a valid category.")
                    break
            elif category_id != clue['category_id']:
                await ctx.send(f"All clues should share the same category, clue number {i+1} (`{clue_id}`) doesn't have the same category as the previous clues.")
                break
            elif same_id(jdict['final'], clue):
                await ctx.send(f'`{clue_id}` is already the **Final Jeopardy!** clue.')
            clues[i] = clue
        else:
            result = add_jeopardy_clues(jdict, category_index, category, clues)
            await ctx.send(result)
        jdict['modifying'] = False

    @jeopardy.command()
    async def add_final(self, ctx, clue_id=None):
        if await self.is_active_jeopardy(ctx) or await self.is_modifying_jeopardy(ctx):
            return
        jdict = self.get_channel(ctx.channel.id)['jeopardy']
        jdict['modifying'] = True
        if clue_id is None:
            while True:
                clue = await self.get_random_clue()
                if not clue_in_jeopardy(jdict, clue['id']):
                    break
            clue['answered'] = False
            jdict['final'] = clue
            await ctx.send(f"Added `{jdict['final']['id']}` to **Final Jeopardy!**\n")
            jdict['modifying'] = False
            return
        try:
            clue_id = int(clue_id)
        except ValueError:
            await ctx.send("The id, if you're using it, needs to be a number.")
            jdict['modifying'] = False
            return
        if clue_in_jeopardy(jdict, clue_id):
            await ctx.send("That clue has already been added to this game!")
        else:   
            clue = await jservice_get_json(self.session, f'clues/{clue_id}.json')
            if not clue:
                await ctx.send("That clue doesn't exist!")
            elif not is_valid_clue(clue):
                await ctx.send("That's not a valid clue.")
            else:
                category = await jservice_get_json(self.session, f"categories/{clue['category_id']}.json")
                if not category or not category['title']:
                    await ctx.send("Somehow that clue doesn't belong to a valid category.")
                else:           
                    clue['category'] = category
                    clue['answered'] = False
                    jdict['final'] = clue
                    await ctx.send(f"Added `{jdict['final']['id']}` to **Final Jeopardy!**\n")
        jdict['modifying'] = False

    @jeopardy.command(name='categories')
    async def categories_info(self, ctx):
        jdict = self.get_channel(ctx.channel.id)['jeopardy']
        categories = jdict['categories']
        result = "The **Jeopardy!** categories are: "
        for i in range(6):
            if i != 5:
                result += f"`{categories[i] and categories[i]['id']}`, "
            else:
                result += f"and `{categories[i] and categories[i]['id']}`.\n"
        result += "The **Double Jeopardy!** categories are: "
        for i in range(6,12):
            if i != 11:
                result += f"`{categories[i] and categories[i]['id']}`, "
            else:
                result += f"and `{categories[i] and categories[i]['id']}`.\n"
        result += f"The clue for **Final Jeopardy!** is: `{jdict['final'] and jdict['final']['id']}`.\n"
        await ctx.send(result)

    @jeopardy.command()
    async def remove(self, ctx, category_id:int):
        if await self.is_active_jeopardy(ctx) or await self.is_modifying_jeopardy(ctx):
            return
        jdict = self.get_channel(ctx.channel.id)['jeopardy']
        jdict['modifying'] = True
        category_index = id_to_index(jdict['categories'], category_id)
        if category_index is None:
            await ctx.send(f"Category `{category_id}` is not one of the categories.")
        else:
            jdict['categories'][category_index] = None
            jdict['clues'][category_index] = None
            await ctx.send(f"Category `{category_id}` has been removed.")
        jdict['modifying'] = False

    @jeopardy.command()
    async def join(self, ctx):
        if await self.is_active_jeopardy(ctx):
            return
        players = self.get_channel(ctx.channel.id)['jeopardy']['players']
        player_index = id_to_index(players, ctx.author.id)
        if player_index is not None:
            await ctx.send(f"{ctx.author.display_name} is already a player!")
            return
        players.append({'id': ctx.author.id, 'name': ctx.author.display_name, 'score': 0})
        await ctx.send(f"{players[-1]['name']} is now a player.")

    @jeopardy.command()
    async def leave(self, ctx):
        if await self.is_active_jeopardy(ctx):
            return
        players = self.get_channel(ctx.channel.id)['jeopardy']['players']
        player_index = id_to_index(players, ctx.author.id)
        if player_index is None:
            await ctx.send(f"{ctx.author.display_name} is not a player!")
        else:
            player = players.pop(player_index)
            await ctx.send(f"{player['name']} is no longer a player.")

    @jeopardy.command()
    async def players(self, ctx):
        players = self.get_channel(ctx.channel.id)['jeopardy']['players']
        if not players:
            result = 'There are currently no players.'
        elif len(players) == 1:
            result = f"The only player is {players[0]['name']} with {score_to_text(players[0]['score'])}."
        else:
            result = 'The players are, from left to right: '
            for i, player in enumerate(players):
                if i != len(players)-1:
                    result += f"{player['name']} with {score_to_text(player['score'])}, "
                else:
                    result += f"and {player['name']} with {score_to_text(player['score'])}."
        await ctx.send(result)

    @jeopardy.command()
    async def start(self, ctx):
        if await self.is_active_jeopardy(ctx) or await self.is_modifying_jeopardy(ctx):
            return
        jdict = self.get_channel(ctx.channel.id)['jeopardy']
        jdict['active'] = True
        if len(jdict['players']) < 2:
            await ctx.send('You need at least two players to play Jeopardy!')
        elif None in jdict['categories'] or not jdict['final']:
            await ctx.send("You don't have all clues ready!")
        else:
            leader = jdict['players'][0]
            jdict['leader'] = leader['id']
            n = random.randint(0,29)
            print(n//6, n%5)
            jdict['daily doubles'][0] = jdict['clues'][n//6][n%5]['id']
            n = random.randint(30,59)
            print(n//6, n%5)
            jdict['daily doubles'][1] = jdict['clues'][n//6][n%5]['id']
            newn = random.randint(30,59)
            while newn == n:
                newn = random.randint(30,59)
            n = newn
            print(n//6, n%5)
            jdict['daily doubles'][2] = jdict['clues'][n//6][n%5]['id']
            jdict['start time'] = datetime.utcnow()
            jdict['round'] = 1
            jdict['answered'] = 0
            await ctx.send(f"The game has started.") # , {leader['name']} chooses first.")
            await ctx.send(get_board(jdict))
            return

        jdict['active'] = False

    @jeopardy.command()
    async def stop(self, ctx):
        if not await self.is_active_jeopardy(ctx, False):
            await ctx.send("There's no game currently active.")
            return
        jdict = self.get_channel(ctx.channel.id)['jeopardy']
        for category in jdict['clues']:
            for clue in category:
                clue['answered'] = False
        jdict['final']['answered'] = False
        for player in jdict['players']:
            player['score'] = 0
        jdict['active'] = False
        await ctx.send("The game has been cancelled.")

    @jeopardy.command()
    async def clear(self, ctx):
        if await self.is_active_jeopardy(ctx) or await self.is_modifying_jeopardy(ctx):
            return
        self.get_channel(ctx.channel.id)['jeopardy'] = {
            'categories' : [None]*12,
            'clues' : [None]*12,
            'final' : None,
            'daily doubles' : [None, None, None],
            'active' : False,
            'modifying' : False,
            'players' : [],
            'leader' : None,
            'answered' : 0,
            'round' : 0,
            'time limit' : 60.0*60.0,
            'start time' : None
            }
        await ctx.send("The game has been cleared.")


    @jeopardy.command()
    async def display(self, ctx):
        if not await self.is_active_jeopardy(ctx, False):
            await ctx.send("There's no game currently active.")
            return
        await ctx.send(get_board(self.get_channel(ctx.channel.id)['jeopardy']))

    @jeopardy.command()
    async def final(self, ctx):
        if not await self.is_active_jeopardy(ctx, False):
            await ctx.send("There's no game currently active.")
            return
        channel = self.get_channel(ctx.channel.id)
        if channel['active']:
            await ctx.send("There's already an active question in this channel.")
            return
        jdict = channel['jeopardy']
        if jdict['round'] != 3:
            await ctx.send("You're not in the Final Jeopardy! round.")
            return
        channel['active'] = True
        players = {}
        for player in jdict['players']:
            if player['score'] <= 0:
                continue
            info = await self.bot.get_user_info(player['id'])
            players[player['id']] = {'bet':0, 'info':info, 'score':player['score'], 'answer':None}
        if not players:
            await ctx.send("Nobody has money for **Final Jeopardy!** The game is over.")
            channel['active'] = False
            return

        await ctx.send("You'll have 30 seconds to `bet` privately. Starting now.")
        for player in players.values():
            await player['info'].send(f"You have 30 seconds to `bet` between 0 and {player['score']}")

        def is_valid_bet(message):
            return (message.author.id in players and
                    type(message.channel) == discord.DMChannel and
                    message.content.lower().startswith("bet ")
                    )
        bet_start = datetime.utcnow()

        remainingtime = 30.0
        while remainingtime > 0:
            try:
                answer = await self.bot.wait_for('message', check=is_valid_bet, timeout=remainingtime)
            except asyncio.TimeoutError:
                break
            bet = answer.content[4:]
            try:
                bet = int(bet)
            except ValueError:
                await answer.author.send("That's not a valid bet.")
            else:
                if 0 <= bet <= players[answer.author.id]['score']:
                    players[answer.author.id]['bet'] = bet
                    await answer.author.send(f"You've bet ${bet}.")
                else:
                    await answer.author.send(f"Bet between $0 and ${players[answer.author.id]['score']}")
            remainingtime = max(0.5, 30.0 - (datetime.utcnow() - bet_start).total_seconds())

        bet_message = "Time for bet is over, it's time to answer."
        await ctx.send(bet_message)
        for player in players.values():
            await player['info'].send(bet_message)

        bet_message = "You'll have 40 seconds to answer privately. The clue is as follows:"
        await ctx.send(bet_message)
        for player in players.values():
            await player['info'].send(bet_message)

        clue = jdict['final']
        title = clue['category']['title']

        correct_answers = get_possible_answers(clue['answer'])
        number_clue = [True for x in correct_answers]
        for i, answer in enumerate(correct_answers):
            try:
                n = int(answer)
            except ValueError:
                number_clue[i] = False
            else:
                correct_answers[i] = n
        def is_valid_answer(message):
            return (message.author.id in players and
                    type(message.channel) == discord.DMChannel and
                    message.content.lower().startswith(answer_starts) and
                    not message.content.lower().startswith("skip clue"))

        clue['airdate'] = clue['airdate'][5:7] + "/" + clue['airdate'][2:4]
        question = (f'The category is **{title.upper()}**: ' +
                    '`{category_id}:{id}` ({airdate})\n```\n{question}```'.format(**clue))

        await ctx.send(question)
        for player in players.values():
            await player['info'].send(question)


        remainingtime = 40.0
        bet_start = datetime.utcnow()
        while remainingtime > 0:
            try:
                answer = await self.bot.wait_for('message',
                                                 timeout=remainingtime,
                                                 check=is_valid_answer)
            except asyncio.TimeoutError:
                break
            players[answer.author.id]['answer'] = answer.content
            await answer.author.send("Got it.")
            remainingtime = max(0.5, 40 - (datetime.utcnow() - bet_start).total_seconds())
        bet_message = "Time's up, let's check how everyone answered."
        await ctx.send(bet_message)
        for player in players.values():
            await player['info'].send(bet_message)
        def sort_key(player_id):
            return players[player_id]['score']
        sorted_players = sorted(list(players), key=sort_key)
        for player in sorted_players:
            result = f"{players[player]['info'].display_name}, you "
            answer = players[player]['answer']
            if answer:
                result += f"guessed {answer}... "
                await ctx.send(result)
                if is_correct_answer(correct_answers, answer, number_clue):
                    await ctx.send("That is correct.")
                    await ctx.send(f"You also bet ${players[player]['bet']}.")
                    await award_points(ctx, jdict['players'], player, players[player]['bet'])
                else:
                    await ctx.send("That is incorrect.")
                    await ctx.send(f"You also bet ${players[player]['bet']}.")
                    await award_points(ctx, jdict['players'], player, -players[player]['bet'])
            else:
                result += f"did not make a guess."
                await ctx.send(result)
                await ctx.send(f"You also bet ${players[player]['bet']}.")
                await award_points(ctx, jdict['players'], player, -players[player]['bet'])

        final_scores = sorted([(x['score'], x['name']) for x in jdict['players']])

        await ctx.send("From last to first place, the final scores are:")
        for score, name in final_scores:
            await ctx.send(f"{name} with {score_to_text(score)}.")
        channel['active'] = False
        await mark_as_answered(ctx, jdict, clue)

    @jeopardy.command()
    async def timelimit(self, ctx, minutes:int):
        if minutes <= 1:
            return
        self.get_channel(ctx.channel.id)['jeopardy']['time limit'] = minutes * 60.0
        await ctx.send(f"Changed the time limit of the game to {minutes} minutes.")


    @jeopardy.command()
    async def award(self, ctx, player:discord.Member, money:int):
        if ctx.author.id == player.id:
            await ctx.send("You can't award money to yourself.")
        elif id_to_index(self.get_channel(ctx.channel.id)['jeopardy']['players'], player.id) is None:
            await ctx.send("That's not one of the players.")
        else:
            await award_points(ctx, self.get_channel(ctx.channel.id)['jeopardy']['players'], player.id, money)

    @jeopardy.command()
    async def kick(self, ctx, player:discord.Member):
        if await self.is_active_jeopardy(ctx):
            return
        players = self.get_channel(ctx.channel.id)['jeopardy']['players']
        player_index = id_to_index(players, player.id)
        if player_index is None:
            await ctx.send(f"{player.display_name} is not a player!")
        else:
            player = players.pop(player_index)
            await ctx.send(f"{player['name']} is no longer a player.")



    @commands.command()
    async def take(self, ctx, *, clue):
        channel = self.get_channel(ctx.channel.id)
        if channel['active']:
            return
        jdict = channel['jeopardy']
        if not (0 <= jdict['answered'] < 60):
            return
        if not jdict['active'] or (id_to_index(jdict['players'], ctx.author.id) is None):
            return
        if jdict['leader'] != ctx.author.id:
            await ctx.send("Only the current leader can choose a clue.")
            return
        clue = clue.lower().split()
        if len(clue) == 1:
            await ctx.send("The format is `take <category> <value>`")
            return
        elif len(clue) == 2:
            category, value = clue
        if len(clue) > 2:
            category = ' '.join(clue[:-1]).strip()
            value = clue[-1]
        try:
            value = int(value)
        except ValueError:
            await ctx.send("The value needs to be a number.")
            return
        try:
            category_id = int(category)
        except ValueError:
            for i, c in enumerate(jdict['categories']):
                if c['title'] == category:
                    category_index = i
                    break
            else:
                category_index = None
        else:
            category_index = id_to_index(jdict['categories'], category_id)
        if category_index is None:
            await ctx.send("That's not one of the categories.")
            return
        if ((category_index < 6 and jdict['answered'] >= 30) or
            (category_index >= 6 and jdict['answered'] < 30)):
            await ctx.send("That category is not in this round.")
            return
        if jdict['answered'] < 30:
            try:
                clue_index = [100,200,300,400,500].index(value)
            except ValueError:
                await ctx.send("There's no clue with that value.")
                return
        else:
            try:
                clue_index = [200,400,600,800,1000].index(value)
            except ValueError:
                await ctx.send("There's no clue with that value.")
                return
        clue = jdict['clues'][category_index][clue_index]
        if clue['answered']:
            await ctx.send("That clue has already been answered.")
            return
        title = jdict['categories'][category_index]['title']
        if clue['id'] in jdict['daily doubles']:
            await self.daily_double(ctx, clue, title)
        else:
            await self.play(ctx, clue, title, jeopardy_mode=True)
        

    @commands.command(hidden=True)
    @commands.is_owner()
    async def globalsend(self, ctx, *, downmessage:str):
        for channel in self.channels:
            c = self.bot.get_channel(channel)
            if isinstance(c, discord.TextChannel):
                try:
                    await c.send(downmessage)
                except (discord.HTTPException, discord.Forbidden):
                    pass

def setup(bot):
    bot.add_cog(GameCog(bot))
