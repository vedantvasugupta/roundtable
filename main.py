import discord
from discord.ext import commands
import asyncio
from datetime import datetime, timedelta, timezone
# Import custom modules
import db
import traceback

import voting
import proposals
import moderation
import utils  # Add this new import
import voting_utils
# Define intents explicitly
intents = discord.Intents.default()
intents.messages = True
intents.guilds = True
intents.reactions = True
intents.members = True  # Required for role updates & member fetching
intents.message_content = True  # Required for command handling

bot = commands.Bot(command_prefix="!", intents=intents)
update_queue = asyncio.Queue()  # Create the queue
bot.update_queue = update_queue  # Attach queue to bot object

# Default Channels and Messages
CHANNELS = {
    "rules": "rules-and-agreement",
    "announcements": "announcements",
    "proposals": "proposals",
    "voting": "voting-room",
    "logs": "governance-logs",
    "general": "general",
    "results": "governance-results",
    "guide": "server-guide"
}

CONSTITUTION_TEXT = """
📜 **Welcome to the Server!**
Before you gain full access, please read and agree to our governance rules.

✅ **React with ✅ below to agree to the rules and unlock the server.**
🚫 Failure to comply with the rules may result in removal.
"""

# ========================
# 🔹 BOT EVENTS & COMMANDS
# ========================


@bot.event
async def on_command_error(ctx, error):
    """Handle command errors and provide helpful suggestions for typos"""
    if isinstance(error, commands.CommandNotFound):
        # Extract the command name from the error message
        command_name = str(error).split('"')[1]

        # Get all command names
        all_commands = [cmd.name for cmd in bot.commands]
        all_commands.extend([alias for cmd in bot.commands for alias in cmd.aliases])

        # Find similar commands using string similarity
        similar_commands = []
        for cmd in all_commands:
            # Simple similarity check - if the first few characters match
            if cmd.startswith(command_name[:2]) or command_name.startswith(cmd[:2]):
                similar_commands.append(cmd)

        if similar_commands:
            suggestions = ", ".join([f"`!{cmd}`" for cmd in similar_commands[:3]])
            await ctx.send(f"Command `!{command_name}` not found. Did you mean: {suggestions}?")
        else:
            await ctx.send(f"Command `!{command_name}` not found. Use `!help` to see available commands.")
    else:
        # Handle other types of errors
        print(f"Command error: {error}")

@bot.event
async def on_ready():
    print(f"🤖 Bot is online as {bot.user}")
    await db.init_db()  # Ensure database is initialized

    for guild in bot.guilds:
        print(f"📌 Setting up in {guild.name}")
        await debug_roles_permissions(guild)  # Debug all roles and permissions

        # Store server info in database
        await db.add_server(guild.id, guild.name, guild.owner_id, guild.member_count)

        # Fetch settings for this server (or set defaults)
        settings = await db.get_settings(guild.id)
        if not settings:
            await db.update_setting(guild.id, "admission_method", "anyone")
            await db.update_setting(guild.id, "removal_method", "admin")

        # Initialize constitutional variables
        await db.init_constitutional_variables(guild.id)

        # Enforce all governance settings
        await enforce_all_permissions(guild)

        # Ensure audit log channel exists & is configured
        await setup_audit_log_channel(guild)

        # Ensure other governance channels exist
        for channel_key in ["proposals", "voting", "results"]:
            channel_name = CHANNELS.get(channel_key)
            if channel_name:
                # Calls utils.get_or_create_channel() from the imported utils module
                await utils.get_or_create_channel(guild, channel_name, bot.user.id)

        # Create and send the server guide
        await create_and_send_server_guide(guild, bot)

    # Check for any pending result announcements
    print("🔍 Checking for pending result announcements...")
    await announce_pending_results(bot)
    print("✅ Pending result announcements checked")

    # Start background tasks
    bot.loop.create_task(check_proposal_deadlines_task(bot))

    bot.loop.create_task(pending_results_loop(bot))
    bot.loop.create_task(expired_moderations_loop(bot))
    bot.loop.create_task(update_tracking_worker(bot, update_queue))

    # Maybe a separate task to periodically update tracking messages?
    # bot.loop.create_task(update_all_voting_tracking_task(bot)) # Example task


    print("✅ All servers are set up and ready!\n")


async def update_tracking_worker(bot: commands.Bot, queue: asyncio.Queue):
    await bot.wait_until_ready()
    print("TASK: Update tracking worker started.")
    while not bot.is_closed():
        try:
            # Wait for an item from the queue
            item = await queue.get()
            guild_id = item.get('guild_id')
            proposal_id = item.get('proposal_id')

            if guild_id is None or proposal_id is None:
                print(f"WARNING: Received invalid item from queue: {item}")
                queue.task_done()
                continue

            guild = bot.get_guild(guild_id)
            if guild:
                # --- ADD THIS LOG ---
                print(
                    f"TASK: Processing tracker update for proposal {proposal_id} in guild {guild_id} from queue.")
                try:
                    # Call the update function here
                    # Pass guild object and proposal ID
                    await voting_utils.update_vote_tracking(guild, proposal_id)
                    print(
                        f"TASK: Tracker update complete for proposal {proposal_id}.")
                except Exception as e:
                    print(
                        f"ERROR in update_tracking_worker for proposal {proposal_id}: {e}")
                    import traceback
                    traceback.print_exc()  # Ensure traceback is printed for errors in the worker
            else:
                print(
                    f"WARNING: Guild {guild_id} not found for tracker update.")

            # Mark the task as done
            queue.task_done()

        except Exception as e:
            print(f"CRITICAL ERROR in update_tracking_worker: {e}")
            import traceback
            traceback.print_exc()
        # Keep the sleep
        await asyncio.sleep(5)

async def check_proposal_deadlines_task(bot):
    from voting_utils import check_expired_proposals  # Import only necessary function
    """Background task to check for proposals with expired deadlines and close them"""
    while True:
        try:
           print("TASK: Checking for expired proposals...")
           # check_expired_proposals now handles closing and setting pending flag
           # Returns list of proposals *just* closed
           closed_proposals = await check_expired_proposals()

           # The pending_results_loop will handle the announcements from here

        except Exception as e:
           print(f"CRITICAL ERROR in check_proposal_deadlines_task: {e}")
           import traceback
           traceback.print_exc()

        await asyncio.sleep(20)  # Check every 20 seconds


async def expired_moderations_loop(bot):
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
           # Assumes moderation.check_expired_moderations exists and handles database/discord actions
           # If not, implement it based on the logic previously in main's check_proposal_deadlines
           # Pass bot instance if needed for discord actions
           await moderation.check_expired_moderations(bot)
        except Exception as e:
           print("Error in expired_moderations_loop:", e)
           import traceback
           traceback.print_exc()
        await asyncio.sleep(60)


async def pending_results_loop(bot):
   await bot.wait_until_ready()
   while not bot.is_closed():
       try:
           # announce_pending_results checks the flag and announces
           # Pass bot instance
           await announce_pending_results(bot)
       except Exception as e:
           print("pending_results_loop error:", e)
           import traceback
        #    traceback.print_exc()
       await asyncio.sleep(30)

@bot.event
async def on_guild_join(guild):
    """Handles when the bot joins a new server."""
    await db.add_server(guild.id, guild.name, guild.owner_id, guild.member_count)
    await db.update_setting(guild.id, "admission_method", "anyone")
    await db.init_constitutional_variables(guild.id)
    # Ensure audit log channel exists & is configured
    await setup_audit_log_channel(guild)
    # Ensure other governance channels exist
    for channel_key in ["proposals", "voting", "results"]:
        channel_name = CHANNELS.get(channel_key)
        if channel_name:
            await utils.get_or_create_channel(guild, channel_name, bot.user.id)
    # Create and send the server guide
    await create_and_send_server_guide(guild, bot)


