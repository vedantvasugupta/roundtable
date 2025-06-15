import discord
from discord.ext import commands
from discord.app_commands import Choice
import asyncio
from datetime import datetime, timedelta
import json
import traceback
import re
from typing import List, Optional, Dict, Any, Union

# Local project imports
from . import db
from . import utils
from . import voting
from . import voting_utils

# Enable all intents (or specify only the necessary ones)
intents = discord.Intents.default()
intents.message_content = True  # Required for handling messages

# Initialize bot with intents
# This bot instance is problematic here. It should be defined in main.py and passed around.
# For now, keeping it as is to minimize changes, but this is a structural issue.
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

            if interaction.guild_id is None:
                await interaction.followup.send("Campaigns can only be created from within a server/guild.", ephemeral=True)
                return

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
                # Campaign is created, now it's pending approval.
                # Notify the user who submitted the modal.
                await interaction.followup.send(
                    f"🗳️ Weighted Campaign '{title}' (ID: {campaign_id}) has been submitted for admin approval. "
                    f"You will be notified once it's reviewed.",
                    ephemeral=True
                )

                # Attempt to edit the original interaction that opened the mechanism selection (if it exists and was passed)
                # This self.original_interaction is from the ProposalMechanismSelectionView (or a command)
                if self.original_interaction and not self.original_interaction.is_expired():
                    try:
                        await self.original_interaction.edit_original_response(
                            content=f"Campaign '{title}' submitted for approval. Please check your DMs or wait for an admin.",
                            view=None
                        )
                    except discord.HTTPException:
                        pass # Original interaction might have been ephemeral or already handled

                # Send notification to admins for approval
                await _send_campaign_approval_notification(interaction, campaign_id, title, description)

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

async def _send_campaign_approval_notification(interaction: discord.Interaction, campaign_id: int, title: str, description: Optional[str]):
    admin_channel_name = "proposals" # Or a new config option, e.g., "campaign-approvals"
    admin_channel = await utils.get_or_create_channel(interaction.guild, admin_channel_name, interaction.client.user.id)

    if not admin_channel:
        print(f"ERROR: Could not find or create admin channel '{admin_channel_name}' for campaign approval notification.")
        # Notify the user who submitted the campaign that admin notification failed
        try:
            await interaction.followup.send(
                "Your campaign has been submitted, but an error occurred while notifying admins. Please contact an admin directly.",
                ephemeral=True
            )
        except discord.HTTPException as e_followup:
            print(f"Error sending followup to user about admin notification failure: {e_followup}")
        return

    embed = discord.Embed(
        title=f"🆕 Campaign Submitted for Approval: '{title}'",
        description=f"**Description:**\n{description}" if description and description.strip() else "No description provided.",
        color=discord.Color.orange()
    )
    embed.add_field(name="Campaign ID", value=campaign_id, inline=False)
    embed.add_field(name="Submitted by", value=interaction.user.mention, inline=False)
    embed.set_footer(text=f"Submitted at {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")

    view = AdminCampaignApprovalView(campaign_id=campaign_id, bot_instance=interaction.client)
    try:
        await admin_channel.send(
            content="Admins, a new campaign requires approval:",
            embed=embed,
            view=view
        )
    except Exception as e_send_admin:
        print(f"ERROR: Could not send campaign approval message to admin channel '{admin_channel_name}': {e_send_admin}")
        # Notify the user who submitted the campaign that admin notification failed
        try:
            await interaction.followup.send(
                "Your campaign has been submitted, but an error occurred during admin notification. Please contact an admin directly.",
                ephemeral=True
            )
        except discord.HTTPException as e_followup_admin_fail:
            print(f"Error sending followup to user about admin notification send failure: {e_followup_admin_fail}")

