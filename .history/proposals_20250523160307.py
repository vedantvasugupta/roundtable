import discord
from discord.ext import commands
import asyncio
from datetime import datetime, timedelta
import json
import db
import voting
import re
from typing import List, Optional, Dict, Any, Union
import traceback
import utils
import voting_utils
# Enable all intents (or specify only the necessary ones)
intents = discord.Intents.default()
intents.message_content = True  # Required for handling messages

# Initialize bot with intents
bot = commands.Bot(command_prefix="!", intents=intents)

# ========================
# 🔹 INTERACTIVE PROPOSAL CREATION


class ProposalMechanismSelectionView(discord.ui.View):
    """View with buttons to select a voting mechanism for a new proposal."""
    def __init__(self, original_interaction: Optional[discord.Interaction], invoker_id: int):
        super().__init__(timeout=300)  # 5 minutes timeout
        self.original_interaction = original_interaction
        self.invoker_id = invoker_id

        mechanisms = [
            ("Plurality", "plurality", "🗳️"),
            ("Borda Count", "borda", "📊"),
            ("Approval Voting", "approval", "👍"),
            ("Runoff Voting", "runoff", "🔄"),
            ("D'Hondt Method", "dhondt", " proporcional"),  # Placeholder emoji
        ]

        for i, (label, custom_id_suffix, emoji) in enumerate(mechanisms):
            button = discord.ui.Button(
                label=label,
                style=discord.ButtonStyle.secondary,
                custom_id=f"select_mechanism_{custom_id_suffix}",
                emoji=emoji,
                row=i // 2  # Max 2 buttons per row
            )
            button.callback = self.mechanism_button_callback
            self.add_item(button)

    async def mechanism_button_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.invoker_id:
            await interaction.response.send_message("This interaction is not for you.", ephemeral=True)
            return

        custom_id = interaction.data["custom_id"]
        mechanism_name = custom_id.replace("select_mechanism_", "")

        modal: Optional[discord.ui.Modal] = None
        if mechanism_name == "plurality":
            modal = PluralityProposalModal(interaction, mechanism_name)
        elif mechanism_name == "borda":
            modal = BordaProposalModal(interaction, mechanism_name)
        elif mechanism_name == "approval":
            modal = ApprovalProposalModal(interaction, mechanism_name)
        elif mechanism_name == "runoff":
            modal = RunoffProposalModal(interaction, mechanism_name)
        elif mechanism_name == "dhondt":
            modal = DHondtProposalModal(interaction, mechanism_name)
        # Add other mechanisms here

        if modal:
            await interaction.response.send_modal(modal)
            self.stop()  # Stop this view once a modal is sent
            # Optionally edit the original message to remove buttons or indicate a modal was sent
            try:
                await self.original_interaction.edit_original_response(content="Proposal creation form sent. Please fill it out.", view=None)
            except discord.HTTPException:
                pass  # Original interaction might have already been responded to or timed out
        else:
            await interaction.response.send_message(f"Modal for {mechanism_name} not implemented yet.", ephemeral=True)

    async def on_timeout(self):
        if self.original_interaction and not self.original_interaction.is_expired():
            try:
                # Try to edit the original message to indicate timeout
                # This only works if original_interaction was provided and is still valid.
                await self.original_interaction.edit_original_response(content="Proposal mechanism selection timed out. Please run the command again.", view=None)
            except discord.NotFound:
                print("INFO: Original interaction message not found on timeout, likely already deleted or handled.")
            except discord.HTTPException as e:
                print(f"ERROR: Failed to edit original interaction on timeout: {e}")
        else:
            # If no original_interaction or it's expired, we can't edit the message.
            # We might try to send a new message if we had the channel context, but the View itself doesn't store it.
            # For now, just log it.
            print(f"INFO: ProposalMechanismSelectionView timed out for invoker {self.invoker_id}. No original interaction to edit.")
        self.stop()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.invoker_id:
            await interaction.response.send_message("You cannot interact with this button.", ephemeral=True)
            return False
        return True

class BaseProposalModal(discord.ui.Modal):
    """Base modal for creating a proposal with common fields."""
    def __init__(self, interaction: discord.Interaction, mechanism_name: str, title: str):
        super().__init__(title=title)
        self.original_interaction = interaction  # The interaction from the button click
        self.mechanism_name = mechanism_name

        self.proposal_title_input = discord.ui.TextInput(
            label="Proposal Title",
            placeholder="Enter a concise title for your proposal",
            min_length=5,
            max_length=100,
            required=True,
            style=discord.TextStyle.short
        )
        self.add_item(self.proposal_title_input)

        self.description_input = discord.ui.TextInput(
            label="Proposal Description",
            placeholder="Describe your proposal in detail.",
            min_length=10,
            max_length=4000,  # Max length for modal text input
            required=True,
            style=discord.TextStyle.paragraph
        )
        self.add_item(self.description_input)

        self.options_input = discord.ui.TextInput(
            label="Options (One Per Line)",
            placeholder="Enter each voting option on a new line.\nDefault: Yes, No (if left blank)",
            required=False,
            style=discord.TextStyle.paragraph
        )
        self.add_item(self.options_input)

        self.deadline_input = discord.ui.TextInput(
            label="Voting Duration (e.g., 7d, 24h, 30m)",
            placeholder="Default: 7d (7 days)",
            required=False,
            style=discord.TextStyle.short,
            default_value="7d"
        )
        self.add_item(self.deadline_input)

    async def common_on_submit(self, interaction: discord.Interaction, specific_hyperparameters: Dict[str, Any]):
        """Common logic for modal submission, to be called by subclasses."""
        try:
            title = self.proposal_title_input.value
            description = self.description_input.value
            options_text = self.options_input.value
            deadline_str = self.deadline_input.value or "7d"

            options = [opt.strip() for opt in options_text.split('\n') if opt.strip()] if options_text else ["Yes", "No"]
            deadline_seconds = utils.parse_duration(deadline_str)
            deadline_days = deadline_seconds / 86400

            # Defer the interaction response from the modal submission itself
            await interaction.response.defer(ephemeral=True, thinking=True)

            proposal_id = await create_proposal(
                interaction,  # Pass the modal's interaction object
                title,
                description,
                self.mechanism_name,
                options,
                deadline_days,
                hyperparameters=specific_hyperparameters
            )
            # create_proposal now handles sending followup messages.

        except Exception as e:
            print(f"Error in BaseProposalModal on_submit: {e}")
            traceback.print_exc()
            if not interaction.response.is_done():
                await interaction.response.send_message(f"An unexpected error occurred: {e}", ephemeral=True)
            else:
                await interaction.followup.send(f"An unexpected error occurred: {e}", ephemeral=True)