@bot.event
async def on_raw_reaction_add(payload):
    """Grants access when a user reacts to the Constitution message."""
    if payload.emoji.name == "✅":
        guild = bot.get_guild(payload.guild_id)
        member = guild.get_member(payload.user_id)
        verified_role = discord.utils.get(guild.roles, name="Verified")

        if verified_role and member:
            await member.add_roles(verified_role)
            print(f"✅ {member.name} has agreed to the rules and got Verified role")


@bot.command()
async def get_setting(ctx, key: str):
    """Retrieve a specific setting's value."""
    settings = await db.get_settings(ctx.guild.id)
    value = settings.get(key, "Not Set")
    await ctx.send(f"🔍 `{key}` is set to `{value}`.")


@bot.command(name="constitution", aliases=["constiution", "constituion", "const"])
async def constitution(ctx):
    """Displays the server constitution as an embed with examples."""
    settings = await db.get_settings(ctx.guild.id)
    const_vars = await db.get_constitutional_variables(ctx.guild.id)

    # Create main embed with governance settings
    main_embed = discord.Embed(
        title="📜 Server Constitution",
        description="This is the official governance constitution for this server.",
        color=discord.Color.blue()
    )

    # Add governance settings
    governance_text = f"""
    ✅ **Admission Method:** {settings.get("admission_method", "Not Set")}
    🚫 **Removal Method:** {settings.get("removal_method", "Not Set")}
    📜 **Immutable Rule Changes:** {settings.get("immutable_rule_change_method", "Not Set")}
    📊 **Default Voting Protocol:** {settings.get("default_voting_protocol", "Not Set")}
    """

    main_embed.add_field(name="Governance Settings", value=governance_text, inline=False)

    # Add constitutional variables summary
    const_vars_text = ""
    for var_name, var_data in const_vars.items():
        const_vars_text += f"• **{var_name}:** {var_data['value']}\n"

    main_embed.add_field(name="Constitutional Variables", value=const_vars_text, inline=False)

    # Add examples section
    examples_text = """
    **Examples of changing settings:**
    • `!set_setting admission_method anyone` - Allow anyone to invite new members
    • `!set_setting default_voting_protocol plurality` - Set default voting to simple majority

    **Examples of changing constitutional variables:**
    • `!set_constitutional_var proposal_requires_approval false` - Allow proposals to skip admin approval
    • `!set_constitutional_var eligible_voters_role everyone` - Allow everyone to vote on proposals
    • `!set_constitutional_var warning_threshold 5` - Increase warning threshold to 5
    • `!set_constitutional_var vote_privacy anonymous` - Hide voter names in audits
    """

    main_embed.add_field(name="Examples", value=examples_text, inline=False)

    # Add footer
    main_embed.set_footer(text=f"Requested by {ctx.author.name}",
                     icon_url=ctx.author.avatar.url if ctx.author.avatar else None)

    # Send the embed
    await ctx.send(embed=main_embed)


@bot.command()
@commands.has_permissions(administrator=True)
async def set_setting(ctx, key: str, value: str):
    """Allows administrators to update governance settings but only to predefined values."""

    # Define allowed settings and their valid values
    valid_settings = {
        "admission_method": ["admin", "vote", "anyone"],
        "removal_method": ["admin", "some_roles", "vote", "anyone"],
        "immutable_rule_change_method": ["3-step", "harder_majority"],
        "default_voting_protocol": ["plurality", "borda", "approval", "copeland", "runoff", "condorcet"]
    }

    # Check if the key might be a constitutional variable instead of a setting
    const_vars = await db.get_constitutional_variables(ctx.guild.id)
    if key in const_vars and key not in valid_settings:
        await ctx.send(f"⚠️ `{key}` is a constitutional variable, not a setting. "
                      f"Use `!set_constitutional_var {key} {value}` instead.")
        return

    # Check if the key is valid
    if key not in valid_settings:
        await ctx.send(f"⚠️ `{key}` is not a valid setting.")
        return

    # Check if the value is valid for this setting
    if value not in valid_settings[key]:
        valid_values_str = ", ".join(valid_settings[key])
        await ctx.send(f"🚫 Invalid value for `{key}`. Allowed values: `{valid_values_str}`.")
        return

    # Get current settings before updating
    current_settings = await db.get_settings(ctx.guild.id)
    current_value = current_settings.get(key, None)

    # Check if the value is actually changing
    if current_value == value:
        await ctx.send(f"🔍 `{key}` is already set to `{value}`, no changes made.")
        return

    # Update the setting in the database
    await db.update_setting(ctx.guild.id, key, value)
    await ctx.send(f"✅ Setting `{key}` updated to `{value}`.")

    # Enforce the updated settings
    await enforce_all_permissions(ctx.guild)
    await ctx.send(f"🔄 `{key}` updated, permissions have been enforced.")


@bot.command()
@commands.has_permissions(administrator=True)
async def set_constitutional_var(ctx, variable_name: str, value: str):
    """Update a constitutional variable."""
    server_id = ctx.guild.id

    # Get current value
    const_vars = await db.get_constitutional_variables(server_id)
    if variable_name not in const_vars:
        await ctx.send(f"❌ Constitutional variable `{variable_name}` does not exist.")
        return

    current_value = const_vars[variable_name]["value"]
    if current_value == value:
        await ctx.send(f"🔍 `{variable_name}` is already set to `{value}`, no changes made.")
        return

    # Update the variable
    await db.update_constitutional_variable(server_id, variable_name, value)
    await ctx.send(f"✅ Constitutional variable `{variable_name}` updated to `{value}`.")

    # Log to audit channel
    audit_channel = discord.utils.get(ctx.guild.text_channels, name="audit-log")
    if audit_channel:
        await audit_channel.send(f"📜 **Constitutional Variable Updated**: `{variable_name}` changed from `{current_value}` to `{value}` by {ctx.author.mention}")


# ========================
# 🔹 PROPOSAL COMMANDS
# ========================

@bot.command(name="propose")
async def propose(ctx: commands.Context): # Ensure type hint for ctx
    """Command to initiate the proposal creation flow.
    Users will be presented with buttons to choose a voting mechanism.

    Note: For the best user experience (e.g., message editing on timeout/completion),
    this command would ideally be a slash command or hybrid command, which provides
    an interaction context for the initial message.
    """
    try:
        # Call the new proposal creation flow function from the proposals module
        await proposals.start_proposal_creation_flow(ctx)
    except Exception as e:
        print(f"❌ Error in !propose command (main.py): {e}")
        traceback.print_exc() # It's useful to have traceback here too
        try:
            # Try to send an error message to the user
            if ctx.interaction and not ctx.interaction.response.is_done():
                await ctx.interaction.response.send_message(f"❌ An error occurred while starting the proposal process: {e}", ephemeral=True)
            else:
                await ctx.send(f"❌ An error occurred while starting the proposal process: {e}")
        except Exception as e_report:
            print(f"Error sending !propose error report: {e_report}")

