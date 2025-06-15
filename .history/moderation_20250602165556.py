import discord
from discord.ext import commands
import asyncio
from datetime import datetime, timedelta
import db
import re
from datetime import  timezone
import traceback
# ========================
# 🔹 WARNING SYSTEM
# ========================

async def warn_user(ctx, member, reason):
    """Issue a warning to a user"""
    server_id = ctx.guild.id
    moderator_id = ctx.author.id

    # Store moderator info
    await db.update_user(moderator_id, ctx.author.name, getattr(ctx.author, 'discriminator', None))

    # Get warning settings
    const_vars = await db.get_constitutional_variables(server_id)
    max_warnings = int(const_vars.get("warning_threshold", {"value": "3"})["value"])
    warning_action = const_vars.get("warning_action", {"value": "kick"})["value"]

    # Add warning to database
    warning_count = await db.add_warning(server_id, member.id, moderator_id, reason)

    # Send DM to warned user
    try:
        dm_embed = discord.Embed(
            title="⚠️ Warning Received",
            description=f"You have received a warning in **{ctx.guild.name}**",
            color=discord.Color.orange()
        )
        dm_embed.add_field(name="Reason", value=reason, inline=False)
        dm_embed.add_field(name="Warning Level", value=f"{warning_count}/{max_warnings}", inline=False)

        if warning_count >= max_warnings:
            dm_embed.add_field(
                name="⚠️ Maximum Warnings Reached",
                value=f"You have reached the maximum number of warnings. Action will be taken: **{warning_action}**",
                inline=False
            )

        await member.send(embed=dm_embed)
    except discord.Forbidden:
        await ctx.send(f"⚠️ Could not send DM to {member.mention}. They may have DMs disabled.")

    # Create embed for channel response
    embed = discord.Embed(
        title="⚠️ Warning Issued",
        description=f"{member.mention} has been warned.",
        color=discord.Color.orange()
    )
    embed.add_field(name="Reason", value=reason, inline=False)
    embed.add_field(name="Warning Count", value=f"{warning_count}/{max_warnings}", inline=False)
    embed.add_field(name="Moderator", value=ctx.author.mention, inline=False)

    # Check if action needed based on warning count
    action_taken = None
    if warning_count >= max_warnings:
        if warning_action.lower() == "kick":
            try:
                await member.kick(reason=f"Reached maximum warnings ({max_warnings})")
                action_taken = "kicked"
            except discord.Forbidden:
                await ctx.send("❌ I don't have permission to kick this member.")
        elif warning_action.lower() == "ban":
            try:
                await member.ban(reason=f"Reached maximum warnings ({max_warnings})")
                action_taken = "banned"
            except discord.Forbidden:
                await ctx.send("❌ I don't have permission to ban this member.")
        elif warning_action.lower() == "mute":
            # Get mute role
            mute_role_name = const_vars.get("mute_role", {"value": "Muted"})["value"]
            mute_role = discord.utils.get(ctx.guild.roles, name=mute_role_name)

            if mute_role:
                try:
                    await member.add_roles(mute_role, reason=f"Reached maximum warnings ({max_warnings})")
                    action_taken = "muted"
                except discord.Forbidden:
                    await ctx.send("❌ I don't have permission to add roles to this member.")
            else:
                # Create mute role if it doesn't exist
                try:
                    mute_role = await create_mute_role(ctx.guild)
                    await member.add_roles(mute_role, reason=f"Reached maximum warnings ({max_warnings})")
                    action_taken = "muted"
                except discord.Forbidden:
                    await ctx.send("❌ I don't have permission to create roles or add roles to this member.")

        if action_taken:
            embed.add_field(name="Action Taken", value=f"User has been {action_taken} for reaching maximum warnings.", inline=False)

    # Send response to channel
    await ctx.send(embed=embed)

    # Log to audit channel
    audit_channel = discord.utils.get(ctx.guild.text_channels, name="audit-log")
    if audit_channel:
        audit_embed = discord.Embed(
            title="⚠️ Warning Issued",
            description=f"{member.mention} has been warned by {ctx.author.mention}.",
            color=discord.Color.orange()
        )
        audit_embed.add_field(name="Reason", value=reason, inline=False)
        audit_embed.add_field(name="Warning Count", value=f"{warning_count}/{max_warnings}", inline=False)

        if action_taken:
            audit_embed.add_field(name="Action Taken", value=f"User has been {action_taken} for reaching maximum warnings.", inline=False)

        await audit_channel.send(embed=audit_embed)

    return warning_count, action_taken