class PluralityProposalModal(BaseProposalModal):
    def __init__(self, interaction: discord.Interaction, mechanism_name: str):
        super().__init__(interaction, mechanism_name, title=f"New Plurality Proposal")
        self.allow_abstain_input = discord.ui.TextInput(
            label="Allow Abstain Votes? (yes/no)",
            default="yes",
            required=False
        )
        self.add_item(self.allow_abstain_input)

        self.winning_threshold_input = discord.ui.TextInput(
            label="Winning Threshold (e.g., 50% or 10 votes)",
            placeholder="Optional. e.g., 50% or 10",
            required=False
        )
        self.add_item(self.winning_threshold_input)

    async def on_submit(self, interaction: discord.Interaction):
        hyperparameters = {
            "allow_abstain": self.allow_abstain_input.value.lower() == 'yes',
            "winning_threshold": self.winning_threshold_input.value or None
        }
        await self.common_on_submit(interaction, hyperparameters)

class BordaProposalModal(BaseProposalModal):
    def __init__(self, interaction: discord.Interaction, mechanism_name: str):
        super().__init__(interaction, mechanism_name, title=f"New Borda Count Proposal")
        self.allow_abstain_input = discord.ui.TextInput(
            label="Allow Abstain Votes? (yes/no)",
            default="yes",
            required=False
        )
        self.add_item(self.allow_abstain_input)
    async def on_submit(self, interaction: discord.Interaction):
        hyperparameters = {
            "allow_abstain": self.allow_abstain_input.value.lower() == 'yes'
        }
        await self.common_on_submit(interaction, hyperparameters)

class ApprovalProposalModal(BaseProposalModal):
    def __init__(self, interaction: discord.Interaction, mechanism_name: str):
        super().__init__(interaction, mechanism_name, title=f"New Approval Voting Proposal")
        self.allow_abstain_input = discord.ui.TextInput(
            label="Allow Abstain Votes? (yes/no)",
            default="yes",
            required=False
        )
        self.add_item(self.allow_abstain_input)
    async def on_submit(self, interaction: discord.Interaction):
        hyperparameters = {
            "allow_abstain": self.allow_abstain_input.value.lower() == 'yes'
        }
        await self.common_on_submit(interaction, hyperparameters)

class RunoffProposalModal(BaseProposalModal):
    def __init__(self, interaction: discord.Interaction, mechanism_name: str):
        super().__init__(interaction, mechanism_name, title=f"New Runoff Voting Proposal")
        self.allow_abstain_input = discord.ui.TextInput(
            label="Allow Abstain Votes? (yes/no)",
            default="yes",
            required=False
        )
        self.add_item(self.allow_abstain_input)
    async def on_submit(self, interaction: discord.Interaction):
        hyperparameters = {
            "allow_abstain": self.allow_abstain_input.value.lower() == 'yes'
        }
        await self.common_on_submit(interaction, hyperparameters)

class DHondtProposalModal(BaseProposalModal):
    def __init__(self, interaction: discord.Interaction, mechanism_name: str):
        super().__init__(interaction, mechanism_name, title=f"New D'Hondt Method Proposal")
        self.allow_abstain_input = discord.ui.TextInput(
            label="Allow Abstain Votes? (yes/no)",
            default="yes",
            required=False
        )
        self.add_item(self.allow_abstain_input)
        self.num_seats_input = discord.ui.TextInput(
            label="Number of 'Seats' to Allocate (Winners)",
            default="1",
            required=False
        )
        self.add_item(self.num_seats_input)
    async def on_submit(self, interaction: discord.Interaction):
        num_seats_val = self.num_seats_input.value
        try:
            num_seats = int(num_seats_val) if num_seats_val else 1
        except ValueError:
            await interaction.response.send_message("Invalid input for 'Number of Seats'. Must be a number.", ephemeral=True)
            return
        hyperparameters = {
            "allow_abstain": self.allow_abstain_input.value.lower() == 'yes',
            "num_seats": num_seats
        }
        await self.common_on_submit(interaction, hyperparameters)

# ========================
# 🔹 OLD INTERACTIVE PROPOSAL CREATION (To be removed or refactored)
# ========================

class ProposalModal(discord.ui.Modal):  # This is the OLD modal, will be replaced.
    """Modal form for creating a proposal"""

    def __init__(self, interaction: discord.Interaction):  # Accept interaction directly
        super().__init__(title="Create a Proposal")
        self.interaction = interaction  # Store the interaction
        # self.ctx = ctx # Removed ctx, use interaction instead
        self.voting_mechanism = "plurality"  # Default value
        self.deadline_value = "7d"  # Default value

        # Add form fields
        self.title_input = discord.ui.TextInput(
            label="Title",
            placeholder="Enter a title for your proposal",
            min_length=1,
            max_length=100,
            required=True,
            style=discord.TextStyle.short
        )
        self.add_item(self.title_input)

        self.description_input = discord.ui.TextInput(
            label="Description",
            placeholder="Describe your proposal in detail. Optional.",
            min_length=0,
            max_length=4000,
            required=False,
            style=discord.TextStyle.paragraph
        )
        self.add_item(self.description_input)

        self.options_input = discord.ui.TextInput(
            label="Options (one per line)",
            placeholder="Enter each voting option on a new line.\nLeave blank for Yes/No options.",
            required=False,
            style=discord.TextStyle.paragraph
        )
        self.add_item(self.options_input)

        self.deadline_input = discord.ui.TextInput(
            label="Deadline",
            placeholder="Enter deadline (e.g., 1d, 12h, 3d, 1w). Default: 7d",
            required=False,
            style=discord.TextStyle.short
        )
        self.add_item(self.deadline_input)

        self.voting_mechanism_input = discord.ui.TextInput(
            label="Voting Mechanism",
            placeholder="Enter voting mechanism (plurality, borda, approval, runoff, dhondt)",
            required=False,
            style=discord.TextStyle.short
        )
        self.add_item(self.voting_mechanism_input)
    # >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>
    # THIS METHOD MUST BE INSIDE THE ProposalModal CLASS
    # >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>
    async def on_submit(self, interaction: discord.Interaction):
        """Handle form submission"""
        try:
            print(f"🔍 {interaction.user} submitted a proposal form")

            title = self.title_input.value
            description = self.description_input.value or "No description provided."
            options_text = self.options_input.value
            voting_mechanism = self.voting_mechanism_input.value.strip().lower()

            # Validate voting mechanism
            valid_mechanisms = ["plurality", "borda",
                                "approval", "runoff", "dhondt"]
            if voting_mechanism not in valid_mechanisms:
                # Use interaction.response.send_message for the *initial* response to the modal
                # This is the correct way to respond immediately to a modal submission.
                await interaction.response.send_message(
                    f"❌ Invalid voting mechanism: `{voting_mechanism}`. Choose from {valid_mechanisms}.",
                    ephemeral=True
                )
                return  # Stop execution if validation fails

            options = [opt.strip() for opt in options_text.split(
                "\n") if opt.strip()] if options_text else ["Yes", "No"]
            deadline_value = self.deadline_input.value or self.deadline_value
            deadline_seconds = utils.parse_duration(
                deadline_value)  # Use utils.parse_duration
            deadline_days = deadline_seconds / 86400

            # Defer the response *after* initial validation if the following steps might take time.
            # This prevents the "Interaction failed" timeout.
            # The modal *should* close when response.defer() is successfully called.
            # Subsequent messages must use interaction.followup.send()
            # If initial validation fails and you send an immediate response, you don't defer.
            await interaction.response.defer(ephemeral=True)
            print(
                f"✅ Deferred response for modal submission from {interaction.user}")

            print(f"📢 Calling create_proposal for user: {interaction.user}")

            # ✅ Call create_proposal, which returns the proposal_id
            # Initialize proposal_id to None before the call
            proposal_id = None
            try:
                # Pass the original interaction object to create_proposal
                proposal_id = await create_proposal(
                    interaction,  # Pass interaction object
                    title, description, voting_mechanism, options, deadline_days
                )

                # create_proposal already handles sending success/failure messages via followup
                # So, no need to send a message here based on proposal_id
                # The messages sent by create_proposal (either success or failure) are the final followups.

            except Exception as e:
                # Handle exceptions during the create_proposal call itself
                print(
                    f"❌ Exception during create_proposal for user {interaction.user}: {e}")
                traceback.print_exc()
                # Use followup to send message after deferring
                await interaction.followup.send(f"❌ An error occurred while creating the proposal: `{str(e)}`. Please check the bot logs.", ephemeral=True)

        except Exception as e:
            # This outer catch handles errors *within* the on_submit method itself,
            # before or after the create_proposal call, but before cleanup.
            print(f"❌ Unexpected error in ProposalModal on_submit: {e}")
            traceback.print_exc()

            # Attempt to send an error message using followup if defer succeeded,
            # or using response if defer failed or didn't happen.
            try:
                # Check if response was already sent (e.g., by initial validation or defer)
                if not interaction.response.is_done():
                    # If response is not done, send an immediate error response
                    await interaction.response.send_message(f"❌ An unexpected error occurred: `{str(e)}`. Please check the logs.", ephemeral=True)
                else:
                    # If response is done (e.g., deferred), use followup
                    await interaction.followup.send(f"❌ An unexpected error occurred: `{str(e)}`. Please check the logs.", ephemeral=True)
            except Exception as followup_e:
                print(
                    f"⚠️ Could not send final error message after unexpected error: {followup_e}")


