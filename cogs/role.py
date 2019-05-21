import discord
import asyncio
import sqlite3
from discord.ext import commands


class RoleLowerConverter(commands.RoleConverter):
    async def convert(self, ctx, argument):
        try:
            result = await super().convert(ctx, argument)
        except commands.BadArgument:
            roles = [role for role in ctx.guild.roles if role < ctx.me.top_role]
            roles = {str(role).lower():role for role in roles}
            result = roles.get(argument.lower())

        if result is None:
            raise commands.BadArgument('Role "{}" not found.'.format(argument))
        return result


class RoleCog(commands.Cog):

    def __init__(self, bot):
        self.bot = bot
        self.reaction_roles = {}
        self.database = sqlite3.connect('database.db')
        self.db_cursor = self.database.cursor()
        self.db_cursor.execute("""CREATE TABLE IF NOT EXISTS autoroles
                                  (role integer, guild integer)""")
        self.db_cursor.execute("""CREATE TABLE IF NOT EXISTS reactionroles
                                  (role integer, message integer, channel integer)""")
        self.database.commit()
        reaction_roles = self.db_cursor.execute(f'SELECT role, message, channel FROM reactionroles')
        for (role, message, channel) in reaction_roles:
            if message not in self.reaction_roles:
                self.reaction_roles[message] = {}
            self.reaction_roles[message][channel] = role


    @commands.Cog.listener()
    async def on_member_join(self, member):
        roles = self.db_cursor.execute(f'SELECT role FROM autoroles WHERE guild={member.guild.id}').fetchall()
        roles = [member.guild.get_role(role_id) for (role_id,) in roles]
        for role in roles:
            if role is None:
                continue
            try:
                await member.add_roles(role)
            except discord.Forbidden:
                continue


    @commands.command()
    async def autorole(self, ctx, *, role : RoleLowerConverter):
        if not ctx.author.permissions_in(ctx.channel).manage_roles:
            return await ctx.send("You can't manage roles.")
        if role >= ctx.author.top_role:
            return await ctx.send("You can't add that role.")
        role_exists = self.db_cursor.execute(f'SELECT role FROM autoroles WHERE role={role.id} AND guild={ctx.guild.id}').fetchall()
        if role_exists:
            self.db_cursor.execute(f'DELETE FROM autoroles WHERE role={role.id} AND guild={ctx.guild.id}')
            self.database.commit()
            return await ctx.send(f"{role} is no longer an automatic role.")
        if role >= ctx.me.top_role:
            return await ctx.send("I can't add that role.")
        if role.managed:
            return await ctx.send("I can't add managed roles.")
        self.db_cursor.execute(f'INSERT INTO autoroles VALUES ({role.id}, {ctx.guild.id})')
        self.database.commit()
        await ctx.send(f"{role} is now an automatic role.")


    @commands.command()
    async def autoroles(self, ctx):
        roles = self.db_cursor.execute(f'SELECT role FROM autoroles WHERE guild={ctx.guild.id}').fetchall()
        if not roles:
            return await ctx.send("Your server has no automatic roles.")
        roles = [str(ctx.guild.get_role(role_id)) for (role_id,) in roles]
        await ctx.send(roles)


    @commands.command(aliases=["ar", "iam"])
    async def add_role(self, ctx, *, role : RoleLowerConverter):
        if role in ctx.author.roles:
            return await ctx.send("You already have that role.")
        if role.managed:
            return await ctx.send("I can't add managed roles.")
        try:
            await ctx.author.add_roles(role)
        except discord.Forbidden:
            return await ctx.send("I'm not authorized to do that.")
        except discord.HTTPException:
            return await ctx.send("Sorry, something went wrong.")
        message = await ctx.send("Role added. Would you like to change your nickname to reflect it?")
        await message.add_reaction('✅')
        await message.add_reaction('❎')
        def is_valid_reaction(reaction, user):
            return user == ctx.author and reaction.message.id == message.id and str(reaction.emoji) in ('✅', '❎')
        try:
            reaction, _ = await self.bot.wait_for('reaction_add', timeout=15.0, check=is_valid_reaction)
            change_nickname = str(reaction.emoji) == '✅'
        except asyncio.TimeoutError:
            change_nickname = False
        try:
            await message.clear_reactions()
        except discord.Forbidden:
            await message.remove_reaction('✅', self.bot.user)
            await message.remove_reaction('❎', self.bot.user)
        if change_nickname:
            new_nick = f"{ctx.author.display_name} ({role})"
            if len(new_nick) > 32:
                await ctx.send("Your new nickname would be too long.")
                change_nickname = False
            else:
                try:
                    await ctx.author.edit(nick=new_nick)
                except discord.Forbidden:
                    await ctx.send("I'm not authorized to change your name.")
                    change_nickname = False
                except discord.HTTPException:
                    return await ctx.send("Sorry, something went wrong.")
        if change_nickname:
            await message.edit(content="Role added and nickname changed.")
        else:
            await message.edit(content="Role added.")


    @commands.command(aliases=["rr", "iamnot"])
    async def remove_role(self, ctx, *, role : RoleLowerConverter):
        if role not in ctx.author.roles:
            return await ctx.send("You don't have that role.")
        if role.managed:
            return await ctx.send("I can't remove managed roles.")
        try:
            await ctx.author.remove_roles(role)
        except discord.Forbidden:
            return await ctx.send("I'm not authorized to do that.")
        except discord.HTTPException:
            return await ctx.send("Sorry, something went wrong.")
        if not ctx.author.display_name.endswith(f" ({role})"):
            return await ctx.send("Role removed.")

        message = await ctx.send("Role removed. Would you like to change your nickname to reflect it?")
        await message.add_reaction('✅')
        await message.add_reaction('❎')
        def is_valid_reaction(reaction, user):
            return user == ctx.author and reaction.message.id == message.id and str(reaction.emoji) in ('✅', '❎')
        try:
            reaction, _ = await self.bot.wait_for('reaction_add', timeout=15.0, check=is_valid_reaction)
            change_nickname = str(reaction.emoji) == '✅'
        except asyncio.TimeoutError:
            change_nickname = False
        try:
            await message.clear_reactions()
        except discord.Forbidden:
            await message.remove_reaction('✅', self.bot.user)
            await message.remove_reaction('❎', self.bot.user)
        if change_nickname:
            new_nick = ctx.author.display_name[:-len(f" ({role})")]
            try:
                await ctx.author.edit(nick=new_nick)
            except discord.Forbidden:
                await ctx.send("I'm not authorized to change your name.")
                change_nickname = False
            except discord.HTTPException:
                return await ctx.send("Sorry, something went wrong.")
        if change_nickname:
            await message.edit(content="Role removed and nickname changed.")
        else:
            await message.edit(content="Role removed.")

    @autorole.error
    async def autorole_error(self, ctx, error):
        if isinstance(error, commands.BadArgument):
            return await ctx.send("That's not a role.")
        elif isinstance(error, commands.MissingRequiredArgument):
            return await ctx.send(f"Usage: {ctx.invoked_with} <role>")
        raise error

    @add_role.error
    async def add_role_error(self, ctx, error):
        if isinstance(error, commands.BadArgument):
            return await ctx.send("That's not a role.")
        elif isinstance(error, commands.MissingRequiredArgument):
            return await ctx.send(f"Usage: {ctx.invoked_with} <role>")
        raise error

    @remove_role.error
    async def remove_role_error(self, ctx, error):
        if isinstance(error, commands.BadArgument):
            return await ctx.send("That's not a role.")
        elif isinstance(error, commands.MissingRequiredArgument):
            return await ctx.send(f"Usage: {ctx.invoked_with} <role>")
        raise error

    @commands.command(aliases=["lr", "roles"])
    async def list_roles(self, ctx):
        roles = ctx.guild.roles
        roles = roles[1:roles.index(ctx.me.top_role)]
        roles = [str(role) for role in roles]
        await ctx.send(f"The available roles are: {roles}")


    @commands.command(aliases=["lt", "tags"])
    async def list_tags(self, ctx):
        roles = ctx.guild.roles
        roles = roles[1:roles.index(ctx.me.top_role)]
        roles = [role for role in ctx.author.roles if (role in roles) and (not role.managed)]
        tags = " ("
        for role in roles:
            tags += f"{role}, "
        tags = tags[:-2] + ")"
        if len(ctx.author.display_name + tags) > 32:
            return await ctx.send("Your name is too long to add all your tags.")
        try:
            await ctx.author.edit(nick=ctx.author.display_name + tags)
        except discord.Forbidden:
            return await ctx.send("I'm not authorized to change your name.")
        except discord.HTTPException:
            return await ctx.send("Sorry, something went wrong.")
        return await ctx.send("Tags shown.")


    @commands.command()
    async def reaction_role(self, ctx, role: RoleLowerConverter):
        if not ctx.author.permissions_in(ctx.channel).manage_roles:
            return await ctx.send("You can't manage roles.")
        if role >= ctx.author.top_role:
            return await ctx.send("You can't add that role.")
        if role >= ctx.me.top_role:
            return await ctx.send("I can't add that role.")
        if role.managed:
            return await ctx.send("I can't add managed roles.")
        message = await ctx.send(f"You are {role}")
        await message.add_reaction('✅')
        await message.add_reaction('❎')
        if message.id not in self.reaction_roles:
            self.reaction_roles[message.id] = {ctx.channel.id : role.id}
        else:
            self.reaction_roles[message.id][ctx.channel.id] = role.id
        self.db_cursor.execute(f'INSERT INTO reactionroles VALUES ({role.id}, {message.id}, {ctx.channel.id})')
        self.database.commit()
        await ctx.send("Reaction role added.", delete_after=3)


    @reaction_role.error
    async def reaction_role_error(self, ctx, error):
        if isinstance(error, commands.BadArgument):
            return await ctx.send("That's not a role.")
        elif isinstance(error, commands.MissingRequiredArgument):
            return await ctx.send(f"Usage: {ctx.invoked_with} <role>")
        raise error


    @commands.Cog.listener()
    async def on_raw_reaction_add(self, raw_reaction):
        message_id = raw_reaction.message_id
        if message_id not in self.reaction_roles:
            return

        user_id = raw_reaction.user_id
        if self.bot.user.id == user_id:
            return

        role_id = self.reaction_roles[message_id].get(raw_reaction.channel_id)
        if role_id is None:
            return

        emoji = str(raw_reaction.emoji)
        if emoji == '✅':
            add_role = True
        elif emoji == '❎':
            add_role = False
        else:
            return

        guild = self.bot.get_guild(raw_reaction.guild_id)
        if guild is None:
            return

        member = guild.get_member(user_id)
        if member is None:
            return

        role = guild.get_role(role_id)
        if role is None:
            return

        if add_role:
            await member.add_roles(role)
        else:
            await member.remove_roles(role)


def setup(bot):
    bot.add_cog(RoleCog(bot))