# The old create_button logic is now part of ProposalMechanismSelectionView and its buttons in proposals.py
# So, the commented-out section below is no longer needed here.
# # In proposals.py's ProposalView create_button callback:
#     async def create_button(self, interaction: discord.Interaction, button: discord.ui.Button):
#         try:
#             print(
#                 f"🔍 {interaction.user} clicked the Create Proposal button (invoker: {self.ctx.author})")
#             # Send the modal - Pass the interaction object to the modal constructor
#             # IMPORTANT: Pass the interaction here, NOT the original ctx
#             modal = ProposalModal(interaction)
#             await interaction.response.send_modal(modal)
#             print(f"✅ ProposalModal sent to {interaction.user}")
#
#             # Stop the view after the modal is sent, as it's no longer needed
#             self.stop()
#
#         except Exception as e:
#             print(f"❌ Error in ProposalView create_button: {e}")
#             await interaction.response.send_message(f"❌ An error occurred while opening the proposal form: {e}", ephemeral=True)

@bot.command()
@commands.has_permissions(administrator=True)
async def approve_proposal(ctx, proposal_id: int):
    """Approve a pending proposal and start voting."""
    await proposals.approve_proposal(ctx, proposal_id)


@bot.command()
@commands.has_permissions(administrator=True)
async def reject_proposal(ctx, proposal_id: int, *, reason: str):
    """Reject a pending proposal."""
    await proposals.reject_proposal(ctx, proposal_id, reason)


@bot.command()
async def vote(ctx, proposal_id: int, *args):
    """Cast a vote on a proposal.

    Usage depends on the voting mechanism:
    - Plurality: !vote <proposal_id> <option>
    - Borda/Runoff/Condorcet: !vote <proposal_id> rank option1,option2,option3,...
    - Approval: !vote <proposal_id> approve option1,option2,...
    """
    # Delete the command message for privacy
    try:
        await ctx.message.delete()
    except discord.Forbidden:
        pass

    # Check if this is a DM channel
    if not isinstance(ctx.channel, discord.DMChannel):
        await ctx.send("⚠️ Please vote via DM for privacy. I've sent you instructions.")

        # Send DM with instructions
        try:
            proposal = await db.get_proposal(proposal_id)
            if proposal:
                options = utils.extract_options_from_description(proposal['description'])
                if not options:
                    options = ["Yes", "No"]

                await voting.send_voting_dm(ctx.author, proposal, options)
            else:
                await ctx.author.send(f"❌ Proposal #{proposal_id} not found.")
        except discord.Forbidden:
            await ctx.send("❌ I couldn't send you a DM. Please enable DMs from server members.")

        return

    # Process the vote
    if len(args) == 0:
        await ctx.send("❌ Missing vote option. Please specify your vote.")
        return

    # Handle different voting mechanisms
    proposal = await db.get_proposal(proposal_id)
    if not proposal:
        await ctx.send(f"❌ Proposal #{proposal_id} not found.")
        return

    voting_mechanism = proposal['voting_mechanism'].lower()

    vote_data = {}
    if voting_mechanism == "plurality":
        # Single option vote
        vote_data = {"option": args[0]}

    elif voting_mechanism in ["borda", "runoff", "condorcet"]:
        # Ranked vote
        if len(args) < 2 or args[0].lower() != "rank":
            await ctx.send("❌ For ranked voting, use: `!vote <proposal_id> rank option1,option2,option3,...`")
            return

        rankings = args[1].split(',')
        vote_data = {"rankings": rankings}

    elif voting_mechanism == "approval":
        # Multiple option vote
        if len(args) < 2 or args[0].lower() != "approve":
            await ctx.send("❌ For approval voting, use: `!vote <proposal_id> approve option1,option2,...`")
            return

        approved = args[1].split(',')
        vote_data = {"approved": approved}

    # Record the vote
    success, message = await voting.process_vote(ctx.author.id, proposal_id, vote_data)
    await ctx.send(message)


@bot.command(name="proposals")
async def list_proposals(ctx, status: str = None):
    server_id = ctx.guild.id

    # Get proposals
    if status:
        status = status.capitalize()
        server_proposals = await db.get_server_proposals(server_id, status)
        status_text = f" with status '{status}'"
    else:
        server_proposals = await db.get_server_proposals(server_id)
        status_text = ""

    if not server_proposals:
        await ctx.send(f"📜 No proposals found{status_text}.")
        return

    # Create embed for proposals
    embed = discord.Embed(
        title=f"📜 Proposals{status_text}",
        description=f"Found {len(server_proposals)} proposal(s){status_text}.",
        color=discord.Color.blue()
    )

    # Add each proposal to the embed
    for proposal in server_proposals[:10]:  # Limit to 10 proposals to avoid hitting embed limits
        # Format deadline
        deadline = datetime.fromisoformat(proposal['deadline'].replace('Z', '+00:00'))
        deadline_str = deadline.strftime("%Y-%m-%d %H:%M UTC")

        # Format description (truncated)
        description = proposal['description']
        if len(description) > 100:
            description = description[:97] + "..."

        embed.add_field(
            name=f"#{proposal['proposal_id']}: {proposal['title']}",
            value=f"**Status:** {proposal['status']}\n**Deadline:** {deadline_str}\n**Mechanism:** {proposal['voting_mechanism'].title()}\n{description}",
            inline=False
        )

    if len(server_proposals) > 10:
        embed.set_footer(text=f"Showing 10 of {len(server_proposals)} proposals. Use !proposal <id> to view details.")

    await ctx.send(embed=embed)


@bot.command()
async def proposal(ctx, proposal_id: int):
    """View details of a specific proposal."""
    # Get proposal
    proposal = await db.get_proposal(proposal_id)
    if not proposal:
        await ctx.send(f"❌ Proposal #{proposal_id} not found.")
        return

    # Get proposer
    proposer = ctx.guild.get_member(proposal['proposer_id'])
    proposer_name = proposer.mention if proposer else f"<@{proposal['proposer_id']}>"

    # Format deadline
    deadline = datetime.fromisoformat(proposal['deadline'].replace('Z', '+00:00'))
    deadline_str = deadline.strftime("%Y-%m-%d %H:%M UTC")

    # Create embed
    embed = discord.Embed(
        title=f"Proposal #{proposal_id}: {proposal['title']}",
        description=proposal['description'],
        color=discord.Color.blue()
    )

    # Add metadata
    embed.add_field(name="Status", value=proposal['status'], inline=True)
    embed.add_field(name="Proposer", value=proposer_name, inline=True)
    embed.add_field(name="Voting Mechanism", value=proposal['voting_mechanism'].title(), inline=True)
    embed.add_field(name="Deadline", value=deadline_str, inline=True)

    # Add results if available
    if proposal['status'] in ['Passed', 'Failed']:
        results = await db.get_proposal_results(proposal_id)
        if results:
            # Format results based on voting mechanism
            if results['mechanism'] == 'plurality':
                result_text = "\n".join([f"**{option}**: {count} votes" for option, count in results['results']])
                embed.add_field(name="Results", value=result_text or "No votes cast", inline=False)
            elif results['mechanism'] == 'borda':
                result_text = "\n".join([f"**{option}**: {points} points" for option, points in results['results']])
                embed.add_field(name="Results", value=result_text or "No votes cast", inline=False)
            elif results['mechanism'] == 'approval':
                result_text = "\n".join([f"**{option}**: {count} approvals" for option, count in results['results']])
                embed.add_field(name="Results", value=result_text or "No votes cast", inline=False)

            if results.get('winner'):
                embed.add_field(name="Winner", value=results['winner'], inline=False)

    await ctx.send(embed=embed)


# ========================
# 🔹 MODERATION COMMANDS
# ========================