async def get_warnings(ctx, member):
    """Get all warnings for a user"""
    server_id = ctx.guild.id

    # Get warnings from database
    warnings = await db.get_user_warnings(server_id, member.id)

    if not warnings:
        await ctx.send(f"✅ {member.mention} has no warnings.")
        return

    # Create embed for warnings
    embed = discord.Embed(
        title=f"Warnings for {member.name}",
        description=f"{member.mention} has {len(warnings)} warning(s).",
        color=discord.Color.orange()
    )

    # Add each warning to the embed
    for i, warning in enumerate(warnings):
        moderator = ctx.guild.get_member(warning['moderator_id'])
        moderator_name = moderator.mention if moderator else f"<@{warning['moderator_id']}>"

        embed.add_field(
            name=f"Warning #{warning['level']} - {warning['timestamp'][:10]}",
            value=f"**Reason:** {warning['reason']}\n**Moderator:** {moderator_name}",
            inline=False
        )

    await ctx.send(embed=embed)

async def clear_user_warnings(ctx, member):
    """Clear all warnings for a user"""
    server_id = ctx.guild.id

    # Get current warnings count
    warnings = await db.get_user_warnings(server_id, member.id)

    if not warnings:
        await ctx.send(f"✅ {member.mention} already has no warnings.")
        return

    # Clear warnings
    await db.clear_warnings(server_id, member.id)

    # Send response
    await ctx.send(f"✅ Cleared {len(warnings)} warning(s) for {member.mention}.")

    # Log to audit channel
    audit_channel = discord.utils.get(ctx.guild.text_channels, name="audit-log")
    if audit_channel:
        await audit_channel.send(f"🧹 **Warnings Cleared**: {ctx.author.mention} cleared {len(warnings)} warning(s) for {member.mention}.")

# ========================
# 🔹 TEMPORARY MODERATION
# ========================

async def temp_ban(ctx, member, duration, reason):
    """Temporarily ban a user"""
    server_id = ctx.guild.id
    moderator_id = ctx.author.id

    # Parse duration
    duration_seconds = parse_duration(duration)
    if duration_seconds <= 0:
        await ctx.send("❌ Invalid duration format. Use a format like `1d`, `2h`, `30m`, or `45s`.")
        return False

    expires_at = datetime.now() + timedelta(seconds=duration_seconds)

    # Format duration for display
    duration_str = format_duration(duration_seconds)

    try:
        # Ban the user
        await member.ban(reason=f"{reason} (Temporary: {duration_str})")

        # Store in database for auto-unban
        await db.add_temp_moderation(
            server_id, member.id, moderator_id,
            "ban", reason, expires_at
        )

        # Send response
        embed = discord.Embed(
            title="🔨 Temporary Ban",
            description=f"{member.mention} has been temporarily banned.",
            color=discord.Color.red()
        )
        embed.add_field(name="Reason", value=reason, inline=False)
        embed.add_field(name="Duration", value=duration_str, inline=False)
        embed.add_field(name="Expires", value=expires_at.strftime("%Y-%m-%d %H:%M UTC"), inline=False)
        embed.add_field(name="Moderator", value=ctx.author.mention, inline=False)

        await ctx.send(embed=embed)

        # Log to audit channel
        audit_channel = discord.utils.get(ctx.guild.text_channels, name="audit-log")
        if audit_channel:
            await audit_channel.send(embed=embed)

        # Try to DM the user
        try:
            dm_embed = discord.Embed(
                title="🔨 You've Been Temporarily Banned",
                description=f"You have been temporarily banned from **{ctx.guild.name}**.",
                color=discord.Color.red()
            )
            dm_embed.add_field(name="Reason", value=reason, inline=False)
            dm_embed.add_field(name="Duration", value=duration_str, inline=False)
            dm_embed.add_field(name="Expires", value=expires_at.strftime("%Y-%m-%d %H:%M UTC"), inline=False)

            await member.send(embed=dm_embed)
        except discord.Forbidden:
            # User has DMs disabled, can't notify them
            pass

        return True
    except discord.Forbidden:
        await ctx.send("❌ I don't have permission to ban this member.")
        return False