# View to be sent to campaign creator upon approval, allowing them to start defining scenarios
class StartScenarioDefinitionView(discord.ui.View):
    def __init__(self, campaign_id: int, campaign_title: str, creator_id: int, bot_instance: commands.Bot):
        super().__init__(timeout=None) # Persist until creator acts or dismisses
        self.campaign_id = campaign_id
        self.campaign_title = campaign_title
        self.creator_id = creator_id
        self.bot = bot_instance # Needed for db access potentially or other utils

        self.define_scenarios_button = discord.ui.Button(
            label=f"Define Scenarios for '{self.campaign_title}'",
            style=discord.ButtonStyle.primary,
            custom_id=f"start_define_scenarios_{self.campaign_id}"
        )
        self.define_scenarios_button.callback = self.define_scenarios_callback
        self.add_item(self.define_scenarios_button)

    async def define_scenarios_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.creator_id:
            await interaction.response.send_message("This button is for the campaign creator.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True) # Defer immediately

        campaign_details = await db.get_campaign(self.campaign_id)
        if not campaign_details:
            await interaction.followup.send(f"Error: Campaign C#{self.campaign_id} not found.", ephemeral=True)
            self.define_scenarios_button.disabled = True
            self.define_scenarios_button.label = "Error: Campaign Not Found"
            if interaction.message: await interaction.message.edit(view=self)
            return

        if campaign_details['status'] != 'setup':
            await interaction.followup.send(f"Campaign C#{self.campaign_id} is not in 'setup' phase (current: {campaign_details['status']}). Cannot define scenarios now.", ephemeral=True)
            self.define_scenarios_button.disabled = True
            self.define_scenarios_button.label = f"Campaign Status: {campaign_details['status']}"
            if interaction.message: await interaction.message.edit(view=self)
            return

        next_scenario_num = campaign_details['current_defined_scenarios'] + 1
        total_scenarios = campaign_details['num_expected_scenarios']

        if next_scenario_num > total_scenarios:
            await interaction.followup.send(f"All {total_scenarios} scenarios for Campaign C#{self.campaign_id} seem to be defined already.", ephemeral=True)
            self.define_scenarios_button.disabled = True
            self.define_scenarios_button.label = "All Scenarios Defined"
            if interaction.message: await interaction.message.edit(view=self)
            return

        scenario_view = DefineScenarioView(campaign_id=self.campaign_id, next_scenario_order=next_scenario_num, total_scenarios=total_scenarios, original_interaction=interaction)
        followup_message_content = (
            f"🗳️ Campaign '{self.campaign_title}' (ID: {self.campaign_id}) is ready for scenario definition.\n"
            f"You need to define Scenario {next_scenario_num} of {total_scenarios}."
        )
        await interaction.followup.send(content=followup_message_content, view=scenario_view, ephemeral=True)

        self.define_scenarios_button.disabled = True
        self.define_scenarios_button.label = f"Scenario Definition Started"
        try:
            if interaction.message:
                 await interaction.message.edit(view=self)
        except discord.HTTPException as e:
            print(f"Error editing StartScenarioDefinitionView message: {e}")

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
                emoji="⚖️",
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
            print(f"Non-critical error: PMeV.weighted_campaign_button_callback couldn't edit original response: {e}")
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
                        content_msg = f"Scenario {self.scenario_order} ({mechanism_name.title()}) - Creation form sent."
                    await self.original_interaction.edit_original_response(content=content_msg, view=None)
                except discord.HTTPException as e:
                    print(f"Non-critical error: PMeV.mechanism_button_callback couldn't edit original response: {e}")
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
        # Determine the full title based on whether it's a campaign scenario or standalone proposal
        full_title = f"{title_prefix} {mechanism_name.title()} Proposal"
        if campaign_id and scenario_order: # Already handled by title_prefix logic from PMeV
            # title_prefix would be like "Campaign Scenario X: New"
            full_title = f"{title_prefix} {mechanism_name.title()}"
            # To avoid double "Proposal" if title_prefix is just "New", we can refine title prefix or here
            # For now, PMeV sends "Campaign Scenario X: New" as prefix, so this becomes e.g. "Campaign Scenario 1: New Plurality"
            # If just "New", it becomes "New Plurality Proposal"
            if title_prefix == "New": # Standard proposal
                 full_title = f"{title_prefix} {mechanism_name.title()} Proposal"
            else: # Campaign scenario, title_prefix includes more detail
                 full_title = f"{title_prefix} {mechanism_name.title()}"

        super().__init__(title=full_title.replace("Proposal Proposal", "Proposal").strip()) # Avoid double "Proposal"
        self.original_interaction = interaction # Interaction that opened THIS modal
        self.mechanism_name = mechanism_name
        self.campaign_id = campaign_id
        self.scenario_order = scenario_order

        self.proposal_title_input = discord.ui.TextInput(
            label="Proposal Title (context: scenario if any)",
            placeholder="Enter a concise title",
            min_length=1,
            max_length=100,
            required=True,
            style=discord.TextStyle.short
        )
        self.add_item(self.proposal_title_input)

        self.options_input = discord.ui.TextInput(
            label="Options (One Per Line)",
            placeholder="Enter each voting option on a new line.\nDefault: Yes, No (if blank)",
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

            # Construct description based on context
            if self.campaign_id and self.scenario_order:
                description = f"{self.mechanism_name.title()} proposal - Scenario {self.scenario_order} of Campaign ID {self.campaign_id}."
            else:
                description = f"{self.mechanism_name.title()} proposal."

            options_text = self.options_input.value
            deadline_str = self.deadline_input.value or "7d"

            # Options parsing
            options = [opt.strip() for opt in options_text.split('\n') if opt.strip()]
            if not options_text.strip(): # Empty or whitespace input means default Yes/No
                options = ["Yes", "No"]
            elif not options: # Non-empty input but no valid options (e.g. lines of only spaces)
                await interaction.response.send_message("Please provide valid options, or leave blank for default Yes/No.", ephemeral=True)
                return

            # Deadline parsing and validation
            deadline_seconds = utils.parse_duration(deadline_str)
            if deadline_seconds is None:
                await interaction.response.send_message(f"Invalid duration format: '{deadline_str}'. Use d, h, m (e.g., 7d, 24h, 30m).", ephemeral=True)
                return

            actual_deadline_datetime = datetime.utcnow() + timedelta(seconds=deadline_seconds)
            deadline_db_str = actual_deadline_datetime.strftime('%Y-%m-%d %H:%M:%S.%f')

            # Defer interaction if not already done
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=True, thinking=True)

            # Call _create_new_proposal_entry
            proposal_id = await _create_new_proposal_entry(
                interaction=interaction,
                title=title,
                description=description,
                mechanism_name=self.mechanism_name,
                options=options,
                deadline_db_str=deadline_db_str,
                hyperparameters=specific_hyperparameters,
                campaign_id=self.campaign_id,
                scenario_order=self.scenario_order
            )

            # User feedback is now handled by _create_new_proposal_entry

        except Exception as e:
            print(f"Error in BaseProposalModal common_on_submit: {e}")
            traceback.print_exc()
            error_message = f"An unexpected error occurred during proposal submission: {e}"
            if interaction.response.is_done():
                # If already deferred, use followup
                await interaction.followup.send(error_message, ephemeral=True)
            else:
                # If not deferred (e.g. an error happened before deferral), try to respond directly
                try:
                    await interaction.response.send_message(error_message, ephemeral=True)
                except discord.InteractionResponded:
                    # Fallback if somehow responded between check and send_message
                    await interaction.followup.send(error_message, ephemeral=True)
                except Exception as e_resp_send:
                    print(f"Further error attempting to send error message in common_on_submit: {e_resp_send}")

class PluralityProposalModal(BaseProposalModal):
    def __init__(self, interaction: discord.Interaction, mechanism_name: str, campaign_id: Optional[int] = None, scenario_order: Optional[int] = None, title_prefix: str = "New"):
        super().__init__(interaction, mechanism_name, title_prefix=title_prefix, campaign_id=campaign_id, scenario_order=scenario_order)
        # Add specific fields for Plurality
        self.allow_abstain_input = discord.ui.TextInput(
            label="Allow Abstain Votes? (yes/no/blank for yes)",
            default="yes",
            required=False,
            max_length=3
        )
        self.add_item(self.allow_abstain_input)

        self.winning_threshold_percentage_input = discord.ui.TextInput(
            label="Winning Threshold % (e.g., 40)",
            placeholder="Leave blank for simple majority. Enter 0-100.",
            required=False,
            max_length=3
        )
        self.add_item(self.winning_threshold_percentage_input)

    async def on_submit(self, interaction: discord.Interaction):
        hyperparameters = {}
        allow_abstain_str = self.allow_abstain_input.value.strip().lower()
        if allow_abstain_str in ["yes", "y", ""]:
            hyperparameters["allow_abstain"] = True
        elif allow_abstain_str in ["no", "n"]:
            hyperparameters["allow_abstain"] = False
        else:
            await interaction.response.send_message("Invalid input for 'Allow Abstain'. Please use 'yes' or 'no', or leave blank for 'yes'.", ephemeral=True)
            return

        threshold_str = self.winning_threshold_percentage_input.value.strip()
        if threshold_str:
            try:
                threshold = int(threshold_str)
                if not (0 <= threshold <= 100):
                    await interaction.response.send_message("Winning threshold must be a percentage between 0 and 100.", ephemeral=True)
                    return
                hyperparameters["winning_threshold_percentage"] = threshold
            except ValueError:
                await interaction.response.send_message("Invalid input for winning threshold. Please enter a whole number (e.g., 40).", ephemeral=True)
                return
        # If threshold_str is empty, it's a simple majority, so no hyperparameter is added for it.
        # The proposal creation or voting logic should handle this default.

        await self.common_on_submit(interaction, hyperparameters)

class BordaProposalModal(BaseProposalModal):
    def __init__(self, interaction: discord.Interaction, mechanism_name: str, campaign_id: Optional[int] = None, scenario_order: Optional[int] = None, title_prefix: str = "New"):
        super().__init__(interaction, mechanism_name, title_prefix=title_prefix, campaign_id=campaign_id, scenario_order=scenario_order)
        self.allow_abstain_input = discord.ui.TextInput(
            label="Allow Abstain Votes? (yes/no/blank for yes)",
            default="yes",
            required=False,
            max_length=3
        )
        self.add_item(self.allow_abstain_input)

    async def on_submit(self, interaction: discord.Interaction):
        hyperparameters = {}
        allow_abstain_str = self.allow_abstain_input.value.strip().lower()
        if allow_abstain_str in ["yes", "y", ""]:
            hyperparameters["allow_abstain"] = True
        elif allow_abstain_str in ["no", "n"]:
            hyperparameters["allow_abstain"] = False
        else:
            await interaction.response.send_message("Invalid input for 'Allow Abstain'. Please use 'yes' or 'no', or leave blank for 'yes'.", ephemeral=True)
            return

        # Borda currently has no other specific hyperparameters in the modal
        await self.common_on_submit(interaction, hyperparameters)

class ApprovalProposalModal(BaseProposalModal):
    def __init__(self, interaction: discord.Interaction, mechanism_name: str, campaign_id: Optional[int] = None, scenario_order: Optional[int] = None, title_prefix: str = "New"):
        super().__init__(interaction, mechanism_name, title_prefix=title_prefix, campaign_id=campaign_id, scenario_order=scenario_order)
        self.allow_abstain_input = discord.ui.TextInput(
            label="Allow Abstain Votes? (yes/no/blank for yes)",
            default="yes",
            required=False,
            max_length=3
        )
        self.add_item(self.allow_abstain_input)

    async def on_submit(self, interaction: discord.Interaction):
        hyperparameters = {}
        allow_abstain_str = self.allow_abstain_input.value.strip().lower()
        if allow_abstain_str in ["yes", "y", ""]:
            hyperparameters["allow_abstain"] = True
        elif allow_abstain_str in ["no", "n"]:
            hyperparameters["allow_abstain"] = False
        else:
            await interaction.response.send_message("Invalid input for 'Allow Abstain'. Please use 'yes' or 'no', or leave blank for 'yes'.", ephemeral=True)
            return

        # Approval voting currently has no other specific hyperparameters in the modal
        await self.common_on_submit(interaction, hyperparameters)

class RunoffProposalModal(BaseProposalModal):
    def __init__(self, interaction: discord.Interaction, mechanism_name: str, campaign_id: Optional[int] = None, scenario_order: Optional[int] = None, title_prefix: str = "New"):
        super().__init__(interaction, mechanism_name, title_prefix=title_prefix, campaign_id=campaign_id, scenario_order=scenario_order)
        self.allow_abstain_input = discord.ui.TextInput(
            label="Allow Abstain Votes? (yes/no/blank for yes)",
            default="yes",
            required=False,
            max_length=3
        )
        self.add_item(self.allow_abstain_input)

    async def on_submit(self, interaction: discord.Interaction):
        hyperparameters = {}
        allow_abstain_str = self.allow_abstain_input.value.strip().lower()
        if allow_abstain_str in ["yes", "y", ""]:
            hyperparameters["allow_abstain"] = True
        elif allow_abstain_str in ["no", "n"]:
            hyperparameters["allow_abstain"] = False
        else:
            await interaction.response.send_message("Invalid input for 'Allow Abstain'. Please use 'yes' or 'no', or leave blank for 'yes'.", ephemeral=True)
            return

        # Runoff voting currently has no other specific hyperparameters in the modal
        await self.common_on_submit(interaction, hyperparameters)

class DHondtProposalModal(BaseProposalModal):
    def __init__(self, interaction: discord.Interaction, mechanism_name: str, campaign_id: Optional[int] = None, scenario_order: Optional[int] = None, title_prefix: str = "New"):
        super().__init__(interaction, mechanism_name, title_prefix=title_prefix, campaign_id=campaign_id, scenario_order=scenario_order)
        self.allow_abstain_input = discord.ui.TextInput(
            label="Allow Abstain Votes? (yes/no/blank for yes)",
            default="yes",
            required=False,
            max_length=3
        )
        self.add_item(self.allow_abstain_input)

        self.num_seats_input = discord.ui.TextInput(
            label="Number of 'Seats' to Allocate (Winners)",
            default="1",
            required=False
        )
        self.add_item(self.num_seats_input)

    async def on_submit(self, interaction: discord.Interaction):
        hyperparameters = {}
        allow_abstain_str = self.allow_abstain_input.value.strip().lower()
        if allow_abstain_str in ["yes", "y", ""]:
            hyperparameters["allow_abstain"] = True
        elif allow_abstain_str in ["no", "n"]:
            hyperparameters["allow_abstain"] = False
        else:
            await interaction.response.send_message("Invalid input for 'Allow Abstain'. Please use 'yes' or 'no', or leave blank for 'yes'.", ephemeral=True)
            return

        num_seats_str = self.num_seats_input.value.strip()
        if num_seats_str:
            try:
                num_seats = int(num_seats_str)
                if num_seats <= 0:
                    await interaction.response.send_message("Number of seats must be a positive integer.", ephemeral=True)
                    return
                hyperparameters["num_seats"] = num_seats
            except ValueError:
                await interaction.response.send_message("Invalid input for number of seats. Please enter a whole number (e.g., 3).", ephemeral=True)
                return
        else:
            # Default to 1 seat if not specified, as per the input's default value
            hyperparameters["num_seats"] = 1

        await self.common_on_submit(interaction, hyperparameters)

# Helper function to be called by BaseProposalModal (Now _create_new_proposal_entry)
async def _create_new_proposal_entry(interaction: discord.Interaction, title: str, description: str, mechanism_name: str, options: List[str], deadline_db_str: str, hyperparameters: Optional[Dict[str, Any]] = None, campaign_id: Optional[int] = None, scenario_order: Optional[int] = None) -> Optional[int]:
    """Handles the actual proposal/scenario creation in DB and sends feedback."""
    proposal_id_for_debug = "Unknown"
    try:
        server_id_to_use = None
        guild_for_logging = "DM"
        campaign = None # Initialize campaign

        if campaign_id:
            campaign = await db.get_campaign(campaign_id) # Fetch campaign details
            if campaign and campaign.get('guild_id'):
                server_id_to_use = campaign['guild_id']
                # ... (rest of guild_id from campaign logic as before) ...
            else:
                # ... (error handling for campaign guild_id as before) ...
                return None
        else:
            server_id_to_use = interaction.guild_id
            # ... (error handling for standalone in DM as before) ...
            return None

        proposer_id = interaction.user.id
        description_to_store = description

        final_initial_status = None
        requires_approval_check_done = False # Flag to skip constitutional check if status is determined by campaign state

        if campaign_id and campaign: # Check if campaign object was fetched
            campaign_status = campaign.get('status')
            if campaign_status in ['setup', 'active']:
                final_initial_status = "ApprovedScenario"
                requires_approval_check_done = True
                requires_approval_check_done = True # No further constitutional check needed for this scenario
            # If campaign status is 'pending_approval', this scenario will also be 'Pending Approval'.
            # It will be determined by the constitutional check below, as requires_approval_check_done is False.

        if not requires_approval_check_done:
            # This path is for standalone proposals OR scenarios of campaigns that are not yet approved (e.g. campaign is 'pending_approval')
            if server_id_to_use is None:
                print(f"CRITICAL ERROR: server_id_to_use is None before fetching const_vars. P#{proposal_id_for_debug}, C#{campaign_id}")
                await interaction.followup.send("Critical error determining server context for approval rules.", ephemeral=True)
                return None
            try:
                const_vars = await db.get_constitutional_variables(server_id_to_use)
                proposal_approval_config = const_vars.get("proposal_requires_approval", {}).get("value", "true")

                is_approval_needed_by_const = False
                if isinstance(proposal_approval_config, str):
                    is_approval_needed_by_const = proposal_approval_config.lower() == "true"
                else:
                    is_approval_needed_by_const = bool(proposal_approval_config)

                if is_approval_needed_by_const:
                    final_initial_status = "Pending Approval"
                else: # Standalone and constitution says no approval needed OR scenario of pending campaign where constitution says no approval needed for general proposals
                    final_initial_status = "Voting" # For standalone. If part of pending campaign, this might be too aggressive.
                    if campaign_id: # If scenario of a pending campaign, but constitution says general proposals are auto-approved
                        # It should still be Pending Approval, tied to the campaign's fate.
                        # This implies the campaign itself would have been auto-approved if it followed this rule.
                        # For simplicity, let's assume scenarios of pending campaigns are always Pending Approval for now,
                        # unless the campaign system is designed for campaigns to also be auto-approved by this const_var.
                        # Re-evaluate this specific sub-case if campaigns can be auto-approved by const_vars.
                        # For now: override to Pending Approval if part of ANY campaign not yet setup/active.
                        final_initial_status = "Pending Approval"
                        print(f"INFO: Scenario for pending C#{campaign_id}. Overriding const_var-based 'Voting' to 'Pending Approval'.")

            except Exception as e_cv:
                print(f"Notice: Could not fetch const_vars for proposal_requires_approval for Srv#{server_id_to_use}. Defaulting to Pending Approval. Error: {e_cv}")
                final_initial_status = "Pending Approval"

        # Fallback if final_initial_status is somehow still None (should be rare)
        if final_initial_status is None:
            print(f"CRITICAL WARN: final_initial_status was None after all checks for C#{campaign_id} S#{scenario_order}. Defaulting based on campaign presence.")
            final_initial_status = "ApprovedScenario" if campaign_id else "Pending Approval"

        # The db_requires_approval_flag for db.create_proposal is now purely informational.
        # It reflects if the proposal *would have* needed approval if not for other factors (like campaign status).
        db_requires_approval_flag = (final_initial_status == "Pending Approval")

        print(f"DEBUG: _create_new_proposal_entry: server_id={server_id_to_use}, P#{proposal_id_for_debug}, C#{campaign_id}, S#{scenario_order}, determined initial_status='{final_initial_status}', db_flag_requires_approval={db_requires_approval_flag}")

        proposal_id = await db.create_proposal(
            server_id=server_id_to_use,
            proposer_id=proposer_id,
            title=title,
            description=description_to_store,
            voting_mechanism=mechanism_name,
            deadline=deadline_db_str,
            requires_approval=db_requires_approval_flag, # Informational for DB schema
            hyperparameters=hyperparameters,
            campaign_id=campaign_id,
            scenario_order=scenario_order,
            initial_status=final_initial_status # Explicitly set the status for DB insert
        )
        proposal_id_for_debug = str(proposal_id) if proposal_id else "FailedCreation"

        if not proposal_id:
            feedback_message = "❌ Failed to create proposal/scenario in the database."
            await interaction.followup.send(feedback_message, ephemeral=True)
            return None

        await db.add_proposal_options(proposal_id, options)
        print(f"DEBUG: Proposal/Scenario P#{proposal_id} options added: {options}")

        # ... (Rest of the function - user feedback, admin notifications, and CampaignControlView update - will be in the next step)

        # Placeholder for admin notification logic and user feedback
        admin_notification_needed = (final_initial_status == "Pending Approval")
        final_user_message_content = f"✅ Proposal/Scenario P#{proposal_id} ('{title}') created with status: {final_initial_status}."
        if admin_notification_needed:
            final_user_message_content += " It requires admin approval."
            # Actual admin notification to be re-added later
            print(f"INFO: Admin notification would be needed for P#{proposal_id}.")

        # Simplified user feedback for now. More detailed feedback (like DefineNextScenario view) will be re-added.
        await interaction.followup.send(final_user_message_content, ephemeral=False)

        return proposal_id

    except Exception as e:
        print(f"ERROR in _create_new_proposal_entry P#{proposal_id_for_debug}: {e}")
        traceback.print_exc()
        error_message_final = f"An error occurred while finalizing the proposal/scenario: {e}"
        if interaction and not interaction.is_expired() and interaction.response.is_done():
             await interaction.followup.send(error_message_final, ephemeral=True)
        return None

async def start_proposal_creation_flow(ctx: commands.Context):
    """Starts the new proposal creation flow (standalone or first step of campaign scenario definition)."""
    try:
        if ctx.guild is None:
            await ctx.send("The `!propose` command can only be used in a server channel, not in DMs.")
            print(f"INFO: {ctx.author} attempted to use !propose in DMs.")
            return

        print(f"🔍 {ctx.author} invoked proposal creation in {ctx.guild.name} via !propose command.")

        # For a standalone proposal initiated by !propose, campaign_id and scenario_order are None.
        # The original_interaction for the view is ctx.interaction (which is None for message commands).
        view = ProposalMechanismSelectionView(original_interaction=ctx.interaction, invoker_id=ctx.author.id, campaign_id=None, scenario_order=None)

        if ctx.interaction:
            # This case is less likely for a traditional !propose command but handled for completeness if it was triggered by another interaction.
            await ctx.interaction.response.send_message("Please select a voting mechanism for your proposal:", view=view, ephemeral=False)
            print(f"✅ Sent ProposalMechanismSelectionView to {ctx.author} via interaction response.")
        else:
            # Standard case for !propose: send a new message.
            sent_message = await ctx.send("Please select a voting mechanism for your proposal (or create a campaign):", view=view)
            view.message = sent_message # Allow the view to know about its message for timeout edits
            print(f"✅ Sent ProposalMechanismSelectionView to {ctx.author} via new message (ID: {sent_message.id}).")

    except Exception as e:
        print(f"❌ Error in start_proposal_creation_flow: {e}")
        traceback.print_exc()
        error_message = f"❌ An error occurred: {e}"
        try:
            if ctx.interaction and not ctx.interaction.response.is_done():
                await ctx.interaction.response.send_message(error_message, ephemeral=True)
            elif ctx.channel:
                await ctx.send(error_message)
            else:
                print(f"CRITICAL: Could not send error to user {ctx.author.id if ctx.author else 'Unknown User'} in start_proposal_creation_flow")
        except Exception as e_report:
            print(f"Error sending error report: {e_report}")

# New View for Admin Approval Buttons
class AdminApprovalView(discord.ui.View):
    def __init__(self, proposal_id: int, bot_instance: commands.Bot, campaign_id: Optional[int] = None, scenario_order: Optional[int] = None):
        super().__init__(timeout=None)
        self.proposal_id = proposal_id
        self.bot = bot_instance
        self.campaign_id = campaign_id # Store for context in actions/messages

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.success, custom_id="admin_approve_proposal")
    async def approve_button_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("You do not have permission to approve proposals.", ephemeral=True)
            return

        await interaction.response.defer()

        # Pass campaign context to the action handler if needed, or let it fetch from DB via proposal_id
        success, message_content = await _perform_approve_proposal_action(interaction, self.proposal_id, self.bot, campaign_id=self.campaign_id, scenario_order=self.scenario_order)

        original_embed = interaction.message.embeds[0] if interaction.message.embeds else None
        new_embed = None
        if original_embed:
            new_embed = original_embed.copy()
            status_field_index = next((i for i, field in enumerate(new_embed.fields) if field.name.lower() == "status"), 0) # default to 0 if not found

            if success:
                new_embed.colour = discord.Color.green()
                new_embed.set_field_at(status_field_index, name="Status", value=f"Approved by {interaction.user.mention}", inline=True)
            else:
                new_embed.colour = discord.Color.red()
                # Optionally update status field on failure too, or leave as is.
                # For now, if action failed, message_content will explain. Embed status remains as it was.

            action_by_text = f"Actioned by {interaction.user.display_name} at {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}"
            if self.campaign_id and self.scenario_order:
                action_by_text += f" (C:{self.campaign_id} S:{self.scenario_order})"
            new_embed.set_footer(text=action_by_text)

        for item in self.children:
            item.disabled = True

        await interaction.message.edit(content=message_content, embed=new_embed, view=self)

    @discord.ui.button(label="Reject", style=discord.ButtonStyle.danger, custom_id="admin_reject_proposal")
    async def reject_button_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("You do not have permission to reject proposals.", ephemeral=True)
            return

        modal = RejectReasonModal(proposal_id=self.proposal_id, original_button_interaction=interaction, bot_instance=self.bot, parent_view=self, campaign_id=self.campaign_id, scenario_order=self.scenario_order)
        await interaction.response.send_modal(modal)