async def notify_admins_of_pending_proposals(guild):
    """Send notification to admins about pending proposals"""
    # Get all pending proposals
    pending_proposals = await db.get_server_proposals(guild.id, "Pending")
    if not pending_proposals:
        return

    # Get admin role
    admin_role = discord.utils.get(guild.roles, name="Admin")
    if not admin_role:
        return

    # Get proposals channel
    proposals_channel = await utils.get_or_create_channel(guild, "proposals")
    if not proposals_channel:
        return

    # Create notification embed
    embed = discord.Embed(
        title="🔔 Pending Proposals Require Approval",
        description=f"There are {len(pending_proposals)} proposals waiting for admin approval.",
        color=discord.Color.orange()
    )

    # Add each proposal
    for proposal in pending_proposals[:5]:  # Limit to 5 to avoid embed limits
        embed.add_field(
            name=f"#{proposal['proposal_id']}: {proposal['title']}",
            value=f"Proposed by <@{proposal['proposer_id']}>",
            inline=False
        )

    # Send notification
    await proposals_channel.send(
        content=f"{admin_role.mention} Please review pending proposals",
        embed=embed
    )


class EarlyTerminationView(discord.ui.View):
    """View for early termination of proposals"""
    def __init__(self, proposal_id):
        super().__init__(timeout=None)  # No timeout for persistent views
        self.proposal_id = proposal_id

    @discord.ui.button(label="🛑 Terminate Early", style=discord.ButtonStyle.danger)
    async def terminate_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Check if the user has permission
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("You don't have permission to terminate proposals early!", ephemeral=True)
            return

        # Confirm termination
        await interaction.response.send_message(
            "Are you sure you want to terminate this proposal early and count the votes?",
            view=ConfirmTerminationView(self.proposal_id),
            ephemeral=True
        )

class ConfirmTerminationView(discord.ui.View):
    """View for confirming early termination"""
    def __init__(self, proposal_id):
        super().__init__(timeout=60)
        self.proposal_id = proposal_id

    @discord.ui.button(label="✅ Yes, terminate now", style=discord.ButtonStyle.danger)
    async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Disable buttons
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)

        # Process early termination
        await terminate_proposal_early(interaction, self.proposal_id)

    @discord.ui.button(label="❌ No, keep voting", style=discord.ButtonStyle.secondary)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Just close the confirmation dialog
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content="Early termination cancelled.", view=self)

async def terminate_proposal_early(interaction, proposal_id):
    """Terminate a proposal early and count the votes"""
    # Get the proposal
    proposal = await db.get_proposal(proposal_id)
    if not proposal:
        await interaction.followup.send(f"❌ Proposal #{proposal_id} not found.")
        return

    if proposal['status'] != "Voting":
        await interaction.followup.send(f"❌ Proposal #{proposal_id} is not in voting status.")
        return

    # Import voting module here to avoid circular imports
    from voting_utils import close_proposal

    # Close the proposal
    results = await close_proposal(proposal_id)

    # Announce the results
    await voting_utils.close_and_announce_results(interaction.guild, proposal, results)

    # Send confirmation message
    await interaction.followup.send(f"✅ Proposal #{proposal_id} has been terminated early and the votes have been counted.")

    # Log to audit channel
    audit_channel = discord.utils.get(interaction.guild.text_channels, name="audit-log")
    if audit_channel:
        await audit_channel.send(f"🛑 **Proposal Terminated Early**: #{proposal_id} '{proposal['title']}' has been terminated early by {interaction.user.mention}")

async def open_proposal_form(ctx):
    """Open the proposal creation form"""
    # Create and send the modal
    modal = ProposalModal(ctx)
    await ctx.send_modal(modal)


async def notify_admins_of_pending_proposals(guild):
    """Send notification to admins about pending proposals"""
    # ... (keep as is) ...

# Keep EarlyTerminationView and ConfirmTerminationView if they are here
# ... (EarlyTerminationView, ConfirmTerminationView) ...

# --- Updated ProposalView class ---