async def temp_mute(ctx, member, duration, reason):
    """Temporarily mute a user"""
    server_id = ctx.guild.id
    moderator_id = ctx.author.id

    # Parse duration
    duration_seconds = parse_duration(duration)
    if duration_seconds <= 0:
        await ctx.send("❌ Invalid duration format. Use a format like `1d`, `2h`, `30m`, or `45s`.")
        return False

    expires_at = datetime.now() + timedelta(seconds=duration_seconds)

    # Format duration for display
    duration_str = format_duration(duration_seconds)

    # Get mute role
    const_vars = await db.get_constitutional_variables(server_id)
    mute_role_name = const_vars.get("mute_role", {"value": "Muted"})["value"]
    mute_role = discord.utils.get(ctx.guild.roles, name=mute_role_name)

    if not mute_role:
        # Create mute role if it doesn't exist
        try:
            mute_role = await create_mute_role(ctx.guild)
        except discord.Forbidden:
            await ctx.send("❌ I don't have permission to create roles.")
            return False

    try:
        # Add mute role to user
        await member.add_roles(mute_role, reason=f"{reason} (Temporary: {duration_str})")

        # Store in database for auto-unmute
        await db.add_temp_moderation(
            server_id, member.id, moderator_id,
            "mute", reason, expires_at
        )

        # Send response
        embed = discord.Embed(
            title="🔇 Temporary Mute",
            description=f"{member.mention} has been temporarily muted.",
            color=discord.Color.orange()
        )
        embed.add_field(name="Reason", value=reason, inline=False)
        embed.add_field(name="Duration", value=duration_str, inline=False)
        embed.add_field(name="Expires", value=expires_at.strftime("%Y-%m-%d %H:%M UTC"), inline=False)
        embed.add_field(name="Moderator", value=ctx.author.mention, inline=False)

        await ctx.send(embed=embed)

        # Log to audit channel
        audit_channel = discord.utils.get(ctx.guild.text_channels, name="audit-log")
        if audit_channel:
            await audit_channel.send(embed=embed)

        # Try to DM the user
        try:
            dm_embed = discord.Embed(
                title="🔇 You've Been Temporarily Muted",
                description=f"You have been temporarily muted in **{ctx.guild.name}**.",
                color=discord.Color.orange()
            )
            dm_embed.add_field(name="Reason", value=reason, inline=False)
            dm_embed.add_field(name="Duration", value=duration_str, inline=False)
            dm_embed.add_field(name="Expires", value=expires_at.strftime("%Y-%m-%d %H:%M UTC"), inline=False)

            await member.send(embed=dm_embed)
        except discord.Forbidden:
            # User has DMs disabled, can't notify them
            pass

        return True
    except discord.Forbidden:
        await ctx.send("❌ I don't have permission to add roles to this member.")
        return False

async def unmute(ctx, member, reason=None):
    """Manually unmute a user"""
    server_id = ctx.guild.id

    # Get mute role
    const_vars = await db.get_constitutional_variables(server_id)
    mute_role_name = const_vars.get("mute_role", {"value": "Muted"})["value"]
    mute_role = discord.utils.get(ctx.guild.roles, name=mute_role_name)

    if not mute_role:
        await ctx.send(f"❌ Mute role `{mute_role_name}` not found.")
        return False

    if mute_role not in member.roles:
        await ctx.send(f"❌ {member.mention} is not muted.")
        return False

    try:
        # Remove mute role
        await member.remove_roles(mute_role, reason=reason or "Manual unmute")

        # Send response
        await ctx.send(f"✅ {member.mention} has been unmuted.")

        # Log to audit channel
        audit_channel = discord.utils.get(ctx.guild.text_channels, name="audit-log")
        if audit_channel:
            await audit_channel.send(f"🔊 **Manual Unmute**: {member.mention} has been unmuted by {ctx.author.mention}.")

        return True
    except discord.Forbidden:
        await ctx.send("❌ I don't have permission to remove roles from this member.")
        return False

# ========================
# 🔹 HELPER FUNCTIONS
# ========================

async def create_mute_role(guild):
    """Create a mute role with appropriate permissions"""
    # Create the role
    mute_role = await guild.create_role(
        name="Muted",
        color=discord.Color.dark_gray(),
        reason="Creating mute role for moderation"
    )

    # Set permissions for all text channels
    for channel in guild.text_channels:
        await channel.set_permissions(
            mute_role,
            send_messages=False,
            add_reactions=False,
            reason="Setting up mute role permissions"
        )

    # Set permissions for all voice channels
    for channel in guild.voice_channels:
        await channel.set_permissions(
            mute_role,
            speak=False,
            stream=False,
            reason="Setting up mute role permissions"
        )

    return mute_role