# New Modal for Rejection Reason
class RejectReasonModal(discord.ui.Modal, title="Reject Proposal"):
    def __init__(self, proposal_id: int, original_button_interaction: discord.Interaction, bot_instance: commands.Bot, parent_view: AdminApprovalView, campaign_id: Optional[int]=None, scenario_order: Optional[int]=None):
        super().__init__()
        self.proposal_id = proposal_id
        self.original_button_interaction = original_button_interaction
        self.bot = bot_instance
        self.parent_view = parent_view
        self.campaign_id = campaign_id
        self.scenario_order = scenario_order

        self.reason_input = discord.ui.TextInput(
            label="Reason for Rejection",
            style=discord.TextStyle.paragraph,
            placeholder="Please provide a reason for rejecting this proposal.",
            required=True,
            min_length=10
        )
        self.add_item(self.reason_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        reason = self.reason_input.value

        # Pass campaign context to action handler
        success, message_content = await _perform_reject_proposal_action(interaction, self.proposal_id, reason, self.bot, campaign_id=self.campaign_id, scenario_order=self.scenario_order)

        original_embed = self.original_button_interaction.message.embeds[0] if self.original_button_interaction.message.embeds else None
        new_embed = None
        if original_embed:
            new_embed = original_embed.copy()
            status_field_index = next((i for i, field in enumerate(new_embed.fields) if field.name.lower() == "status"), 0)

            if success:
                new_embed.colour = discord.Color.red()
                new_embed.set_field_at(status_field_index, name="Status", value=f"Rejected by {interaction.user.mention}", inline=True)
                # Add reason field if not already there or update if it is (less likely for reject)
                # For simplicity, just add it if success, assuming it wasn't there for a pending proposal.
                reason_field_exists = any(field.name.lower() == "rejection reason" for field in new_embed.fields)
                if not reason_field_exists:
                    new_embed.add_field(name="Rejection Reason", value=reason, inline=False)
                else: # Update existing, though less common
                    idx = next((i for i, field in enumerate(new_embed.fields) if field.name.lower() == "rejection reason"), -1)
                    if idx != -1:
                        new_embed.set_field_at(idx, name="Rejection Reason", value=reason, inline=False)

            action_by_text = f"Actioned by {interaction.user.display_name} at {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}"
            if self.campaign_id and self.scenario_order:
                action_by_text += f" (C:{self.campaign_id} S:{self.scenario_order})"
            new_embed.set_footer(text=action_by_text)

        for item in self.parent_view.children:
            item.disabled = True

        await self.original_button_interaction.message.edit(content=message_content, embed=new_embed, view=self.parent_view)

async def _perform_approve_proposal_action(interaction_or_ctx: Union[discord.Interaction, commands.Context], proposal_id: int, bot_instance: commands.Bot, campaign_id: Optional[int]=None, scenario_order: Optional[int]=None) -> tuple[bool, str]:
    guild = interaction_or_ctx.guild
    user = interaction_or_ctx.user if isinstance(interaction_or_ctx, discord.Interaction) else interaction_or_ctx.author
    # ... (rest of the function needs to be aware of campaign_id/scenario_order for tailored logic if a proposal is part of a campaign)
    # For now, the main impact is on audit logs and notifications. Actual voting start might differ for campaigns.

    try:
        proposal = await db.get_proposal(proposal_id)
        if not proposal:
            return False, f"❌ Proposal #{proposal_id} not found."

        if proposal['status'] not in ["Pending", "Pending Approval"]:
            return False, f"❌ Proposal P#{proposal_id} (S#{scenario_order} C#{campaign_id} if applicable) is not pending approval (status: {proposal['status']})."

        # For campaign scenarios, approving them doesn't mean voting starts immediately.
        # It just means they are ready. The campaign itself needs to be activated / progress.
        # Standalone proposals go to "Voting".
        new_status = "Voting"
        action_message_suffix = "Voting has started!"
        if campaign_id: # If part of a campaign
            new_status = "ApprovedScenario" # Or a similar status indicating it's ready within the campaign
            action_message_suffix = "It is now approved and will be active when the campaign reaches this scenario."
            # We could also fetch campaign details here to see if it's the *first* scenario of a campaign that is *not yet active*,
            # and potentially offer an admin a button to "Start Campaign Now". But that's a further enhancement.

        await db.update_proposal_status(proposal_id, new_status)

        # Announce in proposals channel (if standalone and approved)
        if not campaign_id: # Only for standalone proposals that go directly to voting
            proposals_channel_name = "proposals"
            proposals_channel = await utils.get_or_create_channel(guild, proposals_channel_name, bot_instance.user.id)
            if proposals_channel:
                options = await db.get_proposal_options(proposal_id)
                option_names = options if options else ["Yes", "No"]
                proposer_member = guild.get_member(proposal['proposer_id']) or proposal['proposer_id']

                embed = utils.create_proposal_embed(
                    proposal_id, proposer_member, proposal['title'], proposal['description'],
                    proposal['voting_mechanism'], proposal['deadline'], new_status, option_names,
                    hyperparameters=proposal.get('hyperparameters'),
                    campaign_id=campaign_id, scenario_order=scenario_order # Pass for context to embed
                )
                await proposals_channel.send(content=f"🎉 Voting has started for Proposal #{proposal_id}!", embed=embed)
            else:
                print(f"Warning: Could not find '{proposals_channel_name}' channel to announce vote start for P#{proposal_id}.")

            # Update voting room (if standalone and approved)
            voting_room_channel_name = "voting-room"
            voting_room_channel = await utils.get_or_create_channel(guild, voting_room_channel_name, bot_instance.user.id)
            if voting_room_channel:
                await voting_utils.update_vote_tracking(guild, proposal_id)
            else:
                print(f"Warning: Could not find '{voting_room_channel_name}' for P#{proposal_id}.")

            # Send DMs (if standalone and approved)
            dm_info_message = ""
            try:
                full_proposal_details_for_dm = await db.get_proposal(proposal_id) # Re-fetch for latest status
                if full_proposal_details_for_dm and full_proposal_details_for_dm['status'] == "Voting":
                    eligible_voters_list = await voting_utils.get_eligible_voters(guild, full_proposal_details_for_dm)
                    proposal_options_dicts = await db.get_proposal_options(proposal_id)
                    option_names_list = proposal_options_dicts if proposal_options_dicts else ["Yes", "No"]

                    if eligible_voters_list:
                        successful_dms_count, failed_dms_count = 0, 0
                        for member_to_dm in eligible_voters_list:
                            if member_to_dm.bot: continue
                            dm_sent = await voting.send_voting_dm(member_to_dm, full_proposal_details_for_dm, option_names_list)
                            if dm_sent: successful_dms_count += 1
                            else: failed_dms_count += 1
                            if dm_sent: await db.add_voting_invite(proposal_id, member_to_dm.id)
                        dm_info_message = f" ({successful_dms_count} DMs sent, {failed_dms_count} failed)"
                    else: dm_info_message = " (No eligible voters for DMs)"
                else:
                    dm_info_message = " (DM sending skipped as proposal not in voting status)"
            except Exception as e_dm:
                print(f"ERROR: P#{proposal_id} - DM sending error: {e_dm}")
                dm_info_message = " (Error during DM sending)"
            action_message_suffix += dm_info_message
        else: # It is a campaign scenario
             # Send a notification to proposer? or just admin log?
            proposer = guild.get_member(proposal['proposer_id'])
            if proposer:
                try:
                    await proposer.send(f"👍 Your scenario '{proposal['title']}' (P#{proposal_id}) for Campaign C#{campaign_id} has been approved by an admin. It will become active when the campaign reaches this stage.")
                except discord.Forbidden:
                    print(f"Could not DM proposer {proposer.id} about scenario approval for P#{proposal_id}")

        # Audit Log
        audit_channel = discord.utils.get(guild.text_channels, name="audit-log")
        if audit_channel:
            log_message = f"✅ **Proposal Approved**: P#{proposal_id} ('{proposal['title']}') approved by {user.mention}. {action_message_suffix}"
            if campaign_id:
                log_message = f"✅ **Campaign Scenario Approved**: C#{campaign_id} S#{scenario_order} (P#{proposal_id} '{proposal['title']}') approved by {user.mention}. {action_message_suffix}"
            await audit_channel.send(log_message)

        return True, f"✅ Proposal P#{proposal_id} ('{proposal['title']}') approved. {action_message_suffix}"

    except Exception as e:
        print(f"Error in _perform_approve_proposal_action for P#{proposal_id} (C:{campaign_id} S:{scenario_order}): {e}")
        traceback.print_exc()
        return False, f"❌ An error occurred while approving P#{proposal_id}: {e}"

async def _perform_reject_proposal_action(interaction_or_ctx: Union[discord.Interaction, commands.Context], proposal_id: int, reason: str, bot_instance: commands.Bot, campaign_id: Optional[int]=None, scenario_order: Optional[int]=None) -> tuple[bool, str]:
    guild = interaction_or_ctx.guild
    user = interaction_or_ctx.user if isinstance(interaction_or_ctx, discord.Interaction) else interaction_or_ctx.author
    # ... (rest of the function needs to be aware of campaign_id/scenario_order for tailored logic)
    # ... existing code ...

# New View for Admin Campaign Approval Buttons
class AdminCampaignApprovalView(discord.ui.View):
    def __init__(self, campaign_id: int, bot_instance: commands.Bot):
        super().__init__(timeout=None) # Persist for admin action
        self.campaign_id = campaign_id
        self.bot = bot_instance

    @discord.ui.button(label="Approve Campaign", style=discord.ButtonStyle.success, custom_id="admin_approve_campaign")
    async def approve_campaign_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("You do not have permission to approve campaigns.", ephemeral=True)
            return

        # Defer the interaction from the button click itself.
        # The helper function will send a followup after processing.
        await interaction.response.defer(ephemeral=True)

        await _perform_approve_campaign_action(
            admin_interaction_for_message_edit=interaction,
            campaign_id=self.campaign_id,
            bot_instance=self.bot,
            admin_view_to_disable=self
        )

    @discord.ui.button(label="Reject Campaign", style=discord.ButtonStyle.danger, custom_id="admin_reject_campaign")
    async def reject_campaign_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("You do not have permission to reject campaigns.", ephemeral=True)
            return

        # Send the modal to get the rejection reason.
        # The modal's on_submit will call _perform_reject_campaign_action.
        # The interaction here (button click) is passed to the modal so it knows which message to edit later.
        modal = CampaignRejectReasonModal(
            campaign_id=self.campaign_id,
            original_button_interaction=interaction,
            bot_instance=self.bot,
            parent_view=self
        )
        await interaction.response.send_modal(modal)

# Modal for Campaign Rejection Reason
class CampaignRejectReasonModal(discord.ui.Modal, title="Reject Campaign"):
    def __init__(self, campaign_id: int, original_button_interaction: discord.Interaction, bot_instance: commands.Bot, parent_view: AdminCampaignApprovalView):
        super().__init__(timeout=None) # Or a reasonable timeout like 300s
        self.campaign_id = campaign_id
        self.original_button_interaction = original_button_interaction
        self.bot = bot_instance
        self.parent_view = parent_view

        self.reason_input = discord.ui.TextInput(
            label="Reason for Rejection",
            style=discord.TextStyle.paragraph,
            placeholder="Provide a reason for rejecting this campaign.",
            required=True,
            min_length=10,
            max_length=500 # Add a max length
        )
        self.add_item(self.reason_input)

    async def on_submit(self, modal_interaction: discord.Interaction):
        # This modal_interaction is from submitting the modal itself.
        # The self.original_button_interaction is from clicking the 'Reject Campaign' button.
        reason = self.reason_input.value

        # Defer the modal submission interaction immediately
        await modal_interaction.response.defer(ephemeral=True)

        # Call the helper function that will perform the rejection, notify user, and update admin message.
        # The helper function will use self.original_button_interaction to edit the message with the buttons.
        await _perform_reject_campaign_action(
            admin_interaction_for_message_edit=self.original_button_interaction,
            campaign_id=self.campaign_id,
            reason=reason,
            bot_instance=self.bot,
            admin_view_to_disable=self.parent_view,
            rejecting_admin_user=modal_interaction.user # User who filled out and submitted the modal
        )
        # Optionally, followup to the modal submission if needed, but primary feedback is editing the button message.
        # await modal_interaction.followup.send("Rejection processed.", ephemeral=True)

async def _perform_approve_campaign_action(admin_interaction_for_message_edit: discord.Interaction, campaign_id: int, bot_instance: commands.Bot, admin_view_to_disable: AdminCampaignApprovalView):
    guild = admin_interaction_for_message_edit.guild
    admin_user = admin_interaction_for_message_edit.user

    approved = await db.approve_campaign(campaign_id, admin_user.id)
    if not approved:
        # Check if the interaction is still valid before sending a followup
        try:
            if not admin_interaction_for_message_edit.response.is_done():
                # If for some reason defer wasn't called or failed, respond directly if possible (rare for this flow)
                await admin_interaction_for_message_edit.response.send_message(f"Campaign C#{campaign_id} could not be approved. It might have been actioned already, is not in 'pending_approval' state, or an error occurred.", ephemeral=True)
            else:
                await admin_interaction_for_message_edit.followup.send(f"Campaign C#{campaign_id} could not be approved. It might have been actioned already, is not in 'pending_approval' state, or an error occurred.", ephemeral=True)
        except discord.NotFound: # Handles the "Unknown Interaction" case
            print(f"WARN: Interaction for approving C#{campaign_id} was not found. Campaign approval by {admin_user.id} might have failed or was a duplicate action.")
        except discord.HTTPException as e:
            print(f"WARN: HTTP error sending followup for C#{campaign_id} approval failure: {e}")
        return

    campaign_data = await db.get_campaign(campaign_id)
    if not campaign_data:
        await admin_interaction_for_message_edit.followup.send(f"Campaign C#{campaign_id} data not found after approval attempt.", ephemeral=True)
        return

    # Notify Creator
    creator = await guild.fetch_member(campaign_data['creator_id']) # Use fetch_member for robustness
    if creator:
        try:
            dm_view = StartScenarioDefinitionView(campaign_id, campaign_data['title'], creator.id, bot_instance)
            await creator.send(
                f"🎉 Your campaign '{campaign_data['title']}' (ID: {campaign_id}) has been approved by an admin!\n"
                f"You can now start defining its scenarios. A management panel has also been posted in the server.",
                view=dm_view
            )
        except discord.Forbidden:
            print(f"Could not DM campaign creator U#{creator.id} about approval for C#{campaign_id}.")
        except Exception as e_dm:
            print(f"Error sending approval DM to U#{creator.id} for C#{campaign_id}: {e_dm}")

    # --- NEW: Post to Campaign Management Channel ---
    campaign_mgmt_channel_name = utils.CHANNELS.get("campaign_management", "campaign-management") # Ensure CHANNELS is accessible
    campaign_mgmt_channel = discord.utils.get(guild.text_channels, name=campaign_mgmt_channel_name)

    if campaign_mgmt_channel:
        control_view = CampaignControlView(campaign_id, bot_instance)
        # Initial update of button states based on current (just approved) campaign state
        await control_view.update_button_states()

        embed_title = f"Campaign Management: '{campaign_data['title']}' (ID: C#{campaign_id})"
        embed_description = f"**Creator:** {creator.mention if creator else f'ID: {campaign_data['creator_id']}'}\n"
        embed_description += f"**Description:** {campaign_data['description'] or 'Not provided.'}\n"
        embed_description += f"**Total Scenarios Expected:** {campaign_data['num_expected_scenarios']}\n"
        embed_description += f"**Currently Defined:** {campaign_data['current_defined_scenarios']}"

        campaign_control_embed = discord.Embed(
            title=embed_title,
            description=embed_description,
            color=discord.Color.blue() # Blue for neutral/management
        )
        campaign_control_embed.add_field(name="Status", value=campaign_data['status'].title(), inline=True)
        campaign_control_embed.set_footer(text=f"Campaign created: {utils.format_timestamp_for_display(campaign_data['creation_timestamp'])}")

        try:
            control_message = await campaign_mgmt_channel.send(embed=campaign_control_embed, view=control_view)
            await db.set_campaign_control_message_id(campaign_id, control_message.id)
            print(f"DEBUG: Posted control message for C#{campaign_id} to #{campaign_mgmt_channel.name} (Msg ID: {control_message.id})")
        except Exception as e_post_control:
            print(f"ERROR: Could not post campaign control message for C#{campaign_id}: {e_post_control}")
            # Inform admin who approved, if possible
            await admin_interaction_for_message_edit.followup.send(f"Warning: Campaign C#{campaign_id} approved, but failed to post management panel: {e_post_control}", ephemeral=True)
    else:
        print(f"ERROR: Campaign management channel '{campaign_mgmt_channel_name}' not found for C#{campaign_id}.")
        await admin_interaction_for_message_edit.followup.send(f"Warning: Campaign C#{campaign_id} approved, but '{campaign_mgmt_channel_name}' channel not found.", ephemeral=True)
    # --- END NEW ---

    # Update Admin Message
    original_embed = admin_interaction_for_message_edit.message.embeds[0]
    new_embed = original_embed.copy()
    new_embed.title = f"✅ Campaign Approved: '{campaign_data['title']}'"
    new_embed.colour = discord.Color.green()
    # Ensure 'Status' field exists or add it. For simplicity, let's assume it's added if not present, or updated.
    status_field_found = False
    for i, field in enumerate(new_embed.fields):
        if field.name.lower() == "status":
            new_embed.set_field_at(i, name="Status", value=f"Approved by {admin_user.mention}", inline=False)
            status_field_found = True
            break
    if not status_field_found:
        new_embed.add_field(name="Status", value=f"Approved by {admin_user.mention}", inline=False)

    # Update footer or add timestamp field
    action_by_text = f"Approved by {admin_user.display_name} at {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}"
    new_embed.set_footer(text=f"{action_by_text} | Campaign ID: {campaign_id}")

    for item in admin_view_to_disable.children:
        item.disabled = True

    try:
        await admin_interaction_for_message_edit.message.edit(content="Campaign approval processed.", embed=new_embed, view=admin_view_to_disable)
    except discord.NotFound:
        print(f"WARN: Original message for C#{campaign_id} approval view not found when trying to edit.")
    except discord.HTTPException as e:
        print(f"WARN: HTTP error editing original message for C#{campaign_id} approval view: {e}")

    # Audit Log
    audit_channel = discord.utils.get(guild.text_channels, name="audit-log")
    if audit_channel:
        await audit_channel.send(f"✅ **Campaign Approved**: C#{campaign_id} ('{campaign_data['title']}') approved by {admin_user.mention}.")

    try:
        await admin_interaction_for_message_edit.followup.send(f"Campaign C#{campaign_id} approved.", ephemeral=True)
    except discord.NotFound: # Handles the "Unknown Interaction" case for the final followup
        print(f"WARN: Interaction for C#{campaign_id} approval final followup was not found.")
    except discord.HTTPException as e:
        print(f"WARN: HTTP error sending final followup for C#{campaign_id} approval: {e}")

async def _perform_reject_campaign_action(admin_interaction_for_message_edit: discord.Interaction, campaign_id: int, reason: str, bot_instance: commands.Bot, admin_view_to_disable: AdminCampaignApprovalView, rejecting_admin_user: discord.User):
    guild = admin_interaction_for_message_edit.guild

    rejected = await db.reject_campaign(campaign_id, rejecting_admin_user.id, reason)
    if not rejected:
        try:
            if not admin_interaction_for_message_edit.response.is_done():
                await admin_interaction_for_message_edit.response.send_message(f"Campaign C#{campaign_id} could not be rejected. It might have been actioned already, is not in 'pending_approval' state, or an error occurred.", ephemeral=True)
            else:
                await admin_interaction_for_message_edit.followup.send(f"Campaign C#{campaign_id} could not be rejected. It might have been actioned already, is not in 'pending_approval' state, or an error occurred.", ephemeral=True)
        except discord.NotFound:
            print(f"WARN: Interaction for rejecting C#{campaign_id} was not found. Campaign rejection by {rejecting_admin_user.id} might have failed or was a duplicate action.")
        except discord.HTTPException as e:
            print(f"WARN: HTTP error sending followup for C#{campaign_id} rejection failure: {e}")
        return

    campaign_data = await db.get_campaign(campaign_id)
    if not campaign_data: # Should still exist even if rejected
        await admin_interaction_for_message_edit.followup.send(f"Campaign C#{campaign_id} data not found after rejection attempt.", ephemeral=True)
        return

    # Notify Creator
    creator = await guild.fetch_member(campaign_data['creator_id'])
    if creator:
        try:
            await creator.send(
                f"❌ Your campaign '{campaign_data['title']}' (ID: {campaign_id}) has been rejected by an admin.\n"
                f"Reason: {reason}"
            )
        except discord.Forbidden:
            print(f"Could not DM campaign creator U#{creator.id} about rejection for C#{campaign_id}.")
        except Exception as e_dm:
            print(f"Error sending rejection DM to U#{creator.id} for C#{campaign_id}: {e_dm}")

    # Update Admin Message
    original_embed = admin_interaction_for_message_edit.message.embeds[0]
    new_embed = original_embed.copy()
    new_embed.title = f"❌ Campaign Rejected: '{campaign_data['title']}'"
    new_embed.colour = discord.Color.red()

    status_field_found = False
    for i, field in enumerate(new_embed.fields):
        if field.name.lower() == "status":
            new_embed.set_field_at(i, name="Status", value=f"Rejected by {rejecting_admin_user.mention}", inline=False)
            status_field_found = True
            break
    if not status_field_found:
        new_embed.add_field(name="Status", value=f"Rejected by {rejecting_admin_user.mention}", inline=False)

    reason_field_found = False
    for i, field in enumerate(new_embed.fields):
        if field.name.lower() == "rejection reason":
            new_embed.set_field_at(i, name="Rejection Reason", value=reason, inline=False)
            reason_field_found = True
            break
    if not reason_field_found:
        new_embed.add_field(name="Rejection Reason", value=reason, inline=False)

    new_embed.set_footer(text=f"Rejected by {rejecting_admin_user.display_name} at {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')} | Campaign ID: {campaign_id}")

    for item in admin_view_to_disable.children:
        item.disabled = True

    try:
        await admin_interaction_for_message_edit.message.edit(content="Campaign rejection processed.", embed=new_embed, view=admin_view_to_disable)
    except discord.NotFound:
        print(f"WARN: Original message for C#{campaign_id} rejection view not found when trying to edit.")
    except discord.HTTPException as e:
        print(f"WARN: HTTP error editing original message for C#{campaign_id} rejection view: {e}")

    # Audit Log
    audit_channel = discord.utils.get(guild.text_channels, name="audit-log")
    if audit_channel:
        await audit_channel.send(f"❌ **Campaign Rejected**: C#{campaign_id} ('{campaign_data['title']}') rejected by {rejecting_admin_user.mention}. Reason: {reason}")

    # If the modal submitted successfully, its defer means no explicit followup is strictly needed from it.
    # The admin who clicked the "Reject" button on the admin message gets this.
    try:
        await admin_interaction_for_message_edit.followup.send(f"Campaign C#{campaign_id} rejected.", ephemeral=True)
    except discord.NotFound: # Handles the "Unknown Interaction" case for the final followup
        print(f"WARN: Interaction for C#{campaign_id} rejection final followup was not found.")
    except discord.HTTPException as e:
        print(f"WARN: HTTP error sending final followup for C#{campaign_id} rejection: {e}")

# =============================
# 🔹 CAMPAIGN CONTROL VIEW (NEW)
# =============================

class CampaignControlView(discord.ui.View):
    def __init__(self, campaign_id: int, bot_instance: commands.Bot):
        super().__init__(timeout=None) # Persistent view
        self.campaign_id = campaign_id
        self.bot = bot_instance

        # Button to define the next scenario
        self.define_scenario_button = discord.ui.Button(
            label="Define Next Scenario",
            style=discord.ButtonStyle.primary,
            custom_id=f"campaign_control_define_scenario_{self.campaign_id}",
            emoji="📝"
        )
        self.define_scenario_button.callback = self.define_scenario_callback
        self.add_item(self.define_scenario_button)

        # Button to start the campaign or the next scenario
        self.start_next_button = discord.ui.Button(
            label="Start Campaign", # Initial label
            style=discord.ButtonStyle.success,
            custom_id=f"campaign_control_start_next_{self.campaign_id}",
            emoji="▶️"
        )
        self.start_next_button.callback = self.start_next_callback
        self.add_item(self.start_next_button)

        # Add more buttons later if needed (e.g., Pause, Archive)
        # self.update_button_states() # Call a method to set initial button states based on campaign status

    async def update_button_states(self, interaction: Optional[discord.Interaction] = None):
        """Dynamically update button labels and enabled/disabled states based on campaign state."""
        campaign = await db.get_campaign(self.campaign_id)
        if not campaign:
            self.define_scenario_button.disabled = True
            self.define_scenario_button.label = "Error: Campaign Not Found"
            self.start_next_button.disabled = True
            self.start_next_button.label = "Error: Campaign Not Found"
            # if interaction and interaction.message: await interaction.message.edit(view=self)
            return

        # Define Scenario Button Logic
        if campaign['current_defined_scenarios'] >= campaign['num_expected_scenarios']:
            self.define_scenario_button.disabled = True
            self.define_scenario_button.label = "All Scenarios Defined"
        else:
            self.define_scenario_button.disabled = False
            self.define_scenario_button.label = f"Define Scenario {campaign['current_defined_scenarios'] + 1}/{campaign['num_expected_scenarios']}"

        # Start/Next Button Logic
        if campaign['status'] == 'setup':
            self.start_next_button.label = "Start Campaign"
            # Enable if at least one scenario is defined
            self.start_next_button.disabled = not (campaign['current_defined_scenarios'] > 0)
        elif campaign['status'] == 'active':
            # Placeholder: Need logic to check if current scenario is complete to enable "Start Next Scenario"
            # For now, assume if active, we might be starting the *next* one if previous is done.
            # This needs more robust checking of individual scenario statuses.
            self.start_next_button.label = "Start Next Scenario"
            # Simplified: disable if all defined scenarios might have been run or no more defined.
            # This will be refined in handle_start_campaign_or_next_scenario
            # For now, enable if not all scenarios are done yet.
            # We need to track which scenario is currently active for the campaign.
            # A simple check for now: if there are more scenarios defined than (e.g.) an assumed current_active_scenario_order
            self.start_next_button.disabled = False # Placeholder logic
        elif campaign['status'] in ['completed', 'archived', 'rejected']:
            self.start_next_button.disabled = True
            self.start_next_button.label = f"Campaign {campaign['status'].title()}"
        else: # e.g. pending_approval (shouldn't happen for this view)
            self.start_next_button.disabled = True
            self.start_next_button.label = "Campaign Not Ready"

        # If an interaction is provided, edit the message. Otherwise, the view is just updated for next send.
        if interaction and interaction.message and interaction.response.is_done(): # Ensure response isn't already done by button callback
            try:
                await interaction.message.edit(view=self)
            except discord.HTTPException as e:
                print(f"Error updating CampaignControlView buttons via interaction: {e}")
        elif interaction and interaction.message and not interaction.response.is_done():
             # If response not done, the button callback itself should handle the edit or followup
             pass # Callback will edit


    async def define_scenario_callback(self, interaction: discord.Interaction):
        campaign = await db.get_campaign(self.campaign_id)
        if not campaign:
            await interaction.response.send_message("Error: Campaign not found.", ephemeral=True)
            return

        is_creator = interaction.user.id == campaign['creator_id']
        is_admin = interaction.user.guild_permissions.administrator

        if not (is_creator or is_admin):
            await interaction.response.send_message("You do not have permission to define scenarios for this campaign.", ephemeral=True)
            return

        if campaign['current_defined_scenarios'] >= campaign['num_expected_scenarios']:
            await interaction.response.send_message("All expected scenarios for this campaign have already been defined.", ephemeral=True)
            return

        next_scenario_num = campaign['current_defined_scenarios'] + 1

        # Launch the ProposalMechanismSelectionView for scenario definition
        # The original_interaction for PMeV will be this button interaction.
        mechanism_view = ProposalMechanismSelectionView(
            original_interaction=interaction, # This interaction
            invoker_id=interaction.user.id,
            campaign_id=self.campaign_id,
            scenario_order=next_scenario_num
        )
        await interaction.response.send_message(
            f"Defining Scenario {next_scenario_num} for Campaign '{campaign["title"]}' (ID: {self.campaign_id}).\nSelect the voting mechanism for this scenario:",
            view=mechanism_view,
            ephemeral=True # Scenario definition flow usually starts ephemerally for the definer
        )
        # No need to update this CampaignControlView message from here, PMeV is ephemeral.
        # The _create_new_proposal_entry will handle updating this control view after scenario creation.

    async def start_next_callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True) # Acknowledge, actual response will be complex

        campaign = await db.get_campaign(self.campaign_id)
        if not campaign:
            await interaction.followup.send("Error: Campaign not found.", ephemeral=True)
            return

        is_creator = interaction.user.id == campaign['creator_id']
        is_admin = interaction.user.guild_permissions.administrator

        if not (is_creator or is_admin):
            await interaction.followup.send("You do not have permission to start/progress this campaign.", ephemeral=True)
            return

        # Call the main handler function (to be created)
        # This function will handle logic for starting campaign OR starting next scenario
        # It will also update the original CampaignControlView message
        success, message = await handle_start_campaign_or_next_scenario(interaction, self.campaign_id, self.bot)

        if success:
            await interaction.followup.send(f"✅ {message}", ephemeral=True)
        else:
            await interaction.followup.send(f"❌ {message}", ephemeral=True)

        # The handle_start_campaign_or_next_scenario should update the original control message
        # by fetching it using campaign['control_message_id'] and then editing it with an updated view.
        # This is crucial because the current interaction is ephemeral.