class ProposalView(discord.ui.View):
    """View containing the button to create a proposal, limited to the command invoker."""

    def __init__(self, ctx: commands.Context):  # Type hint for context
        super().__init__(timeout=600)  # Set a reasonable timeout
        self.ctx = ctx
        self.invoker_id = ctx.author.id  # Store the ID of the command invoker

    @discord.ui.button(label="Create Proposal", style=discord.ButtonStyle.primary, emoji="📝")
    async def create_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # interaction_check in BaseView (if applied to this) or this view
        # will prevent others from getting here.
        # If interaction_check is NOT applied here, you'd need this:
        # if interaction.user.id != self.invoker_id:
        #     await interaction.response.send_message("This button is only for the command invoker.", ephemeral=True)
        #     return

        try:
            print(
                f"🔍 {interaction.user} clicked the Create Proposal button (invoker: {self.ctx.author})")
            # Send the modal - Pass the interaction object to the modal constructor
            modal = ProposalModal(interaction)
            await interaction.response.send_modal(modal)
            print(f"✅ ProposalModal sent to {interaction.user}")

            # Stop the view after the modal is sent, as it's no longer needed
            # This prevents the button from being clickable again.
            # If you want the button to potentially be clicked again, remove this.
            self.stop()

        except Exception as e:
            print(f"❌ Error in ProposalView create_button: {e}")
            await interaction.response.send_message(f"❌ An error occurred while opening the proposal form: {e}", ephemeral=True)

    # Add interaction_check to limit who can click the button
    # This check should be on the View itself.
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Checks if the user interacting is the original command invoker."""
        if interaction.user.id != self.invoker_id:
            await interaction.response.send_message("This button is only for the person who used the `!propose` command.", ephemeral=True)
            return False
        return True

    async def on_timeout(self) -> None:
        """Disable the button when the view times out."""
        for child in self.children:
            child.disabled = True
        # If you stored the original message the view was attached to:
        # try:
        #     await self.message.edit(view=self)
        # except: pass # Ignore errors if message deleted etc.


# --- create_proposal function ---
# This function needs to be updated to handle either a discord.Interaction or a commands.Context
# as its first argument, and use the appropriate method for sending followups/messages.
# The original `create_proposal` in proposals.py already has this logic.

# Let's ensure the `create_proposal` function uses `interaction.followup.send` or `ctx.send` correctly
# based on the type of the first argument.

async def create_proposal(ctx_or_interaction: Union[commands.Context, discord.Interaction], title, description, voting_mechanism, options, deadline_days, hyperparameters: Optional[Dict[str, Any]] = None):
    """Create a new proposal and store it in the database"""
    # Determine server_id and proposer_id correctly based on input type
    if isinstance(ctx_or_interaction, discord.Interaction):
        interaction = ctx_or_interaction
        ctx = None
        guild = interaction.guild
        server_id = guild.id
        proposer_obj = interaction.user  # Store the object
        proposer_id = proposer_obj.id
        send_message_func = interaction.followup.send
        is_interaction = True
    elif isinstance(ctx_or_interaction, commands.Context):
        ctx = ctx_or_interaction
        interaction = None
        guild = ctx.guild
        server_id = guild.id
        proposer_obj = ctx.author  # Store the object
        proposer_id = proposer_obj.id
        send_message_func = ctx.send
        is_interaction = False
    else:
        print("ERROR: Invalid context/interaction object passed to create_proposal")
        return None  # Or raise an error

    # Calculate deadline
    deadline = datetime.now() + timedelta(days=deadline_days)

    # Get constitutional variables
    const_vars = await db.get_constitutional_variables(server_id)

    # Check if user has permission to create proposals
    eligible_proposers_role = const_vars.get(
        "eligible_proposers_role", {"value": "everyone"})["value"]

    # Check if proposal requires approval
    requires_approval = const_vars.get("proposal_requires_approval", {
                                       "value": "true"})["value"].lower() == "true"

    # Send explanation if user might be confused about approval requirement
    if requires_approval and eligible_proposers_role.lower() == "everyone":
        # Handle both Interaction and Context objects for sending messages
        if is_interaction:
            await interaction.followup.send(  # Use interaction if available for ephemeral
                "ℹ️ Note: While anyone can create proposals, they still require admin approval before voting starts.",
                ephemeral=True
            )
        elif ctx:  # Use ctx if not interaction
            await ctx.send("ℹ️ Note: While anyone can create proposals, they still require admin approval before voting starts.")

    if eligible_proposers_role.lower() != "everyone":
        # Check if user has the required role
        # Handle both Interaction and Context objects
        member = guild.get_member(proposer_id)  # Get the member object
        if member and eligible_proposers_role.lower() != "everyone":  # Double check this condition
            role = discord.utils.get(guild.roles, name=eligible_proposers_role)
            if role and role not in member.roles:
                error_msg = f"❌ You need the `{eligible_proposers_role}` role to create proposals."
                if is_interaction:
                     # Use interaction if available
                     await interaction.followup.send(error_msg, ephemeral=True)
                elif ctx:
                    await ctx.send(error_msg)  # Use ctx if not interaction
                return None

    # Validate voting mechanism
    mechanism = voting_utils.get_voting_mechanism(voting_mechanism)
    if not mechanism:
        error_msg = f"❌ Invalid voting mechanism: `{voting_mechanism}`. Valid options: plurality, borda, approval, runoff, dhondt"
        if is_interaction:
            # Use interaction if available
            await interaction.followup.send(error_msg, ephemeral=True)
        elif ctx:
            await ctx.send(error_msg)  # Use ctx if not interaction
        return None

    # Debug print for options
    print(f"DEBUG: Creating proposal with options: {options}")

    # Insert into database
    # Use the correct deadline calculation (already done above)
    proposal_id = await db.create_proposal(
        server_id, proposer_id, title, description,
        # Pass deadline as ISO string - db.create_proposal expects datetime
        voting_mechanism, deadline, requires_approval,
        hyperparameters # Pass hyperparameters to db layer
    )
    # Debug print
    print(f"DEBUG: Created proposal with proposal_id {proposal_id}")

    # Store options in the database
    # Ensure proposal_id is not None before storing options
    if not proposal_id:
        print("ERROR: Failed to get proposal_id after DB insert.")
        # Use the determined send_message_func to notify user
        await send_message_func("❌ Failed to create the proposal in the database.", ephemeral=is_interaction)
        return None  # Indicate failure

    await db.add_proposal_options(proposal_id, options)
    print(
        f"DEBUG: Stored {len(options)} options for proposal {proposal_id}: {options}")

    # Get or create proposals channel (Now in utils)
    # Need bot user ID for utils.get_or_create_channel permission setup
    bot_user_id = guild.me.id if guild.me else None
    proposals_channel = await utils.get_or_create_channel(guild, "proposals", bot_user_id)

    if not proposals_channel:
        print(
            f"ERROR: Proposals channel not found or could not be created in guild {guild.name}.")
        # Use the determined send_message_func to notify user
        await send_message_func("❌ Failed to find or create the proposals channel.", ephemeral=is_interaction)
        return None  # Indicate failure

    # Create embed for proposal (using utils helper)
    embed = utils.create_proposal_embed(  # Call the function from utils
        proposal_id, proposer_obj, title, description,  # Pass the proposer object here
        voting_mechanism, deadline, "Pending" if requires_approval else "Voting",
        options  # Pass options to embed creator
    )

    # Send proposal to proposals channel
    try:
        message = await proposals_channel.send(embed=embed)
    except Exception as e:
        print(
            f"ERROR sending initial proposal message to proposals channel: {e}")
        # Use the determined send_message_func to notify user
        await send_message_func("❌ Failed to send the proposal message to the #proposals channel.", ephemeral=is_interaction)
        return None  # Indicate failure

    # Create thread for discussion
    # Check if message is from a standard TextChannel before creating thread
    if isinstance(message.channel, discord.TextChannel):
        try:
            thread = await message.create_thread(name=f"Proposal #{proposal_id}: {title}")
            # Send initial message in thread
            await thread.send(f"Discussion thread for Proposal #{proposal_id}. Please keep all discussion related to this proposal in this thread.")
        except discord.Forbidden:
            print(
                f"WARNING: Missing permissions to create thread for proposal {proposal_id} in guild {guild.name}")
        except Exception as e:
            print(f"ERROR creating thread for proposal {proposal_id}: {e}")

    # If no approval required, start voting immediately
    if not requires_approval:
        print(
            f"DEBUG: No approval required, starting voting for proposal {proposal_id}")
        # Call from voting_utils
        # start_voting now needs the guild object
        await voting_utils.start_voting(guild, proposal_id, options)
        success_msg = f"✅ Proposal #{proposal_id} created and voting has started! Check <#{proposals_channel.id}> for details."
        # Use the determined send_message_func
        await send_message_func(success_msg, ephemeral=is_interaction)

    else:
        print(
            f"DEBUG: Approval required, posting for review for proposal {proposal_id}")
        # Post for review uses the proposal dict, not the full object
        # Need to get the proposal data again after DB insert
        proposal_data_for_review = await db.get_proposal(proposal_id)

        if proposal_data_for_review:
            # Pass guild and full proposal dict
            await post_proposal_for_review(guild, proposal_data_for_review)
            success_msg = f"✅ Proposal #{proposal_id} created and awaiting admin approval. Check <#{proposals_channel.id}> for details."
            # Use the determined send_message_func
            await send_message_func(success_msg, ephemeral=is_interaction)

            # Notify admins of pending proposals (Now accepts guild)
            try:
                await notify_admins_of_pending_proposals(guild)
            except Exception as e:
                print(f"Error notifying admins: {e}")
        else:
            print(
                f"ERROR: Failed to retrieve proposal {proposal_id} data for review posting.")
            # Use the determined send_message_func to notify user
            await send_message_func("❌ Proposal created, but failed to post it for admin review.", ephemeral=is_interaction)

    return proposal_id  # Return the created proposal ID on success or None on failure


async def start_voting(guild, proposal_id, options=None):
    """Start the voting process for a proposal"""
    # Update proposal status (Assuming this is handled elsewhere, or happens after approval)
    # await db.update_proposal_status(proposal_id, "Voting") # This might be called by approve_proposal_from_interaction *before* calling start_voting

    # Get proposal details
    proposal = await db.get_proposal(proposal_id)
    if not proposal:
        print(f"ERROR: Proposal {proposal_id} not found during start_voting.")
        return False  # Return False on error

    # Double check status here - should be Voting
    if proposal.get('status') != 'Voting':
        print(
            f"WARNING: Attempted to start voting for proposal {proposal_id} which is not in 'Voting' status (current: {proposal.get('status')}). Skipping.")
        return False  # Return False if status is wrong

    # If options not provided, try to get them from the database
    if options is None:
        print(
            f"DEBUG: No options provided for proposal {proposal_id}, retrieving from database")
        # Use proposal['proposal_id'] or proposal['id'] safely
        proposal_db_id = proposal.get('proposal_id') or proposal.get('id')
        options = await db.get_proposal_options(proposal_db_id)
        print(f"DEBUG: Retrieved options from database: {options}")

        # If still no options, try to extract from description (using utils)
        if not options:
            options = utils.extract_options_from_description(
                proposal.get('description', ''))
            print(f"DEBUG: Extracted options from description: {options}")

            # If still no options, use default Yes/No
            if not options:
                options = ["Yes", "No"]
                print(f"DEBUG: Using default Yes/No options")

            # Store the extracted/default options in the database if they weren't already
            if proposal_db_id:
                try:
                     await db.add_proposal_options(proposal_db_id, options)
                     print(
                         f"DEBUG: Stored extracted/default options for proposal {proposal_db_id}")
                except Exception as e:
                     print(
                         f"ERROR storing extracted/default options for proposal {proposal_db_id}: {e}")
            else:
                print(f"WARNING: Could not store options, proposal_id is missing.")

    # Get eligible voters based on server settings (using voting_utils helper)
    # Needs guild object to fetch members
    eligible_voters = await voting_utils.get_eligible_voters(guild, proposal)

    # Create or get voting channel (using utils helper)
    # Needs bot user ID for permission setup
    bot_user_id = guild.me.id if guild.me else None
    voting_channel = await utils.get_or_create_channel(guild, "voting-room", bot_user_id)

    if not voting_channel:
        print(
            f"ERROR: Voting channel not found or could not be created for guild {guild.name}. Cannot start voting for proposal {proposal_id}.")
        return False  # Indicate failure

    # Create embed for voting announcement (using utils helper)
    # Pass the full proposal dict and options
    embed = voting_utils.create_voting_embed(proposal, options)

    # Send voting announcement to voting channel
    try:
        # Use create_vote_post which handles sending the embed and the tracking message
        main_voting_message = await voting_utils.create_vote_post(guild, proposal)  # This function handles saving message ID
        if not main_voting_message:
            print(f"ERROR: Failed to create the main voting post for proposal {proposal_id}.")
             # Still proceed with sending DMs? Or stop? Let's stop if the main post fails.
            return False  # Indicate failure

    except Exception as e:
        print(
            f"ERROR sending initial voting announcement for proposal {proposal_id}: {e}")
        import traceback
        traceback.print_exc()
        return False  # Indicate failure

    # Send DMs to all eligible voters
    dm_sent_count = 0
    dm_failed_count = 0
    already_invited_count = 0  # Track users already invited (useful if retrying start_voting)

    # Get list of users already invited to avoid sending duplicate DMs on restart/retry
    # Using the helper from db
    invited_voter_ids = await db.get_invited_voters_ids(proposal_id) or []  # Default to empty list if None

    print(
        f"DEBUG: Attempting to send DMs to {len(eligible_voters)} eligible voters for proposal {proposal_id}.")
    if invited_voter_ids:
        print(f"DEBUG: {len(invited_voter_ids)} voters already invited according to DB.")

    for member in eligible_voters:
        # Skip if member is the bot itself
        if member.id == (guild.me.id if guild.me else None):
            continue

        # Skip if this voter has already been invited according to the database
        if member.id in invited_voter_ids:
            # print(f"DEBUG: Skipping {member.name} (ID: {member.id}) - already invited.")
            already_invited_count += 1
            continue

        try:
            # Record that this voter is being invited *before* attempting to send DM
            # This prevents re-sending on subsequent runs if the DM sending fails mid-way
            # This helper is in db
            await db.add_voting_invite(proposal_id, member.id)
            print(
                f"DEBUG: Recorded invite for {member.name} (ID: {member.id}) for proposal {proposal_id}")

            print(
                f"DEBUG: Sending DM to {member.name} (ID: {member.id}) for proposal {proposal_id}")
            # Use the helper from voting
            success = await voting.send_voting_dm(member, proposal, options)

            if success:
                dm_sent_count += 1
                print(
                    f"DEBUG: Successfully sent DM to {member.name} (ID: {member.id})")
            else:
                dm_failed_count += 1
                # No need to print specific reason here, send_voting_dm handles its own errors
                # print(f"DEBUG: Failed to send DM to {member.name} (ID: {member.id}) - likely has DMs disabled")
        except Exception as e:
            print(
                f"Error sending DM to {member.name} (ID: {member.id}) for proposal {proposal_id}: {e}")
            import traceback
            traceback.print_exc()
            dm_failed_count += 1

    # Send summary to voting channel
    total_attempted = dm_sent_count + dm_failed_count + already_invited_count
    summary_message = f"📊 **Voting DMs sent** for Proposal #{proposal_id} ({proposal.get('title', 'Untitled')}):\n"
    summary_message += f"Attempted sending to {total_attempted} eligible members.\n"
    summary_message += f"• Successfully sent: {dm_sent_count}\n"
    summary_message += f"• Failed (likely DMs disabled): {dm_failed_count}\n"
    summary_message += f"• Already invited (skipped): {already_invited_count}"

    try:
        await voting_channel.send(summary_message)
    except Exception as e:
        print(f"ERROR sending DM summary message to voting channel for proposal {proposal_id}: {e}")

    print(f"✅ Voting started for proposal #{proposal_id}")
    return True  # Indicate success
async def approve_proposal(ctx, proposal_id):
    """Approve a pending proposal"""
    # Get proposal details
    proposal = await db.get_proposal(proposal_id)
    if not proposal:
        await ctx.send(f"❌ Proposal #{proposal_id} not found.")
        return False

    # Check if proposal is pending
    if proposal['status'] != "Pending":
        await ctx.send(f"❌ Proposal #{proposal_id} is not pending approval. Current status: {proposal['status']}")
        return False

    # Update proposal status
    await db.update_proposal_status(proposal_id, "Voting", ctx.author.id)

    # Get options from database
    options = await db.get_proposal_options(proposal_id)
    print(f"DEBUG: Retrieved options for proposal {proposal_id} from database: {options}")

    # If no options found in database, try to extract from description
    if not options:
        options = extract_options_from_description(proposal['description'])
        print(f"DEBUG: Extracted options from description: {options}")

        # If still no options, use default Yes/No
        if not options:
            options = ["Yes", "No"]
            print(f"DEBUG: Using default Yes/No options")

        # Store the extracted options in the database
        await db.add_proposal_options(proposal_id, options)
        print(f"DEBUG: Stored extracted options in database: {options}")

    # Start voting
    await start_voting(ctx.guild, proposal_id, options)

    await ctx.send(f"✅ Proposal #{proposal_id} approved and voting has started!")
    return True

async def reject_proposal(ctx, proposal_id, reason):
    """Reject a pending proposal"""
    # Get proposal details
    proposal = await db.get_proposal(proposal_id)
    if not proposal:
        await ctx.send(f"❌ Proposal #{proposal_id} not found.")
        return False

    # Check if proposal is pending
    if proposal['status'] != "Pending":
        await ctx.send(f"❌ Proposal #{proposal_id} is not pending approval. Current status: {proposal['status']}")
        return False

    # Update proposal status
    await db.update_proposal_status(proposal_id, "Rejected", ctx.author.id)

    # Get proposals channel
    proposals_channel = discord.utils.get(ctx.guild.text_channels, name="proposals")
    if proposals_channel:
        # Create embed for rejection
        embed = discord.Embed(
            title=f"❌ Proposal #{proposal_id} Rejected",
            description=f"**{proposal['title']}** has been rejected.",
            color=discord.Color.red()
        )

        embed.add_field(name="Reason", value=reason, inline=False)
        embed.add_field(name="Rejected By", value=ctx.author.mention, inline=False)

        await proposals_channel.send(embed=embed)

    await ctx.send(f"✅ Proposal #{proposal_id} has been rejected.")
    return True

# ========================
# 🔹 HELPER FUNCTIONS
# ========================

async def get_or_create_channel(guild, channel_name):
    """Get or create a channel with the given name"""
    channel = discord.utils.get(guild.text_channels, name=channel_name)

    if not channel:
        # Create the channel
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }

        channel = await guild.create_text_channel(name=channel_name, overwrites=overwrites)

        # Send initial message based on channel type
        if channel_name == "proposals":
            await channel.send("📜 **Proposals Channel**\nThis channel is for posting and discussing governance proposals.")
        elif channel_name == "voting-room":
            await channel.send("🗳️ **Voting Room**\nThis channel announces active votes. You will receive DMs to cast your votes.")
        elif channel_name == "governance-results":
            await channel.send("📊 **Governance Results**\nThis channel shows the results of completed votes.")

    return channel

async def get_eligible_voters(guild, proposal):
    """Get all members eligible to vote on a proposal"""
    # Get constitutional variables
    const_vars = await db.get_constitutional_variables(guild.id)
    eligible_voters_role = const_vars.get("eligible_voters_role", {"value": "everyone"})["value"]

    # Verdict Bot ID to exclude
    VERDICT_BOT_ID = 1337818333239574598

    if eligible_voters_role.lower() == "everyone":
        # Everyone can vote except bots and Verdict Bot
        return [member for member in guild.members if not member.bot and member.id != VERDICT_BOT_ID]
    else:
        # Only members with the specified role can vote (excluding bots and Verdict Bot)
        role = discord.utils.get(guild.roles, name=eligible_voters_role)
        if role:
            return [member for member in guild.members if role in member.roles and not member.bot and member.id != VERDICT_BOT_ID]
        else:
            # Role not found, default to everyone except bots and Verdict Bot
            return [member for member in guild.members if not member.bot and member.id != VERDICT_BOT_ID]

def create_proposal_embed(proposal_id, author, title, description, voting_mechanism, deadline, status, options=None):
    """Create an embed for a proposal"""
    # Format deadline
    deadline_str = deadline.strftime("%Y-%m-%d %H:%M UTC") if isinstance(deadline, datetime) else deadline

    # Set color based on status
    if status == "Pending":
        color = discord.Color.orange()
    elif status == "Voting":
        color = discord.Color.blue()
    elif status == "Passed":
        color = discord.Color.green()
    elif status == "Failed" or status == "Rejected":
        color = discord.Color.red()
    else:
        color = discord.Color.light_grey()

    # Create embed
    embed = discord.Embed(
        title=f"Proposal #{proposal_id}: {title}",
        description=description,
        color=color
    )

    # Add metadata
    embed.add_field(name="Status", value=status, inline=True)
    embed.add_field(name="Proposer", value=author.mention if hasattr(author, 'mention') else f"<@{author}>", inline=True)
    embed.add_field(name="Voting Mechanism", value=voting_mechanism.title(), inline=True)
    embed.add_field(name="Deadline", value=deadline_str, inline=True)

    # Add options if provided
    if options:
        options_text = "\n".join([f"• {option}" for option in options])
        embed.add_field(name="Options", value=options_text, inline=False)

    # Add footer with timestamp
    embed.set_footer(text=f"Created at {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}")

    return embed

def extract_options_from_description(description):
    """Extract voting options from proposal description"""
    # Look for a section with options
    options_section = re.search(r'(?:Options|Choices):\s*\n((?:[-•*]\s*[^\n]+\n?)+)', description, re.IGNORECASE)

    if options_section:
        # Extract individual options
        options_text = options_section.group(1)
        options = re.findall(r'[-•*]\s*([^\n]+)', options_text)
        return [option.strip() for option in options if option.strip()]

    # If no options section found, check for Yes/No format
    if re.search(r'\b(?:yes|no|approve|reject|for|against)\b', description, re.IGNORECASE):
        return ["Yes", "No"]

    return None

def parse_duration(duration_str):
    """Parse a duration string (e.g., '1d', '2h', '30m', '1w') into seconds"""
    if not duration_str:
        return 7 * 86400  # Default to 7 days if empty

    total_seconds = 0
    pattern = r'(\d+)([wdhms])'

    for match in re.finditer(pattern, duration_str.lower()):
        value, unit = match.groups()
        value = int(value)

        if unit == 'w':
            total_seconds += value * 604800  # weeks to seconds
        elif unit == 'd':
            total_seconds += value * 86400   # days to seconds
        elif unit == 'h':
            total_seconds += value * 3600    # hours to seconds
        elif unit == 'm':
            total_seconds += value * 60      # minutes to seconds
        elif unit == 's':
            total_seconds += value           # seconds

    # If no valid duration found, default to 7 days
    if total_seconds == 0:
        try:
            # Try to interpret as just a number of days
            days = int(duration_str.strip())
            total_seconds = days * 86400
        except ValueError:
            total_seconds = 7 * 86400  # Default to 7 days

    return total_seconds

# ========================
# 🔹 SCHEDULED TASKS
# ========================

async def check_proposal_deadlines(bot):
    """Check for proposals with expired deadlines and close them"""
    while True:
        try:
            # Use imported function from voting_utils
            closed_proposals = await voting.check_expired_proposals()

            for proposal, results in closed_proposals:
                guild = bot.get_guild(proposal['server_id'])
                if guild:
                    await voting_utils.close_and_announce_results(guild, proposal, results)

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

        # Wait for 5 minutes before checking again
        await asyncio.sleep(300)  # 5 minutes

async def post_proposal_for_review(guild, proposal):
    """Post a proposal to the appropriate channel for admin review"""
    # Get proposals channel
    proposals_channel = discord.utils.get(guild.text_channels, name="proposals")
    if not proposals_channel:
        return

    # Create an embed for the proposal
    embed = discord.Embed(
        title=f"📜 Proposal #{proposal['proposal_id']}: {proposal['title']}",
        description=proposal['description'],
        color=discord.Color.gold()
    )

    # Add metadata
    embed.add_field(name="Status", value=proposal['status'], inline=True)
    embed.add_field(name="Proposer", value=f"<@{proposal['proposer_id']}>", inline=True)
    embed.add_field(name="Voting Mechanism", value=proposal['voting_mechanism'].title(), inline=True)

    # Format deadline
    deadline = datetime.fromisoformat(proposal['deadline'].replace('Z', '+00:00'))
    deadline_str = deadline.strftime("%Y-%m-%d %H:%M UTC")
    embed.add_field(name="Deadline", value=deadline_str, inline=True)

    # Add footer
    embed.set_footer(text=f"Use the buttons below to approve or reject this proposal")

    # Create approval view
    view = ApprovalView(proposal['proposal_id'], proposal['proposer_id'])

    # Send the embed with buttons
    await proposals_channel.send(embed=embed, view=view)

    # Log to audit channel
    audit_channel = discord.utils.get(guild.text_channels, name="audit-log")
    if audit_channel:
        await audit_channel.send(f"📜 **New Proposal**: #{proposal['proposal_id']} '{proposal['title']}' by <@{proposal['proposer_id']}> has been submitted for review")

class ApprovalView(discord.ui.View):
    """
    Interactive view for approving or rejecting a proposal
    """
    def __init__(self, proposal_id, author, timeout=600):
        super().__init__(timeout=timeout)
        self.proposal_id = proposal_id
        self.author = author
        self.reason = None

    @discord.ui.button(label="✅ Approve", style=discord.ButtonStyle.green)
    async def approve_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Check if the user has permission to approve
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("You don't have permission to approve proposals!", ephemeral=True)
            return

        # Disable buttons to prevent further interactions
        for child in self.children:
            child.disabled = True

        # Update the message
        await interaction.response.edit_message(view=self)

        # Approve the proposal
        await approve_proposal_from_interaction(interaction, self.proposal_id)

    @discord.ui.button(label="❌ Reject", style=discord.ButtonStyle.red)
    async def reject_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Check if the user has permission to reject
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("You don't have permission to reject proposals!", ephemeral=True)
            return

        # Create a modal for rejection reason
        modal = RejectReasonModal(self.proposal_id)
        await interaction.response.send_modal(modal)

class RejectReasonModal(discord.ui.Modal, title="Reject Proposal"):
    """Modal for entering rejection reason"""

    reason = discord.ui.TextInput(
        label="Rejection Reason",
        placeholder="Please enter the reason for rejecting this proposal",
        min_length=2,
        max_length=1000,
        style=discord.TextStyle.paragraph
    )

    def __init__(self, proposal_id):
        super().__init__()
        self.proposal_id = proposal_id

    async def on_submit(self, interaction: discord.Interaction):
        # Get the reason
        reason_text = self.reason.value

        # Reject the proposal
        await reject_proposal_from_interaction(interaction, self.proposal_id, reason_text)

        # Send a confirmation
        await interaction.response.send_message(f"Proposal #{self.proposal_id} has been rejected.", ephemeral=True)

async def approve_proposal_from_interaction(interaction, proposal_id):
    """Approve a proposal from an interaction"""
    # Get the proposal
    proposal = await db.get_proposal(proposal_id)
    if not proposal:
        await interaction.followup.send(f"❌ Proposal #{proposal_id} not found.")
        return

    # Update proposal status
    await db.update_proposal_status(proposal_id, "Voting")

    # Get options from database
    options = await db.get_proposal_options(proposal_id)
    print(f"DEBUG: Retrieved options for proposal {proposal_id} from database: {options}")

    # If no options found in database, try to extract from description
    if not options:
        options = extract_options_from_description(proposal['description'])
        print(f"DEBUG: Extracted options from description: {options}")

        # If still no options, use default Yes/No
        if not options:
            options = ["Yes", "No"]
            print(f"DEBUG: Using default Yes/No options")

        # Store the extracted options in the database
        await db.add_proposal_options(proposal_id, options)
        print(f"DEBUG: Stored extracted options in database: {options}")

    # Start voting - this will send DMs to eligible voters
    await start_voting(interaction.guild, proposal_id, options)

    # Notify the proposer
    try:
        proposer = await interaction.guild.fetch_member(proposal['proposer_id'])
        if proposer:
            await proposer.send(f"✅ Your proposal '{proposal['title']}' has been approved and is now open for voting!")
    except:
        pass  # Continue even if DM fails

    # Send confirmation message
    await interaction.followup.send(f"✅ Proposal #{proposal_id} has been approved and is now open for voting.")

    # Log to audit channel
    audit_channel = discord.utils.get(interaction.guild.text_channels, name="audit-log")
    if audit_channel:
        await audit_channel.send(f"✅ **Proposal Approved**: #{proposal_id} '{proposal['title']}' by <@{proposal['proposer_id']}> has been approved by {interaction.user.mention}")

async def reject_proposal_from_interaction(interaction, proposal_id, reason):
    """Reject a proposal from an interaction"""
    # Get the proposal
    proposal = await db.get_proposal(proposal_id)
    if not proposal:
        return

    # Update proposal status
    await db.update_proposal_status(proposal_id, "Rejected")

    # Store rejection reason
    await db.add_proposal_note(proposal_id, "rejection_reason", reason)

    # Notify the proposer
    try:
        proposer = await interaction.guild.fetch_member(proposal['proposer_id'])
        if proposer:
            await proposer.send(f"❌ Your proposal '{proposal['title']}' has been rejected.\nReason: {reason}")
    except:
        pass  # Continue even if DM fails

    # Log to audit channel
    audit_channel = discord.utils.get(interaction.guild.text_channels, name="audit-log")
    if audit_channel:
        await audit_channel.send(f"❌ **Proposal Rejected**: #{proposal_id} '{proposal['title']}' by <@{proposal['proposer_id']}> has been rejected by {interaction.user.mention}\nReason: {reason}")

class EarlyTerminationView(discord.ui.View):
    """View for early termination of proposals"""
    def __init__(self, proposal_id):
        super().__init__(timeout=None)  # No timeout for persistent views
        self.proposal_id = proposal_id

    @discord.ui.button(label="🛑 Terminate Early", style=discord.ButtonStyle.danger)
    async def terminate_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Check if the user has permission
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("You don't have permission to terminate proposals early!", ephemeral=True)
            return

        # Confirm termination
        await interaction.response.send_message(
            "Are you sure you want to terminate this proposal early and count the votes?",
            view=ConfirmTerminationView(self.proposal_id),
            ephemeral=True
        )

class ConfirmTerminationView(discord.ui.View):
    """View for confirming early termination"""
    def __init__(self, proposal_id):
        super().__init__(timeout=60)
        self.proposal_id = proposal_id

    @discord.ui.button(label="✅ Yes, terminate now", style=discord.ButtonStyle.danger)
    async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Disable buttons
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)

        # Process early termination
        await terminate_proposal_early(interaction, self.proposal_id)

    @discord.ui.button(label="❌ No, keep voting", style=discord.ButtonStyle.secondary)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Just close the confirmation dialog
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content="Early termination cancelled.", view=self)

async def terminate_proposal_early(interaction, proposal_id):
    """Terminate a proposal early and count the votes"""
    # Get the proposal
    proposal = await db.get_proposal(proposal_id)
    if not proposal:
        await interaction.followup.send(f"❌ Proposal #{proposal_id} not found.")
        return

    if proposal['status'] != "Voting":
        await interaction.followup.send(f"❌ Proposal #{proposal_id} is not in voting status.")
        return

    # Import voting module here to avoid circular imports
    from voting_utils import close_proposal, close_and_announce_results

    # Close the proposal
    results = await close_proposal(proposal_id)

    # Announce the results
    await close_and_announce_results(interaction.guild, proposal, results)

    # Send confirmation message
    await interaction.followup.send(f"✅ Proposal #{proposal_id} has been terminated early and the votes have been counted.")

    # Log to audit channel
    audit_channel = discord.utils.get(interaction.guild.text_channels, name="audit-log")
    if audit_channel:
        await audit_channel.send(f"🛑 **Proposal Terminated Early**: #{proposal_id} '{proposal['title']}' has been terminated early by {interaction.user.mention}")

async def start_proposal_creation_flow(ctx: commands.Context):
    """Starts the new two-step proposal creation flow."""
    try:
        print(f"🔍 {ctx.author} invoked proposal creation in {ctx.guild.name}")

        # For the new UI flow with ProposalMechanismSelectionView, we need an interaction
        # to be able to edit the original message later (e.g., on timeout or after selection).
        # If ctx.interaction is None (typical for pure message commands without a prior interaction),
        # editing the message that contains the view might be problematic from the view itself.
        # The view callbacks (button clicks) will generate their own interactions.

        initial_interaction = ctx.interaction # This will be None if it's a pure message command.

        if initial_interaction:
            # If we have an interaction (e.g., from a slash command or hybrid command context)
            # we can send an ephemeral response with the view.
            await initial_interaction.response.send_message(
                "Please select a voting mechanism for your proposal:",
                view=ProposalMechanismSelectionView(initial_interaction),
                ephemeral=True
            )
            print(f"✅ ProposalMechanismSelectionView sent (ephemeral) to {ctx.channel.name} in {ctx.guild.name} for {ctx.author}")
        else:
            # Fallback for pure message commands: send a regular message.
            # The ProposalMechanismSelectionView needs to handle original_interaction being None.
            # Let's adapt ProposalMechanismSelectionView to handle original_interaction=None
            # For now, we send a message and the view. Editing this message from the view will not be direct.
            message = await ctx.send(
                "Please select a voting mechanism for your proposal:",
                view=ProposalMechanismSelectionView(None) # Pass None, view must handle this
            )
            print(f"✅ ProposalMechanismSelectionView sent (message) to {ctx.channel.name} in {ctx.guild.name} for {ctx.author}")
            # Note: If original_interaction is None, ProposalMechanismSelectionView cannot edit this initial message.

    except Exception as e:
        print(f"❌ Error in start_proposal_creation_flow: {e}")
        traceback.print_exc()
        try:
            # Try to respond to interaction if it exists and response hasn't been sent
            if initial_interaction and not initial_interaction.response.is_done():
                await initial_interaction.response.send_message(f"❌ An error occurred initiating proposal creation: {e}", ephemeral=True)
            else:
                # Fallback to ctx.send if no interaction or response already sent
                await ctx.send(f"❌ An error occurred initiating proposal creation: {e}")
        except Exception as e_report:
            print(f"Error sending error report: {e_report}")
            pass
