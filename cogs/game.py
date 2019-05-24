import discord
from discord.ext import commands

import aiohttp
import asyncio
import random
import logging
import re
from difflib import SequenceMatcher
from datetime import datetime
from cogs.utilities import jservice_get_json, is_valid_clue
import dataclasses

tag_re = re.compile(r'<[^>]*>')
between_parentheses_re = re.compile(r'\([^\)]*\)')
parentheses_re = re.compile(r'[()]')
non_letters_re = re.compile(r'\W')
answer_start_re = re.compile(r"^(?:wh(?:at|ere|o)(?: is|'s|s| are)|que es|qué es) +")
answer_starts = ("what is ", "what's ", "whats ", "what are ",
                 "where is ", "where's ", "wheres " "where are ",
                 "who is ", "who's ", "whos ", "who are ",
                 "que es ", "qué es ",
                 "skip clue")

CATEGORY_AMOUNT = 23411
CLUE_AMOUNT = 176778


@dataclasses.dataclass
class Category:
    id_: int
    title: str
    clues_count: int


    def __eq__(self, other):
        if not isinstance(other, Clue):
            return False
        return self.id_ == other.id_


@dataclasses.dataclass
class Clue:
    id_: int
    answer: str
    question: str
    airdate: str
    category_id: int
    game_id: int
    value: int = 0
    invalid_count: int = 0
    answered: bool = False
    category_title: str = "NO CATEGORY"
    possible_answers: list = dataclasses.field(default_factory=list)


    def __post_init__(self):
        if self.value is None:
            self.value = 0


    def question_to_str(self):
        return ('The category is **{0.category_title}** for ${0.value}: ' +
                '`{0.category_id}:{0.id_}` ({1}/{2})\n```\n{0.question}```'
               ).format(self, self.airdate[5:7], self.airdate[2:4])


    def update_possible_answers(self):
        answer = tag_re.sub('', self.answer).strip().lower()
        if answer[0] == "(":
            answers = [between_parentheses_re.sub('', answer),
                                     parentheses_re.sub('', answer)]
        elif answer[-1] == ")":
            start = answer.find("(")
            if answer[start+1:].startswith("or "):
                answers = [answer[:start], answer[start+4:-1]]
            else:
                answers = [between_parentheses_re.sub('', answer),
                                         parentheses_re.sub('', answer)]
        else:
            answers = [answer]
        for i in range(len(answers)):
            try:
                answer = int(answers[i])
            except ValueError:
                continue
            else:
                answers[i] = answer
        self.possible_answers = answers


    def is_correct_answer(self, answer, similarity_ratio=0.65):
        close_answer = False
        for correct_answer in self.possible_answers:
            if isinstance(correct_answer, int):
                try:
                    if int(answer) == correct_answer:
                        return True
                except ValueError:
                    pass
            elif similarity_ratio <= SequenceMatcher(None, correct_answer,
                                                     answer).ratio():
                return True
            elif not close_answer:
                for word in answer.split():
                    if not re.search(rf'\b{re.escape(word)}\b', correct_answer):
                        break
                else:
                    close_answer = True
        if close_answer:
            return None
        else:
            return False


    def __eq__(self, other):
        if not isinstance(other, Clue):
            return False
        return self.id_ == other.id_