# Placeholder for the handler function (to be fully implemented in proposals.py or campaign_utils.py)
async def handle_start_campaign_or_next_scenario(interaction: discord.Interaction, campaign_id: int, bot: commands.Bot) -> tuple[bool, str]:
    guild = interaction.guild
    if not guild:
        print(f"ERROR: handle_start_campaign_or_next_scenario C#{campaign_id} called without guild context.")
        return False, "Internal error: Guild context not found."

    campaign = await db.get_campaign(campaign_id)
    if not campaign:
        return False, f"Campaign C#{campaign_id} not found."

    campaign_control_message_id = campaign.get('control_message_id')
    campaign_mgmt_channel_name = utils.CHANNELS.get("campaign_management", "campaign-management")
    # Ensure guild.text_channels is used, not interaction.guild.text_channels if guild is already defined
    campaign_mgmt_channel = discord.utils.get(guild.text_channels, name=campaign_mgmt_channel_name)
    original_control_message = None

    if campaign_mgmt_channel and campaign_control_message_id:
        try:
            original_control_message = await campaign_mgmt_channel.fetch_message(campaign_control_message_id)
        except discord.NotFound:
            print(f"ERROR: Control message {campaign_control_message_id} for C#{campaign_id} not found in #{campaign_mgmt_channel.name if campaign_mgmt_channel else 'unknown_channel'}.")
        except discord.Forbidden:
            print(f"ERROR: Bot lacks permissions to fetch control message {campaign_control_message_id} for C#{campaign_id} in #{campaign_mgmt_channel.name if campaign_mgmt_channel else 'unknown_channel'}.")
        except Exception as e_fetch_msg:
            print(f"ERROR: Fetching control message for C#{campaign_id}: {e_fetch_msg}")

    async def _update_control_message(status_message: str, color: discord.Color = discord.Color.blue()):
        if not original_control_message:
            print(f"WARN: _update_control_message called for C#{campaign_id} but original_control_message is None. Cannot update.")
            return

        current_campaign_data_for_view = await db.get_campaign(campaign_id)
        if not current_campaign_data_for_view:
            print(f"ERROR: _update_control_message could not refetch C#{campaign_id} for view update.")
            try:
                error_embed = discord.Embed(title=f"Campaign C#{campaign_id} - Error", description="Control panel update failed: Could not load fresh campaign data.", color=discord.Color.red())
                await original_control_message.edit(embed=error_embed, view=None)
            except Exception as e_err_edit:
                print(f"ERROR: Failed to edit control message to show error state for C#{campaign_id}: {e_err_edit}")
            return

        new_view = CampaignControlView(campaign_id, bot) # Create a new view instance to get fresh button states

        # Explicitly set button states based on current_campaign_data_for_view
        if current_campaign_data_for_view['current_defined_scenarios'] >= current_campaign_data_for_view['num_expected_scenarios']:
            new_view.define_scenario_button.disabled = True
            new_view.define_scenario_button.label = "All Scenarios Defined"
        else:
            new_view.define_scenario_button.disabled = False
            new_view.define_scenario_button.label = f"Define Scenario {current_campaign_data_for_view['current_defined_scenarios'] + 1}/{current_campaign_data_for_view['num_expected_scenarios']}"

        if current_campaign_data_for_view['status'] == 'setup':
            new_view.start_next_button.label = "Start Campaign"
            new_view.start_next_button.disabled = not (current_campaign_data_for_view['current_defined_scenarios'] > 0)
        elif current_campaign_data_for_view['status'] == 'active':
            # This requires checking specific scenarios associated with the campaign
            proposals_in_campaign_for_view = [p for p in await db.get_server_proposals(guild.id) if p.get('campaign_id') == campaign_id]
            active_scenarios_for_view = [p for p in proposals_in_campaign_for_view if p['status'] == 'Voting']

            if active_scenarios_for_view:
                new_view.start_next_button.label = "Scenario Active"
                new_view.start_next_button.disabled = True
            else:
                closed_scenarios_for_view = sorted([p for p in proposals_in_campaign_for_view if p['status'] == 'Closed'], key=lambda x: x.get('scenario_order', 0), reverse=True)
                last_closed_order_for_view = 0
                if closed_scenarios_for_view:
                    last_closed_order_for_view = closed_scenarios_for_view[0].get('scenario_order', 0)

                if last_closed_order_for_view >= current_campaign_data_for_view['num_expected_scenarios']:
                    new_view.start_next_button.label = "All Scenarios Done"
                    new_view.start_next_button.disabled = True
                elif current_campaign_data_for_view['current_defined_scenarios'] > last_closed_order_for_view:
                     new_view.start_next_button.label = f"Start Scenario {last_closed_order_for_view + 1}"
                     new_view.start_next_button.disabled = False # Ready for next
                else:
                    new_view.start_next_button.label = f"Define S{last_closed_order_for_view + 1} First"
                    new_view.start_next_button.disabled = True

        elif current_campaign_data_for_view['status'] in ['completed', 'archived', 'rejected']:
            new_view.define_scenario_button.disabled = True
            new_view.start_next_button.disabled = True
            new_view.start_next_button.label = f"Campaign {current_campaign_data_for_view['status'].title()}"
        else:
            new_view.define_scenario_button.disabled = True
            new_view.start_next_button.disabled = True
            new_view.start_next_button.label = "Campaign Not Ready"

        # Fetch creator for the embed
        creator = None
        try:
            creator = await guild.fetch_member(current_campaign_data_for_view['creator_id'])
        except discord.NotFound:
            print(f"WARN: Creator user ID {current_campaign_data_for_view['creator_id']} not found in guild for C#{campaign_id} embed.")

        embed_title = f"Campaign Management: '{current_campaign_data_for_view['title']}' (ID: C#{campaign_id})"
        embed_description = f"**Creator:** {creator.mention if creator else f'User ID: {current_campaign_data_for_view['creator_id']}'}\n"
        embed_description += f"**Description:** {current_campaign_data_for_view['description'] or 'Not provided.'}\n"
        embed_description += f"**Total Scenarios Expected:** {current_campaign_data_for_view['num_expected_scenarios']}\n"
        embed_description += f"**Currently Defined:** {current_campaign_data_for_view['current_defined_scenarios']}"

        new_embed = discord.Embed(title=embed_title, description=embed_description, color=color)
        new_embed.add_field(name="Status", value=current_campaign_data_for_view['status'].title(), inline=True)
        new_embed.add_field(name="Last Action Result", value=status_message, inline=False)
        new_embed.set_footer(text=f"Last updated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
        try:
            await original_control_message.edit(embed=new_embed, view=new_view)
        except Exception as e_edit_msg:
            print(f"ERROR: Failed to edit control message for C#{campaign_id} with new view/embed: {e_edit_msg}")

    # --- Start Campaign Logic (status 'setup') ---
    # Actual logic for starting campaign / next scenario will be added here in the next step
    if campaign['status'] == 'setup':
        if campaign['current_defined_scenarios'] == 0:
            await _update_control_message("Cannot start: No scenarios defined yet.", discord.Color.orange())
            return False, "Cannot start campaign: No scenarios have been defined yet."

        proposals_in_guild = await db.get_server_proposals(campaign['guild_id'])
        first_scenario = next((p for p in proposals_in_guild if p.get('campaign_id') == campaign_id and p.get('scenario_order') == 1), None)

        if not first_scenario:
            err_msg = f"Error: First scenario (order 1) not found for C#{campaign_id}. Please define it using the control panel."
            await _update_control_message(err_msg, discord.Color.red())
            return False, err_msg

        # Scenarios should be created with a status like 'ApprovedScenario' if the campaign is already 'setup'
        if first_scenario['status'] not in ['ApprovedScenario', 'Voting']: # 'Voting' for idempotency
            msg = f"First scenario P#{first_scenario['proposal_id']} ('{first_scenario['title']}') is not ready (status: {first_scenario['status']}). It might require admin action or re-definition."
            await _update_control_message(msg, discord.Color.orange())
            return False, msg

        await db.update_campaign_status(campaign_id, 'active')
        await db.update_proposal_status(first_scenario['proposal_id'], 'Voting')

        # Announce campaign start and first scenario voting
        try:
            proposals_channel_name = utils.CHANNELS.get("proposals", "proposals")
            proposals_channel = discord.utils.get(guild.text_channels, name=proposals_channel_name)
            if proposals_channel:
                options = await db.get_proposal_options(first_scenario['proposal_id'])
                proposer_member = None
                try:
                    proposer_member = await guild.fetch_member(first_scenario['proposer_id']) # Proposer of scenario is campaign creator
                except discord.NotFound:
                     print(f"WARN: Proposer U#{first_scenario['proposer_id']} for S1 P#{first_scenario['proposal_id']} C#{campaign_id} not found.")

                # Create embed for the first scenario now starting
                # Ensure utils.create_proposal_embed is robust and uses full proposal_data
                scenario_embed = utils.create_proposal_embed(
                    proposal_data=first_scenario,
                    proposer=proposer_member,
                    status='Voting', # Explicitly set status for embed
                    options=options,
                    bot_instance=bot, # Pass bot instance
                    campaign_data=campaign # Pass current campaign data
                )
                await proposals_channel.send(content=f"📢 Campaign '{campaign['title']}' (C#{campaign_id}) is now ACTIVE!\nScenario 1: '{first_scenario['title']}' (P#{first_scenario['proposal_id']}) is open for voting!", embed=scenario_embed)
            else:
                print(f"WARN: Proposals channel '{proposals_channel_name}' not found for C#{campaign_id} S1 announcement.")

            voting_room_channel_name = utils.CHANNELS.get("voting", "voting-room")
            voting_room_channel = discord.utils.get(guild.text_channels, name=voting_room_channel_name)
            if voting_room_channel:
                await voting_utils.update_vote_tracking(bot, guild, first_scenario) # Pass bot and full scenario dict
            else:
                print(f"WARN: Voting room '{voting_room_channel_name}' not found for C#{campaign_id} S1 tracking.")

            # Send DMs for the first scenario
            eligible_voters_list = await voting_utils.get_eligible_voters(guild, first_scenario) # Pass full scenario dict
            scenario_options_for_dm = await db.get_proposal_options(first_scenario['proposal_id'])
            if eligible_voters_list:
                dm_count = 0
                for member_to_dm in eligible_voters_list:
                    if member_to_dm.bot: continue
                    dm_sent = await voting.send_voting_dm(bot, member_to_dm, first_scenario, scenario_options_for_dm) # Pass bot
                    if dm_sent:
                        await db.add_voting_invite(first_scenario['proposal_id'], member_to_dm.id)
                        dm_count +=1
                print(f"INFO: Sent {dm_count} DMs for C#{campaign_id} S1 P#{first_scenario['proposal_id']}.")
        except Exception as e_announce:
            print(f"ERROR announcing start of C#{campaign_id} S1 (P#{first_scenario['proposal_id']}): {e_announce}")
            # Campaign is started, announcements might have failed. Log and continue.

        status_msg = f"Campaign '{campaign["title"]}' started successfully! Voting for Scenario 1: '{first_scenario['title']}' is now active."
        await _update_control_message(status_msg, discord.Color.green())
        return True, status_msg

    elif campaign['status'] == 'active':
        # Logic to start the next scenario if the campaign is already active.
        proposals_in_campaign = [p for p in await db.get_server_proposals(campaign['guild_id']) if p.get('campaign_id') == campaign_id]
        if not proposals_in_campaign:
            await _update_control_message("No scenarios found for this active campaign. Cannot proceed.", discord.Color.red())
            return False, "No scenarios found for this active campaign."

        active_voting_scenarios = [p for p in proposals_in_campaign if p['status'] == 'Voting']
        if active_voting_scenarios:
            active_titles = ", ".join([f"'{s['title']}' (S#{s['scenario_order']})" for s in active_voting_scenarios])
            msg = f"Cannot start a new scenario yet. Scenario(s) {active_titles} are still in 'Voting' status."
            await _update_control_message(msg, discord.Color.orange())
            return False, msg

        # Find the latest scenario that was 'Closed' or 'Voting' (in case of immediate succession desire before closure task runs)
        # Prefer 'Closed' scenarios to determine the next one.
        closed_scenarios = sorted([p for p in proposals_in_campaign if p['status'] == 'Closed'], key=lambda x: x.get('scenario_order', 0), reverse=True)

        last_progressed_order = 0
        if closed_scenarios:
            last_progressed_order = closed_scenarios[0].get('scenario_order', 0)
        else:
            # If no scenarios are closed, it implies either the first scenario hasn't started (covered by 'setup' block)
            # or an issue. For 'active' state, we expect at least S1 to have been processed or be in an error state.
            # This path implies an attempt to start S1 again or an inconsistent state. For now, let's assume S1 was handled by 'setup'.
            # If we reach here and no scenarios are closed, it might be an edge case or the campaign just started and S1 isn't closed yet.
            # The check for active_voting_scenarios should catch if S1 is still voting.
            # If S1 was just started and this button is pressed again, we need to rely on active_voting_scenarios check.
            pass # No closed scenarios, so next should be 1, but that's handled by 'setup' normally or active_voting_scenarios.

        next_scenario_order_to_start = last_progressed_order + 1

        if next_scenario_order_to_start > campaign['num_expected_scenarios']:
            # All expected scenarios have been processed and closed.
            await db.update_campaign_status(campaign_id, 'completed')
            msg = f"All {campaign['num_expected_scenarios']} scenarios for campaign '{campaign['title']}' have been completed. Campaign finished!"
            await _update_control_message(msg, discord.Color.gold())
            return True, msg

        next_scenario_to_start = next((p for p in proposals_in_campaign if p.get('scenario_order') == next_scenario_order_to_start), None)

        if not next_scenario_to_start:
            if campaign['current_defined_scenarios'] < next_scenario_order_to_start <= campaign['num_expected_scenarios']:
                 msg = f"Scenario {next_scenario_order_to_start} for campaign '{campaign['title']}' is not defined yet. Please use 'Define Next Scenario' first."
                 await _update_control_message(msg, discord.Color.orange())
                 return False, msg
            else: # Should be caught by num_expected_scenarios check or implies data/definition issue
                 msg = f"Error: Next scenario (order {next_scenario_order_to_start}) for C#{campaign_id} could not be found or is beyond expected total. Please check campaign setup."
                 await _update_control_message(msg, discord.Color.red())
                 return False, msg

        if next_scenario_to_start['status'] not in ['ApprovedScenario', 'Voting']: # 'Voting' for idempotency
            msg = f"Next scenario S#{next_scenario_order_to_start} ('{next_scenario_to_start['title']}') for C#{campaign_id} is not ready (status: {next_scenario_to_start['status']}). It may require admin action or re-definition."
            await _update_control_message(msg, discord.Color.orange())
            return False, msg

        # Start the next scenario
        await db.update_proposal_status(next_scenario_to_start['proposal_id'], 'Voting')

        # Announce the next scenario's voting period
        try:
            proposals_channel_name = utils.CHANNELS.get("proposals", "proposals")
            proposals_channel = discord.utils.get(guild.text_channels, name=proposals_channel_name)
            if proposals_channel:
                options = await db.get_proposal_options(next_scenario_to_start['proposal_id'])
                proposer_member = None
                try:
                     proposer_member = await guild.fetch_member(next_scenario_to_start['proposer_id'])
                except discord.NotFound:
                    print(f"WARN: Proposer U#{next_scenario_to_start['proposer_id']} for S{next_scenario_order_to_start} P#{next_scenario_to_start['proposal_id']} C#{campaign_id} not found.")

                scenario_embed = utils.create_proposal_embed(
                    proposal_data=next_scenario_to_start,
                    proposer=proposer_member,
                    status='Voting',
                    options=options,
                    bot_instance=bot,
                    campaign_data=campaign
                )
                await proposals_channel.send(content=f"📢 Campaign '{campaign['title']}' (C#{campaign_id}) - Scenario {next_scenario_order_to_start}: '{next_scenario_to_start['title']}' (P#{next_scenario_to_start['proposal_id']}) is now open for voting!", embed=scenario_embed)
            else:
                print(f"WARN: Proposals channel '{proposals_channel_name}' not found for C#{campaign_id} S{next_scenario_order_to_start} announcement.")

            voting_room_channel_name = utils.CHANNELS.get("voting", "voting-room")
            voting_room_channel = discord.utils.get(guild.text_channels, name=voting_room_channel_name)
            if voting_room_channel:
                await voting_utils.update_vote_tracking(bot, guild, next_scenario_to_start)
            else:
                print(f"WARN: Voting room '{voting_room_channel_name}' not found for C#{campaign_id} S{next_scenario_order_to_start} tracking.")

            eligible_voters_list = await voting_utils.get_eligible_voters(guild, next_scenario_to_start)
            scenario_options_for_dm = await db.get_proposal_options(next_scenario_to_start['proposal_id'])
            if eligible_voters_list:
                dm_count = 0
                for member_to_dm in eligible_voters_list:
                    if member_to_dm.bot: continue
                    dm_sent = await voting.send_voting_dm(bot, member_to_dm, next_scenario_to_start, scenario_options_for_dm)
                    if dm_sent:
                        await db.add_voting_invite(next_scenario_to_start['proposal_id'], member_to_dm.id)
                        dm_count += 1
                print(f"INFO: Sent {dm_count} DMs for C#{campaign_id} S{next_scenario_order_to_start} P#{next_scenario_to_start['proposal_id']}.")
        except Exception as e_announce_next:
            print(f"ERROR announcing start of C#{campaign_id} S{next_scenario_order_to_start} (P#{next_scenario_to_start['proposal_id']}): {e_announce_next}")

        status_msg = f"Voting for Scenario {next_scenario_order_to_start}: '{next_scenario_to_start['title']}' in campaign '{campaign["title"]}' is now active."
        if next_scenario_order_to_start == campaign['num_expected_scenarios']:
            status_msg += " This is the final expected scenario for the campaign."
            # Campaign status remains 'active' until this final scenario closes.
            # The check_proposal_deadlines_task will eventually close it. The button logic above will then mark campaign 'completed'.

        await _update_control_message(status_msg, discord.Color.green())
        return True, status_msg

    # Fallback for other statuses
    await _update_control_message(f"No action taken. Campaign status: {campaign['status']}.", discord.Color.greyple())
    return False, f"Campaign is not in a state to be started or advanced (current status: {campaign['status']})."


# Modify _perform_approve_campaign_action to post to campaign-management
# // ... existing code ...