@bot.command()
@commands.has_permissions(kick_members=True)
async def warn(ctx, member: discord.Member, *, reason: str):
    """Issue a warning to a user."""
    await moderation.warn_user(ctx, member, reason)


@bot.command()
@commands.has_permissions(kick_members=True)
async def warnings(ctx, member: discord.Member):
    """View warnings for a user."""
    await moderation.get_warnings(ctx, member)


@bot.command()
@commands.has_permissions(administrator=True)
async def clearwarnings(ctx, member: discord.Member):
    """Clear all warnings for a user."""
    await moderation.clear_user_warnings(ctx, member)


@bot.command()
@commands.has_permissions(ban_members=True)
async def tempban(ctx, member: discord.Member, duration: str, *, reason: str):
    """Temporarily ban a user.

    Duration format: 1d, 2h, 30m, etc.
    """
    await moderation.temp_ban(ctx, member, duration, reason)


@bot.command()
@commands.has_permissions(kick_members=True)
async def tempmute(ctx, member: discord.Member, duration: str, *, reason: str):
    """Temporarily mute a user.

    Duration format: 1d, 2h, 30m, etc.
    """
    await moderation.temp_mute(ctx, member, duration, reason)


@bot.command()
@commands.has_permissions(kick_members=True)
async def unmute(ctx, member: discord.Member, *, reason: str = None):
    """Unmute a user."""
    await moderation.unmute(ctx, member, reason)


# ========================
# 🔹 HELPER FUNCTIONS
# ========================

async def enforce_admission_permissions(guild):
    """Enforces admission settings by adjusting invite permissions for roles."""
    settings = await db.get_settings(guild.id)
    admission_method = settings.get("admission_method", "admin")

    print(
        f"\n🔄 Enforcing admission method '{admission_method}' in {guild.name}")

    changes_made = False

    bot_roles = [role for role in guild.roles if role.managed]
    roles_to_check = [role for role in guild.roles if role not in bot_roles]

    print(f"⚠️ Skipping bot roles: {', '.join([r.name for r in bot_roles])}")

    if admission_method == "admin":
        for role in roles_to_check:
            if role.name == "Admin":
                if not role.permissions.create_instant_invite:
                    new_permissions = role.permissions
                    new_permissions.update(create_instant_invite=True)
                    await role.edit(permissions=new_permissions)
                    print(f"✅ Allowed invites for {role.name}")
                    changes_made = True
            else:
                if role.permissions.create_instant_invite:
                    new_permissions = role.permissions
                    new_permissions.update(create_instant_invite=False)
                    await role.edit(permissions=new_permissions)
                    print(f"🚫 Removed invite permissions from {role.name}")
                    changes_made = True

    elif admission_method == "anyone":
        for role in roles_to_check:
            if not role.permissions.create_instant_invite:
                new_permissions = role.permissions
                new_permissions.update(create_instant_invite=True)
                await role.edit(permissions=new_permissions)
                print(f"✅ Allowed invites for {role.name}")
                changes_made = True

    return changes_made


async def enforce_removal_permissions(guild):
    """Enforces removal settings by adjusting kick/ban permissions for roles."""
    settings = await db.get_settings(guild.id)
    removal_method = settings.get("removal_method", "admin")

    print(f"\n🔄 Enforcing removal method '{removal_method}' in {guild.name}")

    changes_made = False

    bot_roles = [role for role in guild.roles if role.managed]
    roles_to_check = [role for role in guild.roles if role not in bot_roles]

    print(f"⚠️ Skipping bot roles: {', '.join([r.name for r in bot_roles])}")

    if removal_method == "admin":
        for role in roles_to_check:
            if role.name == "Admin":
                if not role.permissions.kick_members:
                    new_permissions = role.permissions
                    new_permissions.update(kick_members=True, ban_members=True)
                    await role.edit(permissions=new_permissions)
                    print(f"✅ Allowed Admins to remove members")
                    changes_made = True
            else:
                if role.permissions.kick_members or role.permissions.ban_members:
                    new_permissions = role.permissions
                    new_permissions.update(
                        kick_members=False, ban_members=False)
                    await role.edit(permissions=new_permissions)
                    print(f"🚫 Removed kick/ban permissions from {role.name}")
                    changes_made = True

    elif removal_method == "anyone":
        for role in roles_to_check:
            if not role.permissions.kick_members:
                new_permissions = role.permissions
                new_permissions.update(kick_members=True, ban_members=True)
                await role.edit(permissions=new_permissions)
                print(f"✅ Allowed all roles to remove members")
                changes_made = True

    return changes_made


async def enforce_all_permissions(guild):
    """Calls all individual permission enforcement functions to ensure settings are applied."""
    print(f"\n🔄 Enforcing all permissions for {guild.name}")

    admission_changes = await enforce_admission_permissions(guild)
    removal_changes = await enforce_removal_permissions(guild)

    if admission_changes or removal_changes:
        print(f"✅ Finished enforcing permissions in {guild.name}.\n")
    else:
        print(
            f"✅ No changes needed. Permissions were already correct in {guild.name}.\n")


async def debug_roles_permissions(guild):
    """Prints all roles and their permissions for debugging."""

    print(f"\n📜 Debugging Roles & Permissions for {guild.name}\n{'='*50}")

    for role in guild.roles:
        perms = role.permissions  # Get role permissions
        # List of enabled permissions
        role_permissions = [perm for perm, value in perms if value]

        print(f"🔹 **Role:** {role.name}")
        print(
            f"   ✅ Enabled Permissions: {', '.join(role_permissions) if role_permissions else 'None'}")
        print("-" * 50)

    print("✅ Finished listing roles and permissions.\n")


async def setup_audit_log_channel(guild):
    """Creates or updates the #audit-log channel and ensures correct permissions."""
    channel_name = "audit-log"

    # Check if the channel already exists
    audit_channel = discord.utils.get(guild.text_channels, name=channel_name)

    # Define permission overwrites: Only the bot can send messages
    overwrites = {}
    if guild.default_role is not None:
        overwrites[guild.default_role] = discord.PermissionOverwrite(read_messages=True, send_messages=False)

    if guild.me is not None:
        overwrites[guild.me] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

    if audit_channel:
        print(
            f"📜 Audit log channel already exists in {guild.name}, updating permissions...")
        # ✅ Update permissions if needed
        await audit_channel.edit(overwrites=overwrites)
    else:
        # Create the channel if it doesn't exist
        audit_channel = await guild.create_text_channel(name=channel_name, overwrites=overwrites)
        print(f"✅ Created #audit-log in {guild.name} with correct permissions")

    # Fetch and send full audit log history
    await send_audit_logs(guild, audit_channel)


