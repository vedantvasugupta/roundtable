import discord
from discord.ext import commands
import asyncio
from datetime import datetime, timedelta
import json
import db
import voting
import voting_utils
import utils
import traceback
import re
from typing import List, Optional, Dict, Any, Union

# Enable all intents (or specify only the necessary ones)
intents = discord.Intents.default()
intents.message_content = True  # Required for handling messages

# Initialize bot with intents
bot = commands.Bot(command_prefix="!", intents=intents)

# ========================
# 🔹 CAMPAIGN DEFINITION VIEWS & MODALS (NEW)
# ========================

class CampaignSetupModal(discord.ui.Modal, title="Create New Weighted Campaign"):
    def __init__(self, original_interaction: discord.Interaction):
        super().__init__()
        self.original_interaction = original_interaction

        self.campaign_title_input = discord.ui.TextInput(
            label="Campaign Title",
            placeholder="Enter a clear title for the overall campaign",
            min_length=5,
            max_length=100,
            required=True,
            style=discord.TextStyle.short
        )
        self.add_item(self.campaign_title_input)

        self.campaign_description_input = discord.ui.TextInput(
            label="Campaign Description (Optional)",
            placeholder="Provide a brief overview of the campaign's purpose",
            max_length=1000,
            required=False,
            style=discord.TextStyle.paragraph
        )
        self.add_item(self.campaign_description_input)

        self.total_tokens_input = discord.ui.TextInput(
            label="Total Tokens Per Voter (for campaign)",
            placeholder="e.g., 100",
            min_length=1,
            max_length=5,
            required=True,
            style=discord.TextStyle.short
        )
        self.add_item(self.total_tokens_input)

        self.num_scenarios_input = discord.ui.TextInput(
            label="Number of Voting Scenarios/Rounds",
            placeholder="e.g., 3",
            min_length=1,
            max_length=2,
            required=True,
            style=discord.TextStyle.short
        )
        self.add_item(self.num_scenarios_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer(thinking=True, ephemeral=True)

            title = self.campaign_title_input.value
            description = self.campaign_description_input.value or None

            try:
                total_tokens = int(self.total_tokens_input.value)
                if total_tokens <= 0:
                    await interaction.followup.send("Total tokens must be a positive number.", ephemeral=True)
                    return
            except ValueError:
                await interaction.followup.send("Invalid input for total tokens. Please enter a number.", ephemeral=True)
                return

            try:
                num_scenarios = int(self.num_scenarios_input.value)
                if num_scenarios <= 0:
                    await interaction.followup.send("Number of scenarios must be a positive number.", ephemeral=True)
                    return
                if num_scenarios > 10: # Practical limit
                    await interaction.followup.send("Maximum number of scenarios is 10 for now.", ephemeral=True)
                    return
            except ValueError:
                await interaction.followup.send("Invalid input for number of scenarios. Please enter a number.", ephemeral=True)
                return

            campaign_id = await db.create_campaign(
                guild_id=interaction.guild_id,
                creator_id=interaction.user.id,
                title=title,
                description=description,
                total_tokens_per_voter=total_tokens,
                num_expected_scenarios=num_scenarios
            )

            if campaign_id:
                # Pass the interaction that SUBMITTED this modal as original_interaction to DefineScenarioView
                # so that DefineScenarioView's on_timeout can edit the followup message from THIS modal submission.
                view = DefineScenarioView(campaign_id=campaign_id, next_scenario_order=1, total_scenarios=num_scenarios, original_interaction=interaction)

                followup_message_content = (
                    f"🗳️ Weighted Campaign '{title}' initiated (ID: {campaign_id})!\n"
                    f"Each voter will have **{total_tokens} tokens** for the entire campaign, which will consist of **{num_scenarios} voting scenarios**.\n\n"
                    f"Next, you need to define Scenario 1 of {num_scenarios}."
                )
                # Send a non-ephemeral followup message with the DefineScenarioView
                sent_message = await interaction.followup.send(content=followup_message_content, view=view, ephemeral=False)
                view.message = sent_message # Allow the view to edit this message on timeout

                # Try to edit the original interaction that opened the mechanism selection (if it exists and was passed)
                # This original_interaction is from the ProposalMechanismSelectionView (or a command)
                if self.original_interaction and not self.original_interaction.is_expired():
                    try:
                        await self.original_interaction.edit_original_response(content="Campaign setup initiated. Please follow the prompts from the new message.", view=None)
                    except discord.HTTPException:
                        pass # Original interaction might have been ephemeral or already handled
            else:
                await interaction.followup.send("Failed to create the campaign in the database.", ephemeral=True)

        except Exception as e:
            print(f"Error in CampaignSetupModal on_submit: {e}")
            traceback.print_exc()
            # Ensure a response is sent even on unexpected error
            if not interaction.response.is_done():
                await interaction.response.send_message(f"An unexpected error occurred: {e}", ephemeral=True)
            else:
                await interaction.followup.send(f"An unexpected error occurred: {e}", ephemeral=True)

class DefineScenarioView(discord.ui.View):
    def __init__(self, campaign_id: int, next_scenario_order: int, total_scenarios: int, original_interaction: Optional[discord.Interaction] = None):
        super().__init__(timeout=300)
        self.campaign_id = campaign_id
        self.next_scenario_order = next_scenario_order
        self.total_scenarios = total_scenarios
        # original_interaction for this view is the one that submitted the CampaignSetupModal or previous DefineScenarioView button
        # It is used to edit the followup message if this view times out.
        self.original_interaction = original_interaction
        self.message: Optional[discord.Message] = None # This will be the message THIS view is attached to

        self.define_button = discord.ui.Button(
            label=f"Define Scenario {next_scenario_order} of {total_scenarios}",
            style=discord.ButtonStyle.primary,
            custom_id=f"define_scenario_{campaign_id}_{next_scenario_order}"
        )
        self.define_button.callback = self.define_scenario_callback
        self.add_item(self.define_button)

    async def define_scenario_callback(self, interaction: discord.Interaction):
        # original_interaction for ProposalMechanismSelectionView will be THIS button interaction.
        # This allows ProposalMechanismSelectionView to edit the ephemeral message it sends.
        view = ProposalMechanismSelectionView(
            original_interaction=interaction,
            invoker_id=interaction.user.id,
            campaign_id=self.campaign_id,
            scenario_order=self.next_scenario_order
        )
        await interaction.response.send_message(
            f"Defining Scenario {self.next_scenario_order} for Campaign ID {self.campaign_id}.\nSelect the voting mechanism for this scenario:",
            view=view,
            ephemeral=True
        )

        self.define_button.disabled = True
        # Edit the message this DefineScenarioView was attached to (which is self.message, set by CampaignSetupModal or previous scenario completion)
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass # Message might have been deleted
        self.stop()

    async def on_timeout(self):
        self.define_button.disabled = True
        self.define_button.label = f"Timed out defining Scenario {self.next_scenario_order}"
        # self.message is the message this DefineScenarioView is attached to.
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException as e:
                print(f"Error editing DefineScenarioView message on timeout: {e}")
        # self.original_interaction is the interaction that created this view (e.g. CampaignSetupModal submission)
        # We don't typically edit that one on THIS view's timeout. The primary concern is disabling buttons on THIS view's message.
        self.stop()

# ========================
# 🔹 INTERACTIVE PROPOSAL CREATION
# ========================

class ProposalMechanismSelectionView(discord.ui.View):
    """View with buttons to select a voting mechanism for a new proposal OR a campaign scenario."""
    def __init__(self, original_interaction: Optional[discord.Interaction], invoker_id: int, campaign_id: Optional[int] = None, scenario_order: Optional[int] = None):
        super().__init__(timeout=300)  # 5 minutes timeout
        self.original_interaction = original_interaction
        self.invoker_id = invoker_id
        self.campaign_id = campaign_id # Will be None if not in campaign flow
        self.scenario_order = scenario_order # Will be None if not in campaign flow
        self.message: Optional[discord.Message] = None # To store the message this view is attached to, if needed

        mechanisms = [
            ("Plurality", "plurality", "🗳️"),
            ("Borda Count", "borda", "📊"),
            ("Approval Voting", "approval", "👍"),
            ("Runoff Voting", "runoff", "🔄"),
            ("D'Hondt Method", "dhondt", "⚖️"),
        ]

        # Add Weighted Campaign creation button ONLY if not already in a campaign definition flow
        if not self.campaign_id:
            wc_button = discord.ui.Button(
                label="Create Weighted Campaign",
                style=discord.ButtonStyle.success,
                custom_id="select_mechanism_weighted_campaign",
                emoji="⚖️💰",
                row = (len(mechanisms) // 2) # Place it after standard mechanisms
            )
            # Ensure row calculation is safe if mechanisms list is short
            button_row = (len(mechanisms) // 2)
            if len(mechanisms) % 2 != 0: # If odd number of mechanisms, last one is on a new row
                 button_row +=1
            wc_button.row = button_row

            wc_button.callback = self.weighted_campaign_button_callback
            self.add_item(wc_button)

        for i, (label, custom_id_suffix, emoji) in enumerate(mechanisms):
            button = discord.ui.Button(
                label=label,
                style=discord.ButtonStyle.secondary,
                custom_id=f"select_mechanism_{custom_id_suffix}",
                emoji=emoji,
                row=i // 2
            )
            button.callback = self.mechanism_button_callback
            self.add_item(button)

    async def weighted_campaign_button_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.invoker_id:
            await interaction.response.send_message("This interaction is not for you.", ephemeral=True)
            return

        # original_interaction for CampaignSetupModal should be the one that triggered this view, if available.
        # If this view was sent in a new message (e.g. by !propose), self.original_interaction might be None
        # or it could be the interaction from the command itself.
        # For the modal, we want to be able to edit the message that SHOWED the "Create Weighted Campaign" button.
        # This current `interaction` is from clicking the campaign button itself.
        modal = CampaignSetupModal(original_interaction=self.original_interaction if self.original_interaction else interaction)
        await interaction.response.send_modal(modal)
        self.stop() # Stop this view

        # Attempt to edit the message that this view was attached to, to remove the buttons.
        # This `interaction` (from button click) is what we use to edit its own message (which contained the view).
        try:
            await interaction.edit_original_response(content="Weighted Campaign setup form sent. Please fill it out.", view=None)
        except discord.HTTPException as e:
            print(f"Non-critical error: Could not edit original message for campaign button: {e}")
            # This can happen if the original message was ephemeral, or if interaction context has changed.
            # Modal was sent, that's the main thing.
            pass

    async def mechanism_button_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.invoker_id:
            await interaction.response.send_message("This interaction is not for you.", ephemeral=True)
            return

        custom_id = interaction.data["custom_id"]
        mechanism_name = custom_id.replace("select_mechanism_", "")

        modal_title_prefix = "New"
        if self.campaign_id and self.scenario_order:
            modal_title_prefix = f"Campaign Scenario {self.scenario_order}: New"

        modal: Optional[discord.ui.Modal] = None
        if mechanism_name == "plurality":
            modal = PluralityProposalModal(interaction, mechanism_name, campaign_id=self.campaign_id, scenario_order=self.scenario_order, title_prefix=modal_title_prefix)
        elif mechanism_name == "borda":
            modal = BordaProposalModal(interaction, mechanism_name, campaign_id=self.campaign_id, scenario_order=self.scenario_order, title_prefix=modal_title_prefix)
        elif mechanism_name == "approval":
            modal = ApprovalProposalModal(interaction, mechanism_name, campaign_id=self.campaign_id, scenario_order=self.scenario_order, title_prefix=modal_title_prefix)
        elif mechanism_name == "runoff":
            modal = RunoffProposalModal(interaction, mechanism_name, campaign_id=self.campaign_id, scenario_order=self.scenario_order, title_prefix=modal_title_prefix)
        elif mechanism_name == "dhondt":
            modal = DHondtProposalModal(interaction, mechanism_name, campaign_id=self.campaign_id, scenario_order=self.scenario_order, title_prefix=modal_title_prefix)

        if modal:
            await interaction.response.send_modal(modal)
            self.stop()
            if self.original_interaction and not self.original_interaction.is_expired():
                try:
                    content_msg = "Proposal creation form sent."
                    if self.campaign_id and self.scenario_order:
                        content_msg = f"Scenario {self.scenario_order} - {mechanism_name.title()} form sent."
                    await self.original_interaction.edit_original_response(content=content_msg, view=None)
                except discord.HTTPException:
                    pass
        else:
            await interaction.response.send_message(f"Modal for {mechanism_name} not implemented yet.", ephemeral=True)

    async def on_timeout(self):
        if self.original_interaction and not self.original_interaction.is_expired():
            try:
                content_msg = "Proposal mechanism selection timed out."
                if self.campaign_id:
                    content_msg = f"Scenario {self.scenario_order} mechanism selection timed out."
                await self.original_interaction.edit_original_response(content=content_msg + " Please run the command again.", view=None)
            except discord.NotFound:
                print("INFO: Original interaction message not found on timeout, likely already deleted or handled.")
            except discord.HTTPException as e:
                print(f"ERROR: Failed to edit original interaction on timeout: {e}")
        self.stop()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.invoker_id:
            await interaction.response.send_message("You cannot interact with this button.", ephemeral=True)
            return False
        return True

class BaseProposalModal(discord.ui.Modal):
    """Base modal for creating a proposal with common fields."""
    def __init__(self, interaction: discord.Interaction, mechanism_name: str, title_prefix: str = "New", campaign_id: Optional[int] = None, scenario_order: Optional[int] = None):
        super().__init__(title=f"{title_prefix} {mechanism_name.title()} Proposal")
        self.original_interaction = interaction
        self.mechanism_name = mechanism_name
        self.campaign_id = campaign_id # New
        self.scenario_order = scenario_order # New

        self.proposal_title_input = discord.ui.TextInput(
            label="Proposal Title",
            placeholder="Enter a concise title for your proposal",
            min_length=1,
            max_length=100,
            required=True,
            style=discord.TextStyle.short
        )
        self.add_item(self.proposal_title_input)

        self.options_input = discord.ui.TextInput(
            label="Options (One Per Line)",
            placeholder="Enter each voting option on a new line.\\nDefault: Yes, No (if left blank)",
            required=False,
            style=discord.TextStyle.paragraph
        )
        self.add_item(self.options_input)

        self.deadline_input = discord.ui.TextInput(
            label="Voting Duration (e.g., 7d, 24h, 30m)",
            placeholder="Default: 7d (7 days)",
            required=False,
            style=discord.TextStyle.short,
            default="7d"
        )
        self.add_item(self.deadline_input)

    async def common_on_submit(self, interaction: discord.Interaction, specific_hyperparameters: Dict[str, Any]):
        """Common logic for modal submission, to be called by subclasses."""
        try:
            title = self.proposal_title_input.value
            description = f"{self.mechanism_name.title()} proposal for Campaign {self.campaign_id}, Scenario {self.scenario_order}." if self.campaign_id else "No description provided."

            options_text = self.options_input.value
            deadline_str = self.deadline_input.value or "7d"

            options = [opt.strip() for opt in options_text.split('\n') if opt.strip()] if options_text else ["Yes", "No"]
            if not options:
                await interaction.response.send_message("Please provide at least one option, or leave blank for default Yes/No.", ephemeral=True)
                return

            deadline_seconds = utils.parse_duration(deadline_str)
            if deadline_seconds is None: # parse_duration returns None on error
                await interaction.response.send_message(f"Invalid duration format: '{deadline_str}'. Use d, h, m (e.g., 7d, 24h, 30m).", ephemeral=True)
                return

            actual_deadline_datetime = datetime.utcnow() + timedelta(seconds=deadline_seconds)
            deadline_db_str = actual_deadline_datetime.strftime('%Y-%m-%d %H:%M:%S.%f')

            # Defer response if not already done. Modal on_submit usually is not done before this point.
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=True, thinking=True)
            else: # If already deferred (e.g. by a specific modal check before calling common_on_submit)
                await interaction.followup.send("Processing...", ephemeral=True) # Send a quick followup if defer was earlier

            proposal_id = await _create_new_proposal_entry(
                interaction,
                title,
                description, # Pass the determined description
                self.mechanism_name,
                options,
                deadline_db_str,
                hyperparameters=specific_hyperparameters,
                campaign_id=self.campaign_id, # Pass campaign context
                scenario_order=self.scenario_order # Pass scenario context
            )

            # Feedback is now handled by _create_new_proposal_entry for campaign flow

        except Exception as e:
            print(f"Error in BaseProposalModal common_on_submit: {e}")
            traceback.print_exc()
            error_message = f"An unexpected error occurred during proposal submission: {e}"
            if interaction.response.is_done():
                await interaction.followup.send(error_message, ephemeral=True)
            else:
                try:
                    await interaction.response.send_message(error_message, ephemeral=True)
                except discord.InteractionResponded: # Should not happen if we check is_done()
                    await interaction.followup.send(error_message, ephemeral=True)

class PluralityProposalModal(BaseProposalModal):
    def __init__(self, interaction: discord.Interaction, mechanism_name: str, campaign_id: Optional[int] = None, scenario_order: Optional[int] = None, title_prefix: str = "New"):
        super().__init__(interaction, mechanism_name, title_prefix=title_prefix, campaign_id=campaign_id, scenario_order=scenario_order)

class BordaProposalModal(BaseProposalModal):
    def __init__(self, interaction: discord.Interaction, mechanism_name: str, campaign_id: Optional[int] = None, scenario_order: Optional[int] = None, title_prefix: str = "New"):
        super().__init__(interaction, mechanism_name, title_prefix=title_prefix, campaign_id=campaign_id, scenario_order=scenario_order)

class ApprovalProposalModal(BaseProposalModal):
    def __init__(self, interaction: discord.Interaction, mechanism_name: str, campaign_id: Optional[int] = None, scenario_order: Optional[int] = None, title_prefix: str = "New"):
        super().__init__(interaction, mechanism_name, title_prefix=title_prefix, campaign_id=campaign_id, scenario_order=scenario_order)

class RunoffProposalModal(BaseProposalModal):
    def __init__(self, interaction: discord.Interaction, mechanism_name: str, campaign_id: Optional[int] = None, scenario_order: Optional[int] = None, title_prefix: str = "New"):
        super().__init__(interaction, mechanism_name, title_prefix=title_prefix, campaign_id=campaign_id, scenario_order=scenario_order)

class DHondtProposalModal(BaseProposalModal):
    def __init__(self, interaction: discord.Interaction, mechanism_name: str, campaign_id: Optional[int] = None, scenario_order: Optional[int] = None, title_prefix: str = "New"):
        super().__init__(interaction, mechanism_name, title_prefix=title_prefix, campaign_id=campaign_id, scenario_order=scenario_order)

# Helper function to be called by BaseProposalModal (Now _create_new_proposal_entry)
async def _create_new_proposal_entry(interaction: discord.Interaction, title: str, description: str, mechanism_name: str, options: List[str], deadline_db_str: str, hyperparameters: Optional[Dict[str, Any]] = None, campaign_id: Optional[int] = None, scenario_order: Optional[int] = None) -> Optional[int]:
    """Handles the actual proposal/scenario creation in DB and sends feedback."""
    try:
        server_id = interaction.guild_id
        proposer_id = interaction.user.id
        description_to_store = description # Already determined by caller (BaseProposalModal.common_on_submit)

        # --- Standard Proposal Approval Logic (Can be adapted for campaigns) ---
        requires_approval = True # Default, can be overridden by constitutional_vars
        try:
            const_vars = await db.get_constitutional_variables(server_id)
            if const_vars and "proposal_requires_approval" in const_vars:
                requires_approval_val = const_vars["proposal_requires_approval"]["value"]
                if isinstance(requires_approval_val, str):
                    requires_approval = requires_approval_val.lower() == "true"
                elif isinstance(requires_approval_val, (bool, int)):
                    requires_approval = bool(requires_approval_val)
        except Exception as e_cv:
            print(f"Notice: Could not fetch constitutional_vars for proposal_requires_approval, defaulting to True. Error: {e_cv}")
        # --- End Standard Proposal Approval Logic ---

        proposal_id = await db.create_proposal(
            server_id=server_id,
            proposer_id=proposer_id,
            title=title,
            description=description_to_store,
            voting_mechanism=mechanism_name,
            deadline=deadline_db_str,
            requires_approval=requires_approval,
            hyperparameters=hyperparameters,
            campaign_id=campaign_id, # Pass to db
            scenario_order=scenario_order # Pass to db
        )

        if not proposal_id:
            feedback_message = "❌ Failed to create proposal/scenario in the database."
            if interaction.response.is_done():
                await interaction.followup.send(feedback_message, ephemeral=True)
            else:
                await interaction.response.send_message(feedback_message, ephemeral=True)
            return None

        await db.add_proposal_options(proposal_id, options)
        print(f"DEBUG: Proposal/Scenario P#{proposal_id} options added: {options}")

        user_feedback_message = f"✅ Scenario {scenario_order} ('{title}') for Campaign ID {campaign_id} created successfully!"
        admin_notification_needed = False

        if campaign_id:
            # This is a scenario within a campaign
            print(f"DEBUG: P#{proposal_id} is Scenario {scenario_order} for Campaign C#{campaign_id}")
            current_defined_count = await db.increment_defined_scenarios(campaign_id)
            campaign_details = await db.get_campaign(campaign_id)

            if not campaign_details:
                await interaction.followup.send("Error: Could not retrieve campaign details after defining scenario.", ephemeral=True)
                return proposal_id # Return proposal_id but signal error with campaign part

            if requires_approval:
                user_feedback_message += " It has been submitted for admin approval (standard proposal rules apply)."
                admin_notification_needed = True # Standard admin approval for this scenario
            else:
                user_feedback_message += " Voting for this scenario can begin once the campaign is active and this scenario is reached."
                # No immediate voting start for scenarios; campaign must be started.

            if current_defined_count is not None and current_defined_count < campaign_details['num_expected_scenarios']:
                next_view = DefineScenarioView(campaign_id=campaign_id, next_scenario_order=current_defined_count + 1, total_scenarios=campaign_details['num_expected_scenarios'], original_interaction=interaction)
                await interaction.followup.send(user_feedback_message + f"\n\nNext: Define Scenario {current_defined_count + 1} of {campaign_details['num_expected_scenarios']}.", view=next_view, ephemeral=False)
            else:
                # All scenarios for the campaign are defined
                await db.update_campaign_status(campaign_id, 'setup') # Still 'setup', admin needs to make it 'active'
                user_feedback_message += f"\n\n🎉 All {campaign_details['num_expected_scenarios']} scenarios for Campaign '{campaign_details['title']}' (ID: {campaign_id}) are now defined!"
                user_feedback_message += "\nAn admin can now activate the campaign to start the voting process for the first scenario."
                # TODO: Add a button here for admins to 'Activate Campaign' or send a separate notification.
                await interaction.followup.send(user_feedback_message, ephemeral=False)
        else:
            # This is a standalone proposal (not part of a campaign)
            user_feedback_message = f"✅ Proposal #{proposal_id} ('{title}') created successfully!"
            if requires_approval:
                user_feedback_message += " It has been submitted for admin approval."
                admin_notification_needed = True
            else:
                user_feedback_message += " Voting has started."
                # Announce voting started for non-campaign, non-approval proposal
                # This part matches the original non-approval flow
                proposals_public_channel_name = "proposals"
                proposals_public_channel = await utils.get_or_create_channel(interaction.guild, proposals_public_channel_name, interaction.client.user.id)
                if proposals_public_channel:
                    proposer_member = interaction.guild.get_member(proposer_id) or proposer_id
                    public_embed = utils.create_proposal_embed(
                        proposal_id, proposer_member, title, description_to_store,
                        mechanism_name, deadline_db_str, "Voting", options,
                        hyperparameters=hyperparameters, campaign_id=None, scenario_order=None
                    )
                    await proposals_public_channel.send(content=f"🎉 Voting has started for Proposal #{proposal_id}!", embed=public_embed)

                voting_room_channel_name = "voting-room"
                voting_room_channel = await utils.get_or_create_channel(interaction.guild, voting_room_channel_name, interaction.client.user.id)
                if voting_room_channel:
                    await voting_utils.update_vote_tracking(interaction.guild, proposal_id) # Assumes this function can handle standalone proposals

            # Send feedback to the user who created the standalone proposal
            # If it was deferred, followup. If not (e.g. error before defer), send_message might be needed but defer is standard in common_on_submit.
            if interaction.response.is_done():
                 await interaction.followup.send(user_feedback_message, ephemeral=False) # Make it non-ephemeral for proposal creator to see easily
            else:
                # This case should ideally not be hit if common_on_submit defers.
                await interaction.response.send_message(user_feedback_message, ephemeral=False)

        if admin_notification_needed:
            # Logic for sending admin notification (applies to both campaign scenarios and standalone proposals if they require approval)
            admin_channel_name = "proposals" # Or a dedicated admin channel
            admin_channel = await utils.get_or_create_channel(interaction.guild, admin_channel_name, interaction.client.user.id)
            if admin_channel:
                admin_embed_title = f"🆕 Proposal Submitted for Approval: P#{proposal_id} - {title}"
                if campaign_id and scenario_order:
                    admin_embed_title = f"🆕 Campaign Scenario Submitted: C#{campaign_id} S#{scenario_order} P#{proposal_id} - {title}"

                admin_embed = discord.Embed(
                    title=admin_embed_title,
                    description=f"Proposed by: {interaction.user.mention}\n\n**Description:**\n{description_to_store}",
                    color=discord.Color.orange()
                )
                admin_embed.add_field(name="Voting Mechanism", value=mechanism_name.title(), inline=True)
                admin_embed.add_field(name="Options", value="\n".join([f"• {opt}" for opt in options]) or "Default: Yes/No", inline=False)
                admin_embed.add_field(name="Requested Voting Deadline", value=utils.format_deadline(deadline_db_str), inline=True)

                if campaign_id:
                    campaign_info = await db.get_campaign(campaign_id)
                    if campaign_info:
                         admin_embed.add_field(name="Part of Campaign", value=f"'{campaign_info['title']}' (ID: {campaign_id}) - Scenario {scenario_order}", inline=False)

                if hyperparameters:
                    hyperparams_text_parts = []
                    # Dynamically build hyperparameter display based on known keys for each mechanism
                    if mechanism_name == "plurality":
                        if "allow_abstain" in hyperparameters:
                            hyperparams_text_parts.append(f"Allow Abstain: {'Yes' if hyperparameters['allow_abstain'] else 'No'}")
                        if "winning_threshold_percentage" in hyperparameters and hyperparameters["winning_threshold_percentage"] is not None:
                            hyperparams_text_parts.append(f"Winning Threshold: {hyperparameters['winning_threshold_percentage']}%" )
                        elif not hyperparams_text_parts: # If only default (simple majority) for plurality
                             hyperparams_text_parts.append("Winning Threshold: Simple Majority")
                    elif mechanism_name == "dhondt":
                        if "allow_abstain" in hyperparameters:
                            hyperparams_text_parts.append(f"Allow Abstain: {'Yes' if hyperparameters['allow_abstain'] else 'No'}")
                        if "num_seats" in hyperparameters:
                            hyperparams_text_parts.append(f"Number of Seats: {hyperparameters['num_seats']}")
                    # Add other mechanism hyperparameter displays here...
                    else: # Generic display for other mechanisms if specific keys not checked
                        for key, value in hyperparameters.items():
                            # Simple formatting, can be improved
                            display_value = 'Yes' if isinstance(value, bool) and value else 'No' if isinstance(value, bool) and not value else value
                            hyperparams_text_parts.append(f"{key.replace('_', ' ').title()}: {display_value}")

                    if hyperparams_text_parts:
                        admin_embed.add_field(name="Voting Rules", value="\n".join(hyperparams_text_parts), inline=False)

                admin_embed.set_footer(text=f"Proposal ID: {proposal_id} | Submitted at {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
                current_view = AdminApprovalView(proposal_id=proposal_id, bot_instance=interaction.client, campaign_id=campaign_id, scenario_order=scenario_order)
                await admin_channel.send(
                    content=f"Admins, action required for the following:" if not campaign_id else f"Admins, action required for campaign scenario:",
                    embed=admin_embed,
                    view=current_view
                )
            else:
                # This followup might be to an ephemeral message if the main one was non-ephemeral.
                # It's okay, just informs the creator about the admin channel issue.
                alt_feedback = " However, there was an issue notifying admins in the designated channel."
                if interaction.response.is_done(): # Check if initial response from modal on_submit was done
                    current_content = ""
                    # Try to get current content if possible, though followup.send replaces it
                    # This is tricky with ephemeral followups. Best to just send the new info.
                    await interaction.followup.send(alt_feedback, ephemeral=True)
                else:
                    # Should not happen given defer in common_on_submit
                    await interaction.response.send_message(alt_feedback, ephemeral=True)

        return proposal_id # Return proposal_id in all successful paths

    except Exception as e:
        print(f"ERROR in _create_new_proposal_entry P#{proposal_id if 'proposal_id' in locals() else 'Unknown'}: {e}")
        traceback.print_exc()
        error_message_final = f"An error occurred while finalizing the proposal/scenario: {e}"
        if interaction and not interaction.is_expired():
            try:
                if interaction.response.is_done():
                    await interaction.followup.send(error_message_final, ephemeral=True)
                else:
                    await interaction.response.send_message(error_message_final, ephemeral=True)
            except discord.HTTPException as http_e:
                print(f"Error sending followup error message in _create_new_proposal_entry: {http_e}")
        return None

async def start_proposal_creation_flow(ctx: commands.Context):
    """Starts the new proposal creation flow (standalone or first step of campaign scenario definition)."""
    # ... (This function will need to be updated to correctly launch ProposalMechanismSelectionView
    # without campaign_id/scenario_order for a new standalone proposal)
    # For now, the main entry point for campaigns is via the new button in ProposalMechanismSelectionView itself.
    # This function will be the entry for !propose for standalone proposals.
    try:
        if ctx.guild is None:
            await ctx.send("The `!propose` command can only be used in a server channel.")
            return

        # For a standalone proposal, campaign_id and scenario_order are None.
        view = ProposalMechanismSelectionView(original_interaction=ctx.interaction, invoker_id=ctx.author.id)

        # The original_interaction for the view is ctx.interaction (which could be None for message commands)
        # The view then handles sending its own message or responding to the interaction.
        if ctx.interaction: # If it's a slash command or component interaction that invoked this
            await ctx.interaction.response.send_message("Please select a voting mechanism for your proposal:", view=view, ephemeral=False)
        else: # If it's a message command like !propose
            # Store the message sent so the view can potentially edit it on timeout.
            # This requires the view to accept and store the message object.
            sent_message = await ctx.send("Please select a voting mechanism for your proposal:", view=view)
            view.message = sent_message # Allow the view to know about its message

    except Exception as e:
        print(f"❌ Error in start_proposal_creation_flow: {e}")
        traceback.print_exc()
        await ctx.send(f"An error occurred: {e}")


# New View for Admin Approval Buttons
class AdminApprovalView(discord.ui.View):
    def __init__(self, proposal_id: int, bot_instance: commands.Bot, campaign_id: Optional[int] = None, scenario_order: Optional[int] = None):
        super().__init__(timeout=None)
        self.proposal_id = proposal_id
        self.bot = bot_instance
        self.campaign_id = campaign_id
        self.scenario_order = scenario_order

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.success, custom_id="admin_approve_proposal")
    async def approve_button_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("You do not have permission to approve proposals.", ephemeral=True)
            return

        # Defer to prevent interaction timeout if action takes time
        await interaction.response.defer()

        success, message_content = await _perform_approve_proposal_action(interaction, self.proposal_id, self.bot)

        # Edit the original message with the outcome
        original_embed = interaction.message.embeds[0] if interaction.message.embeds else None
        new_embed = None
        if original_embed:
            new_embed = original_embed.copy()
            if success:
                new_embed.colour = discord.Color.green()
                new_embed.set_field_at(0, name="Status", value=f"Approved by {interaction.user.mention}", inline=True) # Assuming status is first field
            else:
                new_embed.colour = discord.Color.red()
            # Remove or update other fields as necessary, e.g., a footer indicating action taken
            new_embed.set_footer(text=f"Actioned by {interaction.user.display_name} at {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")

        # Disable buttons
        for item in self.children:
            item.disabled = True

        await interaction.message.edit(content=message_content, embed=new_embed, view=self)
        # Optionally send a followup if more details are needed or if the original message edit isn't enough
        # await interaction.followup.send(message_content, ephemeral=True if not success else False)


    @discord.ui.button(label="Reject", style=discord.ButtonStyle.danger, custom_id="admin_reject_proposal")
    async def reject_button_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("You do not have permission to reject proposals.", ephemeral=True)
            return

        # Send modal to get reason
        modal = RejectReasonModal(proposal_id=self.proposal_id, original_button_interaction=interaction, bot_instance=self.bot, parent_view=self)
        await interaction.response.send_modal(modal)

# New Modal for Rejection Reason
class RejectReasonModal(discord.ui.Modal, title="Reject Proposal"):
    def __init__(self, proposal_id: int, original_button_interaction: discord.Interaction, bot_instance: commands.Bot, parent_view: AdminApprovalView):
        super().__init__()
        self.proposal_id = proposal_id
        self.original_button_interaction = original_button_interaction
        self.bot = bot_instance
        self.parent_view = parent_view

        self.reason_input = discord.ui.TextInput(
            label="Reason for Rejection",
            style=discord.TextStyle.paragraph,
            placeholder="Please provide a reason for rejecting this proposal.",
            required=True,
            min_length=10
        )
        self.add_item(self.reason_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer() # Defer immediately
        reason = self.reason_input.value

        success, message_content = await _perform_reject_proposal_action(interaction, self.proposal_id, reason, self.bot)

        original_embed = self.original_button_interaction.message.embeds[0] if self.original_button_interaction.message.embeds else None
        new_embed = None
        if original_embed:
            new_embed = original_embed.copy()
            if success:
                new_embed.colour = discord.Color.red()
                new_embed.set_field_at(0, name="Status", value=f"Rejected by {interaction.user.mention}", inline=True)
                new_embed.add_field(name="Rejection Reason", value=reason, inline=False)
            # Remove or update other fields as necessary
            new_embed.set_footer(text=f"Actioned by {interaction.user.display_name} at {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")

        # Disable buttons on the original view
        for item in self.parent_view.children:
            item.disabled = True

        await self.original_button_interaction.message.edit(content=message_content, embed=new_embed, view=self.parent_view)
        # await interaction.followup.send(message_content, ephemeral=True) # Send a confirmation of the modal submission


async def _perform_approve_proposal_action(interaction_or_ctx: Union[discord.Interaction, commands.Context], proposal_id: int, bot_instance: commands.Bot) -> tuple[bool, str]:
    guild = interaction_or_ctx.guild
    user = interaction_or_ctx.user if isinstance(interaction_or_ctx, discord.Interaction) else interaction_or_ctx.author

    try:
        proposal = await db.get_proposal(proposal_id)
        if not proposal:
            return False, f"❌ Proposal #{proposal_id} not found."

        if proposal['status'] not in ["Pending", "Pending Approval"]:
            return False, f"❌ Proposal #{proposal_id} is not pending approval (current status: {proposal['status']})."

        await db.update_proposal_status(proposal_id, "Voting")

        # Announce in proposals channel
        proposals_channel_name = "proposals"
        proposals_channel = await utils.get_or_create_channel(guild, proposals_channel_name, bot_instance.user.id)

        if proposals_channel:
            options = await db.get_proposal_options(proposal_id)
            option_names = options if options else ["Yes", "No"]
            proposer_member = guild.get_member(proposal['proposer_id']) or proposal['proposer_id']

            embed = utils.create_proposal_embed(
                proposal_id, proposer_member, proposal['title'], proposal['description'],
                proposal['voting_mechanism'], proposal['deadline'], "Voting", option_names,
                hyperparameters=proposal.get('hyperparameters') # Pass hyperparameters
            )
            await proposals_channel.send(content=f"🎉 Voting has started for Proposal #{proposal_id}!", embed=embed)
        else:
            print(f"Warning: Could not find '{proposals_channel_name}' channel to announce vote start for P#{proposal_id}.")


        voting_room_channel_name = "voting-room"
        voting_room_channel = await utils.get_or_create_channel(guild, voting_room_channel_name, bot_instance.user.id)
        if voting_room_channel:
            await voting_utils.update_vote_tracking(guild, proposal_id) # This sends the tracking message
            # await voting_room_channel.send(f"🗳️ Voting for Proposal #{proposal_id} ('{proposal['title']}') is now open...") # Redundant if update_vote_tracking handles it well
        else:
            print(f"Warning: Could not find '{voting_room_channel_name}' for P#{proposal_id}.")

        # Send DMs to eligible voters
        dm_info_message = "" # For appending to the final success message
        try:
            # Refetch full proposal details as 'proposal' might be stale or incomplete for DM sending
            full_proposal_details_for_dm = await db.get_proposal(proposal_id)
            if not full_proposal_details_for_dm:
                print(f"ERROR: P#{proposal_id} - Could not refetch proposal details before sending DMs.")
                dm_info_message = " (DM sending skipped: proposal details unavailable)"
            else:
                # Use the fresh details for getting eligible voters
                eligible_voters_list = await voting_utils.get_eligible_voters(guild, full_proposal_details_for_dm)

                # Fetch proposal options as a list of strings
                proposal_options_dicts = await db.get_proposal_options(proposal_id)
                # Ensure option_names_list is correctly formed (list of strings)
                option_names_list = proposal_options_dicts if proposal_options_dicts else ["Yes", "No"]

                if not eligible_voters_list:
                    print(f"INFO: P#{proposal_id} - No eligible voters found to send DMs.")
                    dm_info_message = " (No eligible voters for DMs)"
                else:
                    print(f"INFO: P#{proposal_id} - Attempting to send voting DMs to {len(eligible_voters_list)} eligible members.")
                    successful_dms_count = 0
                    failed_dms_count = 0
                    for member_to_dm in eligible_voters_list:
                        # get_eligible_voters should already filter out bots, but this is a safeguard.
                        if member_to_dm.bot:
                            print(f"DEBUG: P#{proposal_id} - Skipping DM to bot user {member_to_dm.name} ({member_to_dm.id}).")
                            continue

                        # Use the fresh full_proposal_details_for_dm for sending DMs
                        dm_sent_successfully = await voting.send_voting_dm(member_to_dm, full_proposal_details_for_dm, option_names_list)
                        if dm_sent_successfully:
                            successful_dms_count += 1
                            # Record that this voter was invited/DM'd
                            await db.add_voting_invite(proposal_id, member_to_dm.id)
                        else:
                            failed_dms_count += 1

                    dm_info_message = f" ({successful_dms_count} DMs sent, {failed_dms_count} failed)"
                    print(f"INFO: P#{proposal_id} - DM sending complete: {successful_dms_count} successful, {failed_dms_count} failed.")
        except Exception as e_dm:
            print(f"ERROR: P#{proposal_id} - An error occurred during the DM sending process: {e_dm}")
            traceback.print_exc()
            dm_info_message = " (Error during DM sending)"

        audit_channel = discord.utils.get(guild.text_channels, name="audit-log")
        if audit_channel:
            await audit_channel.send(f"✅ **Proposal Approved**: #{proposal_id} ('{proposal['title']}') approved by {user.mention}. Voting started.{dm_info_message}")

        return True, f"✅ Proposal #{proposal_id} ('{proposal['title']}') has been approved and voting has started!{dm_info_message}"

    except Exception as e:
        print(f"Error in _perform_approve_proposal_action for P#{proposal_id}: {e}")
        traceback.print_exc()
        return False, f"❌ An error occurred while approving proposal #{proposal_id}: {e}"

async def _perform_reject_proposal_action(interaction_or_ctx: Union[discord.Interaction, commands.Context], proposal_id: int, reason: str, bot_instance: commands.Bot) -> tuple[bool, str]:
    guild = interaction_or_ctx.guild
    user = interaction_or_ctx.user if isinstance(interaction_or_ctx, discord.Interaction) else interaction_or_ctx.author

    try:
        proposal = await db.get_proposal(proposal_id)
        if not proposal:
            return False, f"❌ Proposal #{proposal_id} not found."

        if proposal['status'] not in ["Pending", "Pending Approval"]:
            return False, f"❌ Proposal #{proposal_id} cannot be rejected (current status: {proposal['status']})."

        await db.update_proposal_status(proposal_id, "Rejected")

        try:
            proposer = await guild.fetch_member(proposal['proposer_id'])
            if proposer:
                await proposer.send(f"Your proposal #{proposal_id} ('{proposal['title']}') was rejected by an administrator. Reason: {reason}")
        except discord.NotFound:
            print(f"Could not find proposer {proposal['proposer_id']} to notify of rejection for P#{proposal_id}.")
        except discord.Forbidden:
            print(f"Could not DM proposer {proposal['proposer_id']} (DMs disabled/blocked) for P#{proposal_id}.")

        audit_channel = discord.utils.get(guild.text_channels, name="audit-log")
        if audit_channel:
            await audit_channel.send(f"🚫 **Proposal Rejected**: #{proposal_id} ('{proposal['title']}') rejected by {user.mention}. Reason: {reason}")

        return True, f"❌ Proposal #{proposal_id} ('{proposal['title']}') has been rejected. Reason: {reason}"

    except Exception as e:
        print(f"Error in _perform_reject_proposal_action for P#{proposal_id}: {e}")
        traceback.print_exc()
        return False, f"❌ An error occurred while rejecting proposal #{proposal_id}: {e}"

# Refactored command handlers
async def approve_proposal(ctx: commands.Context, proposal_id: int):
    """Approve a pending proposal and start voting. (Command)"""
    if not ctx.author.guild_permissions.administrator:
        await ctx.send("You do not have permission to approve proposals.")
        return
    success, message = await _perform_approve_proposal_action(ctx, proposal_id, ctx.bot)
    await ctx.send(message)

async def reject_proposal(ctx: commands.Context, proposal_id: int, *, reason: str):
    """Reject a pending proposal. (Command)"""
    if not ctx.author.guild_permissions.administrator:
        await ctx.send("You do not have permission to reject proposals.")
        return
    success, message = await _perform_reject_proposal_action(ctx, proposal_id, reason, ctx.bot)
    await ctx.send(message)