def parse_duration(duration_str):
    """Parse a duration string (e.g., '1d', '2h', '30m') into seconds"""
    if not duration_str:
        return 0

    total_seconds = 0
    pattern = r'(\d+)([dhms])'

    for match in re.finditer(pattern, duration_str.lower()):
        value, unit = match.groups()
        value = int(value)

        if unit == 'd':
            total_seconds += value * 86400  # days to seconds
        elif unit == 'h':
            total_seconds += value * 3600   # hours to seconds
        elif unit == 'm':
            total_seconds += value * 60     # minutes to seconds
        elif unit == 's':
            total_seconds += value          # seconds

    return total_seconds

def format_duration(seconds):
    """Format a duration in seconds to a human-readable string"""
    if seconds < 60:
        return f"{seconds} second{'s' if seconds != 1 else ''}"

    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''}"

    hours = minutes // 60
    if hours < 24:
        return f"{hours} hour{'s' if hours != 1 else ''}"

    days = hours // 24
    return f"{days} day{'s' if days != 1 else ''}"




async def check_expired_moderations(bot: discord.Client):
    """
    Finds any temp‐ban or temp‐mute entries in the database whose
    expiry time has passed, then unbans/unmutes the user and
    removes the DB record. Logs each action to an 'audit-log' channel.
    """
    print("TASK: Checking for expired moderations...")
    now = datetime.now(timezone.utc)

    try:
        # 1) Handle expired bans
        expired_bans = await db.get_expired_moderations("ban")
        if expired_bans:
            print(f"TASK: Found {len(expired_bans)} expired bans.")
        for ban in expired_bans:
            guild = bot.get_guild(ban["server_id"])
            if not guild:
                # Clean up orphaned record
                await db.remove_temp_moderation(ban["action_id"])
                continue

            user_id = ban["user_id"]
            try:
                await guild.unban(discord.Object(id=user_id), reason="Temporary ban expired")
                print(f"TASK: Auto-unbanned user {user_id} in guild {guild.id}.")
                # Send to audit-log
                ch = discord.utils.get(guild.text_channels, name="audit-log")
                if ch:
                    await ch.send(f"🔓 **Auto-unban**: <@{user_id}> (temp ban expired).")
            except discord.NotFound:
                print(f"DEBUG: User {user_id} not banned in guild {guild.id}.")
            except discord.Forbidden:
                print(f"ERROR: Missing unban perms in guild {guild.id}.")
            except Exception as e:
                print(f"ERROR during auto-unban {user_id} in {guild.id}: {e}")
            # Always remove the DB entry so we don’t retry forever
            await db.remove_temp_moderation(ban["action_id"])

        # 2) Handle expired mutes
        expired_mutes = await db.get_expired_moderations("mute")
        if expired_mutes:
            print(f"TASK: Found {len(expired_mutes)} expired mutes.")
        for mute in expired_mutes:
            guild = bot.get_guild(mute["server_id"])
            if not guild:
                await db.remove_temp_moderation(mute["action_id"])
                continue

            user_id = mute["user_id"]
            member = guild.get_member(user_id)
            if member:
                # Lookup the mute role from your stored config
                vars = await db.get_constitutional_variables(guild.id)
                role_name = vars.get("mute_role", {"value": "Muted"})["value"]
                mute_role = discord.utils.get(guild.roles, name=role_name)

                if mute_role and mute_role in member.roles:
                    try:
                        await member.remove_roles(mute_role, reason="Temporary mute expired")
                        print(f"TASK: Auto-unmuted user {user_id} in guild {guild.id}.")
                        ch = discord.utils.get(guild.text_channels, name="audit-log")
                        if ch:
                            await ch.send(f"🔉 **Auto-unmute**: {member.mention} (temp mute expired).")
                    except discord.Forbidden:
                        print(f"ERROR: Missing unmute perms in guild {guild.id}.")
                    except Exception as e:
                        print(f"ERROR during auto-unmute {user_id} in {guild.id}: {e}")
            # Remove record in all cases
            await db.remove_temp_moderation(mute["action_id"])

    except Exception as e:
        print(f"CRITICAL ERROR in check_expired_moderations: {e}")