@dataclasses.dataclass
class JeopardyGame:
    categories: list = None
    clues : list = None
    final : Clue = None
    daily_doubles : list = None
    active : bool = False
    modifying : bool = False
    players : list = None
    leader_id : int = None
    answered : int = 0
    game_round : int = 0
    time_limit : float = 60.0*60.0
    start_time : datetime = None


    def __post_init__(self):
        if self.categories is None:
            self.categories = [None] * 12
        if self.clues is None:
            self.clues = [None] * 12
            for i in range(len(self.clues)):
                self.clues[i] = []
        if self.daily_doubles is None:
            self.daily_doubles = [None] * 3
        if self.players is None:
            self.players = []


    def add_jeopardy_clues(self, category, clues, info=True):
        index = self.categories.index(None)
        for i, clue in enumerate(clues):
            if index < 6:
                clue.value = 100 * (i+1)
            else:
                clue.value = 200 * (i+1)
            clue.answered = False
        self.categories[index] = category
        self.clues[index] = clues
        if not info:
            return ""
        result = "Added "
        for i, clue in enumerate(self.clues[index]):
            if i != 4:
                result += f"`{clue.id_}`, "
            elif index < 6:
                result += f"and `{clue.id_}` (`{category.id_}`) to **Jeopardy!**\n"
            else:
                result += f"and `{clue.id_}` (`{category.id_}`) to **Double Jeopardy!**\n"
        return result


    def has_clue(self, clue_id):
        if any(any(clue.id_ == clue_id for clue in category) for category in self.clues):
            return True
        return self.final and self.final.id_ == clue_id


    def has_player(self, player_id):
        return any(player['id'] == player_id for player in self.players)


    def has_category(self, category_id):
        return any(category and category._id == category_id for category in self.categories)


    def get_leader(self):
        if self.leader_id is None:
            return None
        for player in self.players:
            if player['id'] == self.leader_id:
                return player
        return None


    def get_category(self, category_id):
        for category in self.categories:
            if category and category.id_ == category_id:
                return category
        return None


    def get_category_index(self, category_id):
        for i, category in enumerate(self.categories):
            if category and category.id_ == category_id:
                return i
        return None


    def add_player(self, player_id, name, score=0):
        if any(player['id'] == player_id for player in self.players):
            return None
        else:
            self.players.append({'id': player_id, 'name': name, 'score': score})
            return self.players[-1]


    def remove_player(self, player_id):
        for i, player in enumerate(self.players):
            if player['id'] == player_id:
                index = i
                break
        else:
            return None
        return self.players.pop(index)


    def get_next_clue(self, clue, skip_category=False):
        if self.game_round >= 3:
            return None
        category_index = self.get_category_index(clue.category_id)
        if category_index is None:
            return None
        if skip_category:
            clue_index = 4
        else:
            try:
                clue_index = self.clues[category_index].index(clue)
            except ValueError:
                return None

        for _ in range(30):
            clue_index += 1
            if clue_index == 5:
                clue_index = 0
                category_index += 1
                category_index %= 6
                if self.game_round == 2:
                    category_index += 6
            if not self.clues[category_index][clue_index].answered:
                return self.clues[category_index][clue_index]
        return None


    def award_points(self, player_id, points):
        for player in self.players:
            if player['id'] == player_id:
                player['score'] += points
                return player['score']
        return None


    def clear(self):
        self.categories = [None]*12
        self.clues = [None]*12
        for i in range(len(self.clues)):
            self.clues[i] = []
        self.final = None
        self.daily_doubles = [None]*3
        self.active = False
        self.modifying = False
        self.players = []
        self.leader_id = None
        self.answered = 0
        self.game_round = 0
        self.time_limit = 60.0*60.0
        self.start_time = None


    def end(self):
        for category in self.clues:
            for clue in category:
                clue.answered = False
        if self.final:
            self.final.answered = False
        self.daily_doubles = [None]*3
        self.active = False
        for player in self.players:
            player['score'] = 0
        self.leader_id = None
        self.answered = 0
        self.game_round = 0
        self.start_time = None


    def start(self):
        self.end()
        self.active = True
        self.leader_id = self.players[0]['id']
        n = random.randint(0,29)
        self.daily_doubles[0] = self.clues[n//6][n%5].id_
        n = random.randint(30,59)
        self.daily_doubles[1] = self.clues[n//6][n%5].id_
        newn = random.randint(30,59)
        while newn == n:
            newn = random.randint(30,59)
        n = newn
        self.daily_doubles[2] = self.clues[n//6][n%5].id_
        self.game_round = 1
        self.start_time = datetime.utcnow()


    def get_board(self):
        result = ""
        for i, category in enumerate(self.clues):
            if self.game_round == 1 and i >= 6:
                break
            elif self.game_round == 2 and i < 6:
                continue
            result += f"**{self.categories[i].title}** `{self.categories[i].id_}`: "
            for j, clue in enumerate(category):
                if j == 4:
                    result += ", and "
                elif j != 0:
                    result += ", "
                result += "{0}${1}{0}".format(('~~' if clue.answered else '**'), clue.value)
                if j == 4:
                    result += ".\n"
        leader = self.get_leader()
        result += f"The current leader is {leader['name']}.\n"
        return result


    def mark_as_answered(self, clue):
        clue.answered = True
        self.answered += 1
        if self.game_round == 3:
            self.game_round += 1
        if self.answered == 30:
            self.game_round += 1
            self.answered = 0
            return self.game_round
        return None


def fix_id(dictionary):
    dictionary['id_'] = dictionary['id']
    del dictionary['id']
    return dictionary


def score_to_text(score):
    if score < 0:
        return f'-${-score}'
    else:
        return f'${score}'


async def award_points(ctx, game, player_id, points):
    score = game.award_points(player_id, points)
    if score is not None:
        await ctx.send(f"That gets you{' down' if points < 0 else ''} to {score_to_text(score)}.")


class GameCog(commands.Cog):

    async def __before_invoke(self, ctx):
        logging.info("{0.guild} #{0.channel.id}, ({1.hour}:{1.minute}:{1.second}) {0.author}: {0.content}".format(ctx.message, datetime.now()))

    def __init__(self, bot):
        self.bot = bot
        self.similarity_ratio = 0.65
        self.channels = {}
        self.session = aiohttp.ClientSession(loop=bot.loop)
        random.seed()


    def get_channel(self, channel):
        if channel not in self.channels:
            logging.info(f"Defining channel {channel}")
            self.channels[channel] = {
                'button mode' : False,
                'infinite mode' : True,
                'clean mode' : False,
                'active' : False,
                'id' : channel,
                'jeopardy' : JeopardyGame()
            }
        return self.channels[channel]

    async def button_check(self, question, button_leader_ids, game=None):
        def reactioncheck(reaction, user):
            return (reaction.message.id == question.id and
                    user.id != self.bot.user.id and
                    user.id not in button_leader_ids and
                    (game is None or (game.has_player(user.id) is not None)) and
                    reaction.emoji == '🔴')

        await question.add_reaction('🔴')

        try:
            _, user = await self.bot.wait_for('reaction_add', timeout=15.0, check=reactioncheck)
        except asyncio.TimeoutError:
            return None, None
        return user.id, user.display_name

    async def get_random_clue(self, get_category=False):
        for _ in range(100):
            clue_id = random.randint(1, CLUE_AMOUNT)
            clue = await jservice_get_json(self.session, 'clues/{}.json'.format(clue_id))
            if not clue:
                continue
            if not is_valid_clue(clue):
                continue
            clue = Clue(**fix_id(clue))
            category = await jservice_get_json(self.session, 'categories/{}.json'.format(clue.category_id))
            category['title'] = category['title'].upper()
            clue.category_title = category['title']
            if get_category:
                category = Category(**fix_id(category))
                return clue, category
            return clue

    async def end_jeopardy_clue(self, ctx, game, question, clue):
        new_round = game.mark_as_answered(clue)
        if new_round == 2:
            await ctx.send("And that takes us to the **Double Jeopardy!** round.")
        elif new_round == 3:
            await ctx.send("And that takes us to the **Final Jeopardy!** round. Use the `jeopardy final` command to start it.")

        if game.game_round == 1:
            if (game.time_limit/2 - (datetime.utcnow() - game.start_time).total_seconds() < 0):
                await ctx.send("We're out of time for **Jeopardy!** Moving on to **Double Jeopardy!**")
                game.game_round = 2
                game.answered = 0
        elif game.game_round == 2:
            if (game.time_limit - (datetime.utcnow() - game.start_time).total_seconds() < 0):
                await ctx.send("We're out of time for **Double Jeopardy!** Moving on to **Final Jeopardy!**")
                game.game_round = 3
                game.answered = 0
        if game.game_round == 3:
            return
        def reaction_check(reaction, user):
            return (reaction.message.id == question.id and
                    user.id == game.leader_id and
                    reaction.emoji in ['⬇', '⏭', '📋'])
        for bot_reaction in ['⬇', '⏭', '📋']:
            await question.add_reaction(bot_reaction)
        try:
            reaction, _ = await self.bot.wait_for('reaction_add', timeout=20.0, check=reaction_check)
        except asyncio.TimeoutError:
            for bot_reaction in ['⬇', '⏭', '📋']:
                await question.remove_reaction(bot_reaction, self.bot.user)
            return
        for bot_reaction in ['⬇', '⏭', '📋']:
            await question.remove_reaction(bot_reaction, self.bot.user)
        if reaction.emoji == '📋':
            await ctx.send(game.get_board())
            return

        if reaction.emoji == '⬇':
            next_clue = game.get_next_clue(clue)
        elif reaction.emoji == '⏭':
            next_clue = game.get_next_clue(clue, skip_category=True)
        else:
            next_clue = None
        if next_clue is None:
            await ctx.send("Something went wrong...")
            return
        clue = next_clue
        if clue.id_ in game.daily_doubles:
            await self.daily_double(ctx, clue)
        else:
            await self.play(ctx, clue, jeopardy_mode=True)


    async def play(self, ctx, clue, jeopardy_mode=False):
        channel = self.get_channel(ctx.channel.id)
        if channel['active']:
            await ctx.send("There's already an active question in this channel.")
            return
        channel['active'] = True
        question = clue.question_to_str()

        question = await ctx.send(question)
        clue.update_possible_answers()
        # correctanswer = tag_re.sub('', clue.answer).lower()
        # correctanswer = between_parentheses_re.sub('', correctanswer)
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
                button_leader_id, button_leader_name = await self.button_check(question, incorrect_answer_ids, channel['jeopardy'].players)
                if button_leader_id is not None:
                    question = await ctx.send(f"{button_leader_name}, what's your answer?")
                else:
                    question = await ctx.send(f"Time's up! The correct response was **{clue.answer}**.")
                    break
                remainingtime = 15.0
            elif jeopardy_mode and be_specific:
                remainingtime = 15.0
            elif channel['button mode']:
                button_leader_id, button_leader_name = await self.button_check(question, [button_leader_id])
                if button_leader_id is not None:
                    question = await ctx.send(f"{button_leader_name}, what's your answer?")
                else:
                    question = await ctx.send(f"Time's up! The correct response was **{clue.answer}**.")
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
                if channel['button mode'] or (jeopardy_mode and len(incorrect_answer_ids) < len(channel['jeopardy'].players)):
                    question = await ctx.send("Time's up, somebody else?")
                    if jeopardy_mode:
                        await award_points(ctx, channel['jeopardy'], button_leader_id, -clue.value)
                    continue
                else:
                    question = await ctx.send("Time's up! The correct response was "
                                              f"**{clue.answer}**.")
                    if jeopardy_mode:
                        await award_points(ctx, channel['jeopardy'], button_leader_id, -clue.value)
                    break



            answertext = answer.content.lower()
            if answertext.startswith("skip clue"):
                question = await ctx.send("Ok.")
                break

            answertext = answer_start_re.sub('', answertext, 1)
            result = clue.is_correct_answer(answertext)
            if result:
                question = await ctx.send("That's correct, {}. The correct response was **{}**.".format(
                                 answer.author.display_name, clue.answer))
                if jeopardy_mode:
                    await award_points(ctx, channel['jeopardy'], button_leader_id, clue.value)
                    channel['jeopardy'].leader_id = button_leader_id
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
                        if len(incorrect_answer_ids) == len(channel['jeopardy'].players):
                            question = await ctx.send(f"That's incorrect, {answer.author.display_name}. The correct response was **{clue.answer}**.")
                            await award_points(ctx, channel['jeopardy'], button_leader_id, -clue.value)
                            break
                    question = await ctx.send(f"That's incorrect, {answer.author.display_name}.",
                                              delete_after=(2.0 if channel['clean mode'] else None))
                    if jeopardy_mode:
                        await award_points(ctx, channel['jeopardy'], button_leader_id, -clue.value)
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
            _, user = await self.bot.wait_for('reaction_add', timeout=20.0, check=reactioncheck)
        except asyncio.TimeoutError:
            await question.remove_reaction("🔄", self.bot.user)
        else:
            # await question.remove_reaction("🔄", self.bot.user)
            ctx.message.author = user
            ctx.message.content = "repeat clue"
            await self.clue.invoke(ctx)

    async def daily_double(self, ctx, clue):
        channel = self.get_channel(ctx.channel.id)
        if channel['active']:
            await ctx.send("There's already an active question in this channel.")
            return
        channel['active'] = True
        game = channel['jeopardy']
        leader = channel.get_leader()
        max_bet = max(leader['score'], (500 if game.game_round == 1 else 1000))
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
        question = clue.question_to_str()

        await ctx.send(question)
        clue.update_possible_answers()
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
                await award_points(ctx, channel['jeopardy'], leader['id'], -bet)
                question = await ctx.send("Time's up! The correct response was "
                                    f"**{clue.answer}**.")
            else:
                answertext = answer_start_re.sub('', answer.content.lower(), 1)
                result = clue.is_correct_answer(answertext)
                if result:
                    question = await ctx.send("That's correct, {}. The correct response was **{}**.".format(
                                     answer.author.display_name, clue.answer))
                    await award_points(ctx, channel['jeopardy'], leader['id'], bet)
                elif result is None and not be_specific:
                    await ctx.send(f"Be more specific, {answer.author.display_name}.")
                    be_specific = True
                    continue
                else:
                    question = await ctx.send(f"That's incorrect, {answer.author.display_name}. The correct response was **{clue.answer}**.")
                    await award_points(ctx, channel['jeopardy'], leader['id'], -bet)
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
            clue = Clue(**fix_id(clue))
            category = await jservice_get_json(self.session, 'categories/{}.json'.format(clue.category_id))
            clue.category_title = category['title'].upper()

        await self.play(ctx, clue)

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
        elif cid == 'any': # and value!='any'
            await ctx.send("You currently can't search for a value with no category specified, sorry.")
        elif value == 'any' or value == 0: # and cid!='any'
            category = await jservice_get_json(self.session, 'api/category',
                                         {'id':cid})
            if not category:
                await ctx.send("That doesn't seem to be a valid category.")
                return
            clues = [x for x in clues if is_valid_clue(x) and (value or not x['value'])]
            clues = [Clue(**fix_id(clue)) for clue in clues]
            if not clues:
                if value == 0:
                    await ctx.send("This category doesn't seem to have any valid clues with unknown value.")
                else:
                    await ctx.send("This category doesn't seem to have any valid clues.")
                return
            clue = random.choice(clues)
            clue.category_title = category['title'].upper()
        else:   # cid != 'any' and value != 'any' and value != 0
            category = await jservice_get_json(self.session, 'api/category',
                                               {'id':cid})
            # category = await jservice_get_json(self.session, 'api/clues', {'category':cid, 'value':value})
            if category is None:
                return await ctx.send("The search arrived to an unknown error.")
            clues = [x for x in clues if is_valid_clue(x) and x['value'] == value]
            clues = [Clue(**fix_id(clue)) for clue in category['clues']]
            if not clues:
                return await ctx.send("This category doesn't seem to have any valid clues with that category and value.")
            clue = random.choice(clues)
            clue.category_title = category['title'].upper()

        await self.play(ctx, clue)

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
        if self.get_channel(ctx.channel.id)['jeopardy'].active:
            if warning:
                await ctx.send("There's already an active game. Wait until the current game is done!")
            return True
        return False

    async def is_modifying_jeopardy(self, ctx, warning=True):
        if self.get_channel(ctx.channel.id)['jeopardy'].modifying:
            if warning:
                await ctx.send("The game is currently being modified. Wait until the current step is done!")
            return True
        return False

    async def get_random_category(self, game):
        for _ in range(100):
            category_id = random.randint(1, CATEGORY_AMOUNT)
            if game.has_category(category_id):
                continue
            category = await jservice_get_json(self.session, 'api/category', {'id':category_id})
            if not category:
                continue
            valid_clues = [x for x in category['clues'] if is_valid_clue(x) and (not game.final or game.final.id_ != x['id'])]
            for i in range(len(valid_clues)-1, 0, -1):
                question = valid_clues[i]['question'].lower()
                for j in range(0, i):
                    if question == valid_clues[j]['question'].lower():
                        valid_clues.pop(i)
                        break
            if len(valid_clues) >= 5:
                break
        else:
            return None, None
        clues = [None]*5
        category['title'] = category['title'].upper()
        for i in range(5):
            clue = valid_clues.pop(random.randint(0,len(valid_clues)-1))
            clues[i] = Clue(**fix_id(clue))
            clues[i].category_title = category['title']
        del category['clues']
        category = Category(**fix_id(category))
        return category, clues

    @jeopardy.command()
    async def autoadd(self, ctx):
        if await self.is_active_jeopardy(ctx) or await self.is_modifying_jeopardy(ctx):
            return
        game = self.get_channel(ctx.channel.id)['jeopardy']
        game.modifying = True
        if None not in game.categories:
            await ctx.send("There are already 12 categories! You need to remove one before you add one.")
        else:
            category, clues = await self.get_random_category(game)
            result = game.add_jeopardy_clues(category, clues)
            await ctx.send(result)
        game.modifying = False

    @jeopardy.command()
    async def autofill(self, ctx):
        if await self.is_active_jeopardy(ctx) or await self.is_modifying_jeopardy(ctx):
            return
        game = self.get_channel(ctx.channel.id)['jeopardy']
        game.modifying = True
        while None in game.categories:
            category, clues = await self.get_random_category(game)
            result = game.add_jeopardy_clues(category, clues)
            await ctx.send(result)
        await ctx.send("There are no more categories to add.")
        game.modifying = False

    @jeopardy.command()
    async def add(self, ctx, clue1:int, clue2:int, clue3:int, clue4:int, clue5:int):
        if await self.is_active_jeopardy(ctx) or await self.is_modifying_jeopardy(ctx):
            return
        game = self.get_channel(ctx.channel.id)['jeopardy']
        game.modifying = True
        category_id = None
        if None not in game.categories:
            await ctx.send("There are already 12 categories! You need to remove one before you add one.")
            game.modifying = False
            return
        clue_ids = [clue1, clue2, clue3, clue4, clue5]
        for clue_id in clue_ids:
            if clue_ids.count(clue_id) > 1:
                await ctx.send("You can't add the same clue more than once!")
            elif game.has_clue(clue_id):
                await ctx.send(f"{clue_id} has already been added.")
            else:
                continue
            game.modifying = False
            return
        clues = [None]*5
        for i, clue_id in enumerate(clue_ids):
            clue = await jservice_get_json(self.session, f'clues/{clue_id}.json')
            if not clue or not is_valid_clue(clue):
                await ctx.send(f"Clue number {i+1} (`{clue_id}`) is not a valid clue.")
                break
            if i == 0:
                category_id = clue.category_id
                if game.has_category(category_id):
                    await ctx.send('That category has already been added.')
                    break
                category = await jservice_get_json(self.session, f'categories/{category_id}.json')
                category['title'] = category['title'].upper()
                category = Category(**fix_id(category))
            elif category_id != clue.category_id:
                await ctx.send(f"All clues should share the same category, clue number {i+1} (`{clue_id}`) doesn't have the same category as the previous clues.")
                break
            elif game.final and clue['id'] == game.final.id_:
                await ctx.send(f'`{clue_id}` is already the **Final Jeopardy!** clue.')
            clues[i] = Clue(**fix_id(clue))
            clues[i].category_title = category.title
        else:
            result = game.add_jeopardy_clues(category, clues)
            await ctx.send(result)
        game.modifying = False

    @jeopardy.command()
    async def add_final(self, ctx, clue_id=None):
        if await self.is_active_jeopardy(ctx) or await self.is_modifying_jeopardy(ctx):
            return
        game = self.get_channel(ctx.channel.id)['jeopardy']
        game.modifying = True
        if clue_id is None:
            while True:
                clue = await self.get_random_clue()
                if not game.has_clue(clue.id_):
                    break
            clue.answered = False
            game.final = clue
            await ctx.send(f"Added `{game.final.id_}` to **Final Jeopardy!**\n")
            game.modifying = False
            return
        try:
            clue_id = int(clue_id)
        except ValueError:
            await ctx.send("The id, if you're using it, needs to be a number.")
            game.modifying = False
            return
        if game.has_clue(clue_id):
            await ctx.send("That clue has already been added to this game!")
        else:
            clue = await jservice_get_json(self.session, f'clues/{clue_id}.json')
            if not clue:
                await ctx.send("That clue doesn't exist!")
            elif not is_valid_clue(clue):
                await ctx.send("That's not a valid clue.")
            else:
                clue = Clue(**fix_id(clue))
                category = await jservice_get_json(self.session, f"categories/{clue.category_id}.json")
                if not category:
                    await ctx.send("Somehow that clue doesn't belong to a valid category.")
                else:
                    category['title'] = category['title'].upper()
                    clue.category_title = category['title']
                    clue.answered = False
                    game.final = clue
                    await ctx.send(f"Added `{game.final.id_}` to **Final Jeopardy!**\n")
        game.modifying = False

    @jeopardy.command(name='categories')
    async def categories_info(self, ctx):
        game = self.get_channel(ctx.channel.id)['jeopardy']
        categories = game.categories
        result = "The **Jeopardy!** categories are: "
        for i in range(6):
            if i != 5:
                result += f"`{categories[i] and categories[i].id_}`, "
            else:
                result += f"and `{categories[i] and categories[i].id_}`.\n"
        result += "The **Double Jeopardy!** categories are: "
        for i in range(6,12):
            if i != 11:
                result += f"`{categories[i] and categories[i].id_}`, "
            else:
                result += f"and `{categories[i] and categories[i].id_}`.\n"
        result += f"The clue for **Final Jeopardy!** is: `{game.final and game.final.id_}`.\n"
        await ctx.send(result)

    @jeopardy.command()
    async def remove(self, ctx, category_id:int):
        if await self.is_active_jeopardy(ctx) or await self.is_modifying_jeopardy(ctx):
            return
        game = self.get_channel(ctx.channel.id)['jeopardy']
        game.modifying = True
        category_index = game.get_category_index(category_id)
        if category_index is None:
            await ctx.send(f"Category `{category_id}` is not one of the categories.")
        else:
            game.categories[category_index] = None
            game.clues[category_index] = None
            await ctx.send(f"Category `{category_id}` has been removed.")
        game.modifying = False

    @jeopardy.command()
    async def join(self, ctx):
        if await self.is_active_jeopardy(ctx):
            return
        game = self.get_channel(ctx.channel.id)['jeopardy']
        player = game.add_player(player_id=ctx.author.id,
                                 name=ctx.author.display_name, score=0)
        if player:
            await ctx.send(f"{player['name']} is now a player.")
        else:
            await ctx.send(f"{ctx.author.display_name} is already a player!")


    @jeopardy.command()
    async def leave(self, ctx):
        if await self.is_active_jeopardy(ctx):
            return
        game = self.get_channel(ctx.channel.id)['jeopardy']
        removed_player = game.remove_player(ctx.author.id)
        if removed_player:
            await ctx.send(f"{removed_player['name']} is no longer a player.")
        else:
            await ctx.send(f"{ctx.author.display_name} is not a player!")


    @jeopardy.command()
    async def players(self, ctx):
        players = self.get_channel(ctx.channel.id)['jeopardy'].players
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
        game = self.get_channel(ctx.channel.id)['jeopardy']
        if len(game.players) < 2:
            await ctx.send('You need at least two players to play Jeopardy!')
        elif None in game.categories or not game.final:
            await ctx.send("You don't have all clues ready!")
        else:
            game.start()
            await ctx.send(f"The game has started.")
            await ctx.send(game.get_board())


    @jeopardy.command()
    async def stop(self, ctx):
        if not await self.is_active_jeopardy(ctx, False):
            await ctx.send("There's no game currently active.")
            return
        self.get_channel(ctx.channel.id)['jeopardy'].end()
        await ctx.send("The game has been cancelled.")


    @jeopardy.command()
    async def clear(self, ctx):
        if await self.is_active_jeopardy(ctx) or await self.is_modifying_jeopardy(ctx):
            return
        self.get_channel(ctx.channel.id)['jeopardy'].clear()
        await ctx.send("The game has been cleared.")


    @commands.is_owner()
    @jeopardy.command(hidden=True)
    async def set_round(self, ctx, new_round:int):
        if not await self.is_active_jeopardy(ctx, False):
            return await ctx.send("There's no game currently active.")
        self.get_channel(ctx.channel.id)['jeopardy'].game_round = new_round


    @commands.is_owner()
    @jeopardy.command(hidden=True)
    async def get_round(self, ctx):
        if not await self.is_active_jeopardy(ctx, False):
            return await ctx.send("There's no game currently active.")
        await ctx.send(self.get_channel(ctx.channel.id)['jeopardy'].game_round)


    @commands.is_owner()
    @jeopardy.command(hidden=True)
    async def set_answered(self, ctx, new_answered:int):
        if not await self.is_active_jeopardy(ctx, False):
            return await ctx.send("There's no game currently active.")
        self.get_channel(ctx.channel.id)['jeopardy'].answered = new_answered


    @commands.is_owner()
    @jeopardy.command(hidden=True)
    async def get_answered(self, ctx):
        if not await self.is_active_jeopardy(ctx, False):
            return await ctx.send("There's no game currently active.")
        await ctx.send(self.get_channel(ctx.channel.id)['jeopardy'].answered)


    @jeopardy.command()
    async def display(self, ctx):
        if not await self.is_active_jeopardy(ctx, False):
            return await ctx.send("There's no game currently active.")
        await ctx.send(self.get_channel(ctx.channel.id)['jeopardy'].get_board())


    @jeopardy.command()
    async def final(self, ctx):
        if not await self.is_active_jeopardy(ctx, False):
            await ctx.send("There's no game currently active.")
            return
        channel = self.get_channel(ctx.channel.id)
        if channel['active']:
            await ctx.send("There's already an active question in this channel.")
            return
        game = channel['jeopardy']
        if game.game_round != 3:
            await ctx.send("You're not in the Final Jeopardy! round.")
            return
        channel['active'] = True
        players = {}
        for player in game.players:
            if player['score'] <= 0:
                continue
            info = await self.bot.get_user(player['id'])
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

        clue = game.final

        clue.update_possible_answers()
        def is_valid_answer(message):
            return (message.author.id in players and
                    type(message.channel) == discord.DMChannel and
                    message.content.lower().startswith(answer_starts) and
                    not message.content.lower().startswith("skip clue"))

        question = clue.question_to_str()

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
                if clue.is_correct_answer(answer):
                    await ctx.send("That is correct.")
                    await ctx.send(f"You also bet ${players[player]['bet']}.")
                    await award_points(ctx, game, player, players[player]['bet'])
                else:
                    await ctx.send("That is incorrect.")
                    await ctx.send(f"You also bet ${players[player]['bet']}.")
                    await award_points(ctx, game, player, -players[player]['bet'])
            else:
                result += f"did not make a guess."
                await ctx.send(result)
                await ctx.send(f"You also bet ${players[player]['bet']}.")
                await award_points(ctx, game, player, -players[player]['bet'])

        final_scores = sorted([(x['score'], x['name']) for x in game.players])

        await ctx.send("From last to first place, the final scores are:")
        for score, name in final_scores:
            await ctx.send(f"{name} with {score_to_text(score)}.")
        channel['active'] = False

        game.mark_as_answered(clue)


    @jeopardy.command()
    async def timelimit(self, ctx, minutes:int):
        if minutes <= 1:
            return
        self.get_channel(ctx.channel.id)['jeopardy'].time_limit = minutes * 60.0
        await ctx.send(f"Changed the time limit of the game to {minutes} minutes.")


    @jeopardy.command()
    async def award(self, ctx, player:discord.Member, money:int):
        if ctx.author.id == player.id:
            await ctx.send("You can't award money to yourself.")
        elif self.get_channel(ctx.channel.id)['jeopardy'].has_player(player.id) is None:
            await ctx.send("That's not one of the players.")
        else:
            await award_points(ctx, self.get_channel(ctx.channel.id)['jeopardy'], player.id, money)


    @jeopardy.command()
    async def kick(self, ctx, player:discord.Member):
        if await self.is_active_jeopardy(ctx):
            return
        game = self.get_channel(ctx.channel.id)['jeopardy']
        removed_player = game.remove_player(player.id)
        if removed_player:
            await ctx.send(f"{removed_player['name']} is no longer a player.")
        else:
            await ctx.send(f"{player.display_name} is not a player!")


    @commands.command()
    async def take(self, ctx, *, clue):
        channel = self.get_channel(ctx.channel.id)
        if channel['active']:
            return
        game = channel['jeopardy']
        if not (0 <= game.answered < 60):
            return
        if not game.active or not game.has_player(ctx.author.id):
            return
        if game.leader_id != ctx.author.id:
            await ctx.send("Only the current leader can choose a clue.")
            return
        clue = clue.upper().split()
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
            for i, c in enumerate(game.categories):
                if c.title == category:
                    category_index = i
                    break
            else:
                category_index = None
        else:
            category_index = game.get_category_index(category_id)

        if category_index is None:
            await ctx.send("That's not one of the categories.")
            return
        if ((category_index < 6 and game.game_round != 1) or
            (category_index >= 6 and game.game_round != 2)):
            await ctx.send("That category is not in this round.")
            return
        if game.game_round == 1:
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
        clue = game.clues[category_index][clue_index]
        if clue.answered:
            await ctx.send("That clue has already been answered.")
            return
        if clue.id_ in game.daily_doubles:
            await self.daily_double(ctx, clue)
        else:
            await self.play(ctx, clue, jeopardy_mode=True)


def setup(bot):
    bot.add_cog(GameCog(bot))