async def send_audit_logs(guild, channel):
    """Fetches and sends a detailed audit log to the audit-log channel, tracking role updates and permission changes."""

    # ✅ Correct way to fetch logs
    logs = [entry async for entry in guild.audit_logs(limit=50)]

    if not logs:
        await channel.send("📜 No audit logs found.")
        return

    messages = []
    for entry in reversed(logs):  # ✅ Ensures latest log appears at the bottom
        action = entry.action.name.replace("_", " ").title()  # Format action
        user = entry.user.name if entry.user else "Unknown"
        target = entry.target.name if hasattr(
            entry.target, "name") else str(entry.target)
        timestamp = entry.created_at.strftime(
            '%Y-%m-%d %H:%M:%S')  # Human-readable time

        # Base log message
        log_entry = f"**{action}**\n🛠️ **Performed By:** `{user}`\n🎯 **Target:** `{target}`\n🕒 **Time:** `{timestamp}`"

        # Additional Details Based on Action Type
        if entry.action == discord.AuditLogAction.member_update:
            log_entry += f"\n🔄 **Before:** `{entry.before}`\n🔄 **After:** `{entry.after}`"

        elif entry.action == discord.AuditLogAction.member_role_update:
            added_roles = set(entry.after.roles) - set(entry.before.roles)
            removed_roles = set(entry.before.roles) - set(entry.after.roles)
            if added_roles:
                log_entry += f"\n➕ **Roles Added:** `{', '.join([role.name for role in added_roles])}`"
            if removed_roles:
                log_entry += f"\n➖ **Roles Removed:** `{', '.join([role.name for role in removed_roles])}`"

        elif entry.action == discord.AuditLogAction.overwrite_update:
            log_entry += f"\n🔧 **Permissions Changed:** `{entry.extra}`"

        elif entry.action == discord.AuditLogAction.role_update:
            before_perms = entry.before.permissions
            after_perms = entry.after.permissions

            changed_perms = [
                perm for perm, value in after_perms if value != getattr(before_perms, perm, None)
            ]

            if changed_perms:
                log_entry += f"\n🔑 **Permissions Changed:** `{', '.join(changed_perms)}`"

        elif entry.action == discord.AuditLogAction.message_delete:
            log_entry += f"\n📄 **Deleted Message:** `{entry.extra.get('content', 'Message content unavailable')}`"

        elif entry.action in [discord.AuditLogAction.kick, discord.AuditLogAction.ban]:
            log_entry += f"\n⚠️ **Reason:** `{entry.reason if entry.reason else 'No reason provided'}`"

        messages.append(log_entry)

    # Split logs into multiple messages if they exceed character limits
    chunk_size = 1800  # Discord limit ~2000 characters per message
    for i in range(0, len(messages), 10):
        chunk = "\n".join(messages[i:i+10])
        embed = discord.Embed(title="📜 **Audit Log History**",
                              description=chunk, color=discord.Color.blue())
        await channel.send(embed=embed)

    print(f"✅ Sent detailed audit log history to #audit-log in {guild.name}")


@bot.command(name="see_settings", aliases=["settings", "list_settings"])
async def see_settings(ctx, category: str = None):
    """Display all available settings with examples and descriptions"""

    # Define settings categories
    governance_settings = {
        "admission_method": {
            "description": "Controls who can invite new members to the server",
            "values": ["admin", "vote", "anyone"],
            "example": "!set_setting admission_method anyone",
            "current": None
        },
        "removal_method": {
            "description": "Controls who can remove members from the server",
            "values": ["admin", "some_roles", "vote", "anyone"],
            "example": "!set_setting removal_method admin",
            "current": None
        },
        "immutable_rule_change_method": {
            "description": "Controls how immutable rules can be changed",
            "values": ["3-step", "harder_majority"],
            "example": "!set_setting immutable_rule_change_method 3-step",
            "current": None
        },
        "default_voting_protocol": {
            "description": "Sets the default voting mechanism for proposals",
            "values": ["plurality", "borda", "approval", "copeland", "runoff", "condorcet"],
            "example": "!set_setting default_voting_protocol plurality",
            "current": None
        }
    }

    constitutional_vars = {
        "proposal_requires_approval": {
            "description": "Whether proposals need admin approval before voting starts",
            "values": ["true", "false"],
            "example": "!set_constitutional_var proposal_requires_approval false",
            "current": None
        },
        "eligible_proposers_role": {
            "description": "Role required to create proposals (or 'everyone')",
            "values": ["everyone", "<role_name>"],
            "example": "!set_constitutional_var eligible_proposers_role everyone",
            "current": None
        },
        "eligible_voters_role": {
            "description": "Role required to vote on proposals (or 'everyone')",
            "values": ["everyone", "<role_name>"],
            "example": "!set_constitutional_var eligible_voters_role everyone",
            "current": None
        },
        "warning_threshold": {
            "description": "Number of warnings before automatic action is taken",
            "values": ["1", "2", "3", "4", "5"],
            "example": "!set_constitutional_var warning_threshold 3",
            "current": None
        },
        "warning_action": {
            "description": "Action to take when warning threshold is reached",
            "values": ["kick", "ban", "mute"],
            "example": "!set_constitutional_var warning_action kick",
            "current": None
        },
        "mute_role": {
            "description": "Role to assign when muting a user",
            "values": ["Muted", "<role_name>"],
            "example": "!set_constitutional_var mute_role Muted",
            "current": None
        },
        "vote_privacy": {
            "description": "Whether audit displays names ('public') or anonymous IDs",
            "values": ["public", "anonymous"],
            "example": "!set_constitutional_var vote_privacy anonymous",
            "current": None
        }
    }

    # Get current values
    settings = await db.get_settings(ctx.guild.id)
    const_vars = await db.get_constitutional_variables(ctx.guild.id)

    # Update current values
    for key in governance_settings:
        governance_settings[key]["current"] = settings.get(key, "Not Set")

    for key in constitutional_vars:
        if key in const_vars:
            constitutional_vars[key]["current"] = const_vars[key]["value"]

    # Create embeds based on category
    if category and category.lower() == "governance":
        # Show only governance settings
        embed = discord.Embed(
            title="🛠️ Governance Settings",
            description="These settings control the basic governance structure of the server.",
            color=discord.Color.blue()
        )

        for key, data in governance_settings.items():
            value_text = f"**Description:** {data['description']}\n"
            value_text += f"**Current Value:** `{data['current']}`\n"
            value_text += f"**Allowed Values:** {', '.join([f'`{v}`' for v in data['values']])}\n"
            value_text += f"**Example:** `{data['example']}`"

            embed.add_field(name=key, value=value_text, inline=False)

    elif category and category.lower() == "constitutional":
        # Show only constitutional variables
        embed = discord.Embed(
            title="📋 Constitutional Variables",
            description="These variables control specific aspects of the governance system.",
            color=discord.Color.gold()
        )

        for key, data in constitutional_vars.items():
            value_text = f"**Description:** {data['description']}\n"
            value_text += f"**Current Value:** `{data['current']}`\n"
            value_text += f"**Allowed Values:** {', '.join([f'`{v}`' for v in data['values']])}\n"
            value_text += f"**Example:** `{data['example']}`"

            embed.add_field(name=key, value=value_text, inline=False)

    else:
        # Show overview with both categories
        embed = discord.Embed(
            title="⚙️ Server Settings Overview",
            description="Here are all the settings that control how this server operates.",
            color=discord.Color.blue()
        )

        # Add governance settings summary
        governance_text = ""
        for key, data in governance_settings.items():
            governance_text += f"• **{key}**: `{data['current']}` - {data['description']}\n"

        embed.add_field(
            name="🛠️ Governance Settings",
            value=governance_text + "\nUse `!see_settings governance` for more details.",
            inline=False
        )

        # Add constitutional variables summary
        const_text = ""
        for key, data in constitutional_vars.items():
            const_text += f"• **{key}**: `{data['current']}` - {data['description']}\n"

        embed.add_field(
            name="📋 Constitutional Variables",
            value=const_text + "\nUse `!see_settings constitutional` for more details.",
            inline=False
        )

        # Add usage instructions
        embed.add_field(
            name="📝 Usage",
            value=(
                "• To change governance settings: `!set_setting <key> <value>`\n"
                "• To change constitutional variables: `!set_constitutional_var <key> <value>`\n"
                "• To see detailed information: `!see_settings governance` or `!see_settings constitutional`"
            ),
            inline=False
        )

    await ctx.send(embed=embed)

