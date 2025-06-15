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
            ("D'Hondt Method", "dhondt", "⚖️"),  # Changed emoji for D'Hondt
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

async def start_proposal_creation_flow(ctx: commands.Context):
    """Starts the new two-step proposal creation flow."""
    try:
        # Check if the command was invoked in a guild or DM
        if ctx.guild is None:
            await ctx.send("The `!propose` command can only be used in a server channel, not in DMs.")
            print(f"INFO: {ctx.author} attempted to use !propose in DMs.")
            return

        print(f"🔍 {ctx.author} invoked proposal creation in {ctx.guild.name}")

        # For the new UI flow with ProposalMechanismSelectionView, we need an interaction
        # to be able to edit the original message later (e.g., on timeout or after selection).
        # If ctx.interaction is None (typical for pure message commands without a prior interaction),
        # editing the message that contains the view might be problematic from the view itself.
        # The view callbacks (button clicks) will generate their own interactions.

        initial_interaction = ctx.interaction # This will be None if it's a pure message command.

        # Pass the invoker's ID to the view for interaction checks.
        # Pass the initial_interaction (which might be None) for potential message editing on timeout.
        view = ProposalMechanismSelectionView(original_interaction=initial_interaction, invoker_id=ctx.author.id)

        if initial_interaction:
            # If we have an interaction, we can respond to it directly and the view can edit this response.
            await initial_interaction.response.send_message("Please select a voting mechanism for your proposal:", view=view, ephemeral=False) # Send non-ephemeral so others can see it if desired
            print(f"✅ Sent ProposalMechanismSelectionView to {ctx.author} via interaction response.")
        else:
            # If it's a message command (no initial interaction), send a new message with the view.
            # The view won't be able to easily edit *this* message on timeout without more complex state management.
            # However, button clicks on the view will still work and create their own interactions.
            sent_message = await ctx.send("Please select a voting mechanism for your proposal:", view=view)
            print(f"✅ Sent ProposalMechanismSelectionView to {ctx.author} via new message (ID: {sent_message.id}).")
            # We could store sent_message.id if we wanted to try and edit/delete it later,
            # but that requires more complex handling within the view or a separate manager.
            # For now, on timeout, if original_interaction was None, the view just stops without editing a message.

    except Exception as e:
        print(f"❌ Error in start_proposal_creation_flow: {e}")
        traceback.print_exc()
        error_message = f"❌ An error occurred: {e}"
        try:
            if initial_interaction and not initial_interaction.response.is_done():
                await initial_interaction.response.send_message(error_message, ephemeral=True)
            elif ctx.channel: # Check if ctx.channel is not None
                await ctx.send(error_message)
            else: # Fallback if no channel context (e.g., if ctx was somehow None, very unlikely)
                print(f"CRITICAL: Could not send error to user {ctx.author.id if ctx.author else 'Unknown User'} in start_proposal_creation_flow")
        except Exception as e_report:
            print(f"Error sending error report: {e_report}")
            pass