@bot.command()
async def ping(ctx):
    await ctx.send("🏓 Pong!")


@bot.command(name="dummy")
async def dummy_proposal(ctx):
    """Create a simple plurality proposal and start voting without approval."""
    try:
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        title = f"Test Proposal {timestamp}"
        description = (
            f"This is a test proposal created at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}."
        )

        options = ["Option A", "Option B", "Option C"]
        deadline = (datetime.utcnow() + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S.%f")

        proposal_id = await db.create_proposal(
            server_id=ctx.guild.id,
            proposer_id=ctx.author.id,
            title=title,
            description=description,
            voting_mechanism="plurality",
            deadline=deadline,
            requires_approval=False,
            hyperparameters=None,
            campaign_id=None,
            scenario_order=None,
            # Start in a non-voting state so initiate_voting_for_proposal can
            # handle announcements and DM distribution
            initial_status="Pending",

        )

        if proposal_id:
            await db.add_proposal_options(proposal_id, options)
            success, msg = await voting_utils.initiate_voting_for_proposal(ctx.guild, proposal_id, bot)
            if success:
                await ctx.send(
                    f"✅ Dummy proposal #{proposal_id} created and voting started."
                )
            else:
                await ctx.send(
                    f"⚠️ Proposal #{proposal_id} created but voting failed: {msg}"
                )
        else:
            await ctx.send("❌ Failed to create dummy proposal.")

    except Exception as e:
        print(f"Error creating dummy proposal: {e}")
        import traceback
        traceback.print_exc()  # Print full stack trace for debugging
        await ctx.send(f"❌ Error creating dummy proposal: {e}")

@bot.command(name="terminate")
@commands.has_permissions(administrator=True)
async def terminate_proposal(ctx, proposal_id: int):
    """Terminate a proposal early and calculate results"""
    try:
        # Get the proposal
        proposal = await db.get_proposal(proposal_id)
        if not proposal:
            await ctx.send(f"❌ Proposal #{proposal_id} not found.")
            return

        if proposal['status'] != "Voting":
            await ctx.send(f"❌ Proposal #{proposal_id} is not in voting status (current status: {proposal['status']}).")
            return

        # Close the proposal
        from voting_utils import close_proposal

        # Debug print to track execution
        print(f"DEBUG: Terminating proposal #{proposal_id} early")

        # Call the imported close_proposal from voting_utils
        results = await close_proposal(proposal_id, ctx.guild)

        if results:
            # Debug print to show results were calculated
            print(f"DEBUG: Results calculated for proposal #{proposal_id}: {results}")

            # close_proposal already updates the proposal status to 'Closed'
            # Retrieve the updated proposal record
            proposal = await db.get_proposal(proposal_id)

            # Import and use close_and_announce_results
            from voting_utils import close_and_announce_results
            await close_and_announce_results(ctx.guild, proposal, results)

            await ctx.send(f"✅ Proposal #{proposal_id} has been terminated early and results have been announced.")
        else:
            await ctx.send(f"❌ Failed to calculate results for proposal #{proposal_id}.")

    except Exception as e:
        print(f"Error terminating proposal: {e}")
        import traceback
        traceback.print_exc()  # Print full stack trace for debugging
        await ctx.send(f"❌ Error terminating proposal: {e}")

@bot.command(name="track")
async def track_votes(ctx, proposal_id: int):
    """Display vote tracking information for a proposal"""
    try:
        # Get the proposal
        proposal = await db.get_proposal(proposal_id)
        if not proposal:
            await ctx.send(f"❌ Proposal #{proposal_id} not found.")
            return

        if proposal['status'] != "Voting":
            await ctx.send(f"❌ Proposal #{proposal_id} is not in voting status (current status: {proposal['status']}).")
            return

        # Update vote tracking
        await voting_utils.update_vote_tracking(ctx.guild, proposal_id)
        await ctx.send(f"✅ Vote tracking for proposal #{proposal_id} has been updated in the voting-room channel.")

    except Exception as e:
        print(f"Error tracking votes: {e}")
        await ctx.send(f"❌ Error tracking votes: {e}")


@bot.command(name="audit")
@commands.has_permissions(administrator=True)
async def audit(ctx, proposal_id: int):
    """Display who voted what for a proposal respecting vote privacy."""
    proposal = await db.get_proposal(proposal_id)
    if not proposal:
        await ctx.send(f"❌ Proposal #{proposal_id} not found.")
        return

    votes = await db.get_proposal_votes(proposal_id)
    if not votes:
        await ctx.send("No votes recorded.")
        return

    const_vars = await db.get_constitutional_variables(ctx.guild.id)
    privacy = const_vars.get("vote_privacy", {}).get("value", "public")

    lines = []
    for vote in votes:
        user_id = vote["user_id"]
        vote_data = vote.get("vote_data")
        if privacy == "anonymous":
            voter = await db.get_or_create_vote_identifier(ctx.guild.id, user_id, proposal_id, proposal.get("campaign_id"))
        else:
            member = ctx.guild.get_member(user_id)
            voter = member.display_name if member else str(user_id)
        lines.append(f"{voter}: {vote_data}")

    await ctx.send(f"**Vote Audit for Proposal #{proposal_id}**\n" + "\n".join(lines))


@bot.command(name="announce_results")
@commands.has_permissions(administrator=True)
async def announce_results_command(ctx, proposal_id: int = None):
    """Manually trigger the announcement of pending results"""
    try:
        if proposal_id:
            # Get the specific proposal
            proposal = await db.get_proposal(proposal_id)
            if not proposal:
                await ctx.send(f"❌ Proposal #{proposal_id} not found.")
                return

            if proposal['status'] not in ['Passed', 'Failed', 'Closed']:
                await ctx.send(f"❌ Proposal #{proposal_id} is not in a completed status (current status: {proposal['status']}).")
                return

            # Get the results
            results = await db.get_proposal_results(proposal_id)
            if not results:
                await ctx.send(f"❌ No results found for proposal #{proposal_id}.")
                return

            # Set the results_pending_announcement flag
            await db.update_proposal(proposal_id, {
                'results_pending_announcement': 1
            })

            # Announce the results
            from voting_utils import close_and_announce_results
            await close_and_announce_results(ctx.guild, proposal, results)

            # Clear the flag
            await db.update_proposal(proposal_id, {
                'results_pending_announcement': 0
            })

            await ctx.send(f"✅ Results for proposal #{proposal_id} have been announced.")
        else:
            # Announce all pending results
            await announce_pending_results(bot)
            await ctx.send("✅ All pending results have been announced.")

    except Exception as e:
        print(f"Error announcing results: {e}")
        import traceback
        traceback.print_exc()
        await ctx.send(f"❌ Error announcing results: {e}")


@bot.command(name="help_guide", aliases=["guide", "howto"])
async def help_guide(ctx):
    """Provides a guide on how to use the bot's commands"""
    embed = discord.Embed(
        title="📚 Bot Command Guide",
        description="Here's how to use the main features of this bot:",
        color=discord.Color.blue()
    )

    # Constitution and settings
    embed.add_field(
        name="📜 Constitution & Settings",
        value=(
            "• `!constitution` - View the server constitution\n"
            "• `!get_setting <key>` - View a specific setting\n"
            "• `!set_setting <key> <value>` - Update a setting (admin only)\n"
            "• `!set_constitutional_var <variable> <value>` - Update a constitutional variable (admin only)\n"
            "• `!see_settings [category]` - View all settings and variables\n"
            "• `!ping` - Check if the bot is online"
        ),
        inline=False
    )

    # Proposals
    embed.add_field(
        name="📝 Proposals",
        value=(
            "• `!propose` - Create a new proposal\n"
            "• `!proposals [status]` - List all proposals, optionally filtered by status\n"
            "• `!proposal <id>` - View details of a specific proposal\n"
            "• `!approve_proposal <id>` - Approve a pending proposal (admin only)\n"
            "• `!reject_proposal <id> <reason>` - Reject a pending proposal (admin only)\n"
            "• `!dummy` - Create a test proposal with random options\n"
            "• `!terminate <id>` - Terminate a proposal early (admin only)\n"
            "• `!track <id>` - Show vote tracking for a proposal\n"
            "• `!audit <id>` - Display who voted for what (admin only)\n"
            "• `!announce_results [id]` - Announce results for a specific proposal or all pending results (admin only)"
        ),
        inline=False
    )

    # Voting
    embed.add_field(
        name="🗳️ Voting",
        value=(
            "• `!vote <proposal_id> <option>` - Vote on a plurality proposal\n"
            "• `!vote <proposal_id> rank option1,option2,...` - Vote on a Borda/Runoff/Condorcet proposal\n"
            "• `!vote <proposal_id> approve option1,option2,...` - Vote on an approval proposal\n"
            "Note: Voting is best done via DM for privacy"
        ),
        inline=False
    )

    # Moderation
    embed.add_field(
        name="🛡️ Moderation",
        value=(
            "• `!warn <@user> <reason>` - Issue a warning to a user\n"
            "• `!warnings <@user>` - View warnings for a user\n"
            "• `!clearwarnings <@user>` - Clear all warnings for a user (admin only)\n"
            "• `!tempban <@user> <duration> <reason>` - Temporarily ban a user\n"
            "• `!tempmute <@user> <duration> <reason>` - Temporarily mute a user\n"
            "• `!unmute <@user> [reason]` - Unmute a user"
        ),
        inline=False
    )

    # Tips
    embed.add_field(
        name="💡 Tips",
        value=(
            "• For proposals, you can include options in your description using bullet points\n"
            "• Duration format for temp actions: `1d` (1 day), `2h` (2 hours), `30m` (30 minutes)\n"
            "• If you make a typo in a command, the bot will suggest similar commands\n"
            "• Use `!see_settings governance` or `!see_settings constitutional` for detailed settings information"
        ),
        inline=False
    )

    await ctx.send(embed=embed)

#========================
# 🔹 SERVER GUIDE FUNCTION
#========================

async def create_and_send_server_guide(guild: discord.Guild, bot_instance: commands.Bot):
    """Creates the #server-guide channel (if not exists) and posts/updates the guide embed."""
    guide_channel_name = CHANNELS.get("guide")
    if not guide_channel_name:
        print(f"ERROR: 'guide' channel name not found in CHANNELS dictionary for guild {guild.name}")
        return

    # Get or create the channel. utils.get_or_create_channel should handle permissions
    # (read-only for @everyone, send for bot).
    # It passes bot_instance.user.id to assist in setting bot's permissions if needed by the utility.
    channel = await utils.get_or_create_channel(guild, guide_channel_name, bot_instance.user.id)

    if not channel:
        print(f"ERROR: Could not get or create channel '{guide_channel_name}' in guild {guild.name}")
        return

    try:
        # Purge existing messages in the channel to ensure only the guide is present.
        # This is important if the guide content is updated and needs to be resent.
        # We only purge messages from the bot itself to be safe, though for a dedicated guide channel,
        # purging all messages might also be acceptable.
        print(f"Attempting to purge messages from bot in #{guide_channel_name} in {guild.name}...")
        await channel.purge(limit=None, check=lambda m: m.author == bot_instance.user)
        print(f"Messages purged from #{guide_channel_name} in {guild.name}.")
    except discord.Forbidden:
        print(f"WARNING: Bot lacks 'Manage Messages' permission in #{guide_channel_name} for guild {guild.name}. Cannot purge old guide.")
    except discord.HTTPException as e:
        print(f"ERROR: Failed to purge messages in #{guide_channel_name} for guild {guild.name}: {e}")


    embed = discord.Embed(
        title="🌟 Welcome to the Server Guide! 🌟",
        description="This guide provides an overview of our server, its channels, voting systems, and how to use our governance bot. Please read it to understand how things work around here!",
        color=discord.Color.blurple() # A nice Discord-y color
    )

    # Section 1: Server Channels
    embed.add_field(
        name="🗺️ Server Channels at a Glance",
        value=(
            f"• `#{CHANNELS.get('rules', 'rules-and-agreement')}`: Read and agree to server rules for full access.\n"
            f"• `#{CHANNELS.get('announcements', 'announcements')}`: Important server-wide news.\n"
            f"• `#{CHANNELS.get('proposals', 'proposals')}`: Submit and discuss governance proposals.\n"
            f"• `#{CHANNELS.get('voting', 'voting-room')}`: Active proposals are voted on here (often via DM).\n"
            f"• `#{CHANNELS.get('results', 'governance-results')}`: Official outcomes of completed proposals.\n"
            f"• `#{CHANNELS.get('logs', 'governance-logs')}` (`#audit-log`): Log of governance actions and bot operations.\n"
            f"• `#{CHANNELS.get('general', 'general')}`: General chat, discussions, and questions.\n"
            f"• `#{guide_channel_name}`: You are here! This comprehensive guide.\n"
            f"• `#{utils.CHANNELS.get('campaign_management', 'campaign-management')}`: Manage multi-scenario weighted campaigns."

        ),
        inline=False
    )

    # Section 2: Voting Protocols Intro
    embed.add_field(
        name="🗳️ Understanding Voting Protocols",
        value="Our server supports various voting protocols for fair and flexible decision-making. Each counts votes and determines outcomes differently. Understanding them helps you participate effectively!",
        inline=False
    )

    # Section 3: Plurality Voting
    embed.add_field(
        name="1️⃣ Plurality Voting (First Past the Post)",
        value=(
            "**How it works:** Voters choose one option. The option with the most votes wins.\n"
            "**Analogy:** A simple school election – most votes wins, even without a majority.\n"
            "**Pros:** Simple to understand and use.\n"
            "**Cons:** Winner might lack majority support; susceptible to 'vote splitting'."
        ),
        inline=True
    )

    # Section 4: Borda Count
    embed.add_field(
        name="🔢 Borda Count (Ranked Preference)",
        value=(
            "**How it works:** Voters rank options. Points are awarded based on rank. Most total points wins.\n"
            "**Analogy:** A sports league – points for finishing position, most points wins the league.\n"
            "**Pros:** Considers all preferences, promotes consensus candidates.\n"
            "**Cons:** More complex; tactical voting possible."
        ),
        inline=True
    )

    # Section 5: Approval Voting
    embed.add_field(
        name="✅ Approval Voting (Approve Multiple)",
        value=(
            "**How it works:** Voters approve all acceptable options. Option with most approvals wins.\n"
            "**Analogy:** Choosing ice cream flavors – pick all you like. Most chosen wins.\n"
            "**Pros:** Simple, allows support for multiple good options, reduces 'spoiler effect'.\n"
            "**Cons:** Doesn't capture preference strength between approved options."
        ),
        inline=True
    )

    # Section 6: Runoff Voting (IRV/RCV)
    embed.add_field(
        name="🏆 Runoff Voting (Instant Runoff / Ranked Choice)",
        value=(
            "**How it works:** Voters rank options. If no majority, lowest option eliminated, votes redistributed. Repeats until one option has a majority.\n"
            "**Analogy:** Mini-elections – if your first choice is out, your vote goes to your next.\n"
            "**Pros:** Ensures winner has majority support (among remaining), reduces wasted votes.\n"
            "**Cons:** Complex; can eliminate 'compromise' candidates early."
        ),
        inline=True
    )

    # Section 7: Condorcet Method
    embed.add_field(
        name="🤝 Condorcet Method (Pairwise Majority)",
        value=(
            "**How it works:** Every option is matched head-to-head against each other. If one option beats all others in these pairwise contests, it wins.\n"
            "**Analogy:** A round-robin tournament where the champion defeats every other team.\n"
            "**Pros:** Honors majority preference in each matchup; selects broadly acceptable winners.\n"
            "**Cons:** Can produce cycles with no clear winner; computation and explanation are more complex."
        ),
        inline=True
    )

    # Section 8: Copeland Method

    embed.add_field(
        name="⚔️ Copeland Method (Pairwise Champion)",
        value=(
            "**How it works:** Options compared head-to-head. Option winning most pairwise contests wins.\n"
            "**Analogy:** Round-robin tournament – team with most wins is champion.\n"
            "**Pros:** Often elects candidate preferred by majority over others; intuitive.\n"
            "**Cons:** Can result in ties/cycles; doesn't show preference strength beyond winning."
        ),
        inline=True
    )

    # Make sure the next fields are not inline to take full width
    # Add an empty field if the number of inline fields is odd, to prevent the next non-inline field from appearing next to the last inline one.
    # Current inline fields = 6 (Plurality, Borda, Approval, Runoff, Condorcet, Copeland) - which is even. So no empty field needed.


    # Section 8: How to Use Bot Commands
    embed.add_field(
        name="🤖 Using Bot Commands",
        value=(
            "Our bot has many commands for server governance!\n"
            "• **Proposals:** `!propose` (create), `!proposals` (list), `!proposal <id>` (view).\n"
            "• **Voting:** Usually via DMs. `!vote <id> ...` can also be used.\n"
            "• **Settings:** `!see_settings`, `!constitution`. Admins: `!set_setting`, `!set_constitutional_var`.\n"
            "• **Moderation:** `!warn`, `!tempban`, etc. (permissions may apply).\n"
            "For a full list and detailed usage, type `!help_guide`."
        ),
        inline=False
    )

    # Section 9: Getting Help
    embed.add_field(
        name="❓ Getting Help",
        value="If you have questions about the server, governance, or the bot, please ask in `#general`. Server administrators are also available to assist you.",
        inline=False
    )

    embed.set_footer(
        text=f"{bot_instance.user.name} Server Guide | Last Updated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
    )

    try:
        await channel.send(embed=embed)
        print(f"✅ Server guide sent to #{guide_channel_name} in {guild.name}")
    except discord.Forbidden:
        print(f"ERROR: Bot lacks 'Send Messages' or 'Embed Links' permission in #{guide_channel_name} for guild {guild.name}")
    except discord.HTTPException as e:
        print(f"ERROR: Failed to send server guide to #{guide_channel_name} for guild {guild.name}: {e}")


async def check_proposal_deadlines(bot):
    """Check for proposals with expired deadlines and close them"""
    while True:
        try:
            # Use imported function from voting_utils to avoid circular imports
            from voting_utils import check_expired_proposals, close_and_announce_results
            closed_proposals = await check_expired_proposals()

            # Announce results for each closed proposal
            for proposal, results in closed_proposals:
                guild = bot.get_guild(proposal['server_id'])
                if guild:
                    await close_and_announce_results(guild, proposal, results)

            # Check for proposals with pending result announcements (from 100% voting)
            await announce_pending_results(bot)

            # Check for expired moderation actions
            expired_bans = await db.get_expired_moderations("ban")
            for ban in expired_bans:
                guild = bot.get_guild(ban['server_id'])
                if guild:
                    try:
                        # Unban the user
                        user = await bot.fetch_user(ban['user_id'])
                        await guild.unban(user, reason="Temporary ban expired")

                        # Log to audit channel
                        audit_channel = discord.utils.get(guild.text_channels, name="audit-log")
                        if audit_channel:
                            await audit_channel.send(f"🔓 **Auto-Unban**: <@{ban['user_id']}> has been unbanned (temporary ban expired)")
                    except Exception as e:
                        print(f"Error unbanning user {ban['user_id']}: {e}")

                    # Remove from database
                    await db.remove_temp_moderation(ban['action_id'])

            # Check for expired mutes
            expired_mutes = await db.get_expired_moderations("mute")
            for mute in expired_mutes:
                guild = bot.get_guild(mute['server_id'])
                if guild:
                    try:
                        # Get mute role
                        const_vars = await db.get_constitutional_variables(guild.id)
                        mute_role_name = const_vars.get("mute_role", {"value": "Muted"})["value"]
                        mute_role = discord.utils.get(guild.roles, name=mute_role_name)

                        if mute_role:
                            # Unmute the user
                            member = guild.get_member(mute['user_id'])
                            if member and mute_role in member.roles:
                                await member.remove_roles(mute_role, reason="Temporary mute expired")

                                # Log to audit channel
                                audit_channel = discord.utils.get(guild.text_channels, name="audit-log")
                                if audit_channel:
                                    await audit_channel.send(f"🔊 **Auto-Unmute**: {member.mention} has been unmuted (temporary mute expired)")
                    except Exception as e:
                        print(f"Error unmuting user {mute['user_id']}: {e}")

                    # Remove from database
                    await db.remove_temp_moderation(mute['action_id'])
        except Exception as e:
            print(f"Error in scheduled task: {e}")
            import traceback
            traceback.print_exc()

        # Wait for 5 minutes before checking again
        await asyncio.sleep(20)  # 5 minutes


async def announce_pending_results(bot):
    """Announce results for proposals with pending announcements"""
    try:
        # Get proposals with pending announcements
        pending_announcements = await db.get_proposals_with_pending_announcements()
        print(f"DEBUG: Found {len(pending_announcements)} proposals with pending result announcements")

        for proposal in pending_announcements:
            guild = bot.get_guild(proposal['server_id'])
            if guild:
                try:
                    # Get the results
                    results = await db.get_proposal_results(proposal['proposal_id'])
                    if results:
                        # Announce the results
                        print(f"DEBUG: Announcing results for proposal {proposal['proposal_id']} with pending announcement")
                        from voting_utils import close_and_announce_results
                        await close_and_announce_results(guild, proposal, results)

                        # Clear the pending announcement flag
                        await db.update_proposal(proposal['proposal_id'], {
                            'results_pending_announcement': False
                        })
                        print(f"DEBUG: Successfully announced results for proposal {proposal['proposal_id']}")
                    else:
                        print(f"DEBUG: No results found for proposal {proposal['proposal_id']}")
                except Exception as e:
                    print(f"Error announcing results for proposal {proposal['proposal_id']}: {e}")
                    import traceback
                    traceback.print_exc()
            else:
                print(f"DEBUG: Could not find guild for proposal {proposal['proposal_id']} (server_id: {proposal['server_id']})")
    except Exception as e:
        print(f"Error in announce_pending_results: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    bot_token = open("bot_token.txt", "r").readline().strip()
    bot.run(bot_token)
