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
            # If campaign is 'pending_approval', final_initial_status remains None here,
            # requires_approval_check_done is False, so it falls into const_vars check.

        if not requires_approval_check_done:
            if server_id_to_use is None: # Should be caught by earlier checks
                print(f"CRITICAL ERROR: server_id_to_use is None before const_vars. P#{proposal_id_for_debug}")
                await interaction.followup.send("Critical error: server context missing.", ephemeral=True)
                return None
            try:
                const_vars = await db.get_constitutional_variables(server_id_to_use)
                proposal_approval_config = const_vars.get("proposal_requires_approval", {}).get("value", "true")
                is_needed = proposal_approval_config.lower() == "true" if isinstance(proposal_approval_config, str) else bool(proposal_approval_config)

                if is_needed:
                    final_initial_status = "Pending Approval"
                else: # Not needed by constitution
                    # If it's a scenario for a campaign that is itself 'pending_approval',
                    # this scenario should also be 'Pending Approval', regardless of constitution for standalone items.
                    if campaign_id and campaign and campaign.get('status') == 'pending_approval':
                        final_initial_status = "Pending Approval"
                        print(f"INFO: Scenario for PENDING C#{campaign_id}. Setting status to Pending Approval, overriding const_var.")
                    else: # Standalone, or scenario for an already approved campaign (though that case is handled by requires_approval_check_done=True)
                        final_initial_status = "Voting" # Primarily for standalone proposals that don't need approval
            except Exception as e_cv:
                print(f"Notice: Error fetching const_vars for Srv#{server_id_to_use}. Defaulting to Pending Approval. Error: {e_cv}")
                final_initial_status = "Pending Approval"

        if final_initial_status is None: # Safety net default
            print(f"WARN: final_initial_status was None for P#{proposal_id_for_debug} C#{campaign_id} S#{scenario_order}. Defaulting.")
            final_initial_status = "ApprovedScenario" if campaign_id else "Pending Approval"

        db_requires_approval_flag = (final_initial_status == "Pending Approval")

        print(f"DEBUG: _create_new_proposal_entry: server_id={server_id_to_use}, P#{proposal_id_for_debug}, C#{campaign_id}, S#{scenario_order}, initial_status='{final_initial_status}', db_flag_requires_approval={db_requires_approval_flag}")

        proposal_id = await db.create_proposal(
            server_id=server_id_to_use,
            proposer_id=proposer_id,
            title=title,
            description=description_to_store,
            voting_mechanism=mechanism_name,
            deadline=deadline_db_str,
            requires_approval=db_requires_approval_flag,
            hyperparameters=hyperparameters,
            campaign_id=campaign_id,
            scenario_order=scenario_order,
            initial_status=final_initial_status
        )
        proposal_id_for_debug = str(proposal_id) if proposal_id else "FailedCreation"

        if not proposal_id:
            await interaction.followup.send("❌ Failed to create proposal/scenario in DB.", ephemeral=True)
            return None

        await db.add_proposal_options(proposal_id, options)
        print(f"DEBUG: P#{proposal_id} options added: {options}")

        admin_notification_needed = (final_initial_status == "Pending Approval") # Assuming final_initial_status is correctly set from previous attempts
        final_user_message_content = ""
        final_user_view = None

        if campaign_id and scenario_order: # This is a scenario within a campaign
            # Ensure campaign object is available (it should be if campaign_id is present and fetched earlier)
            if not campaign: # Re-fetch if not available in current scope, though it should be
                campaign = await db.get_campaign(campaign_id)

            user_feedback_scenario_part = f"✅ Scenario {scenario_order} ('{title}') for Campaign '{campaign['title'] if campaign else 'N/A'}' (C#{campaign_id}) created with status: {final_initial_status}!"

            if not campaign: # Should not happen if ID exists
                await interaction.followup.send(user_feedback_scenario_part + "\n🚨 Error: Could not retrieve full campaign details. Contact admin.", ephemeral=True)
                return proposal_id

            if final_initial_status == "Pending Approval":
                user_feedback_scenario_part += " It requires admin approval."
            elif final_initial_status == "ApprovedScenario":
                 user_feedback_scenario_part += " It is defined and will be available when the campaign progresses."
            # If 'Voting', it's immediately voting (less ideal for scenarios but handled by status).

            current_defined_count = campaign['current_defined_scenarios'] # This should have been incremented by db.increment_defined_scenarios if that call was made
            # For this edit, assume current_defined_count is accurate from `campaign` object (which _create_new_proposal_entry should update via db.increment_defined_scenarios)
            # If db.increment_defined_scenarios is not yet in this function, this count might be off by one for the message.
            # We'll assume it IS called before this feedback section in the full version.

            if current_defined_count < campaign['num_expected_scenarios']:
                next_scenario_num_for_view = current_defined_count + 1 # If current_defined_scenarios ALREADY reflects the one just made.
                                                                # If not, it should be current_defined_count + 1 if it was the old count.
                                                                # For safety, let's assume it's for the *next* one to define.
                                                                # The `scenario_order` is the one just created.
                if scenario_order >= campaign['num_expected_scenarios']:
                     final_user_message_content = user_feedback_scenario_part + f"\n\n🎉 All {campaign['num_expected_scenarios']} scenarios for Campaign '{campaign['title']}' are now defined!"
                else:
                    final_user_message_content = user_feedback_scenario_part + f"\n\nNext: Define Scenario {scenario_order + 1} of {campaign['num_expected_scenarios']}?"
                    final_user_view = DefineScenarioView(campaign_id=campaign_id, next_scenario_order=scenario_order + 1, total_scenarios=campaign['num_expected_scenarios'], original_interaction=interaction)
            else:
                final_user_message_content = user_feedback_scenario_part + f"\n\n🎉 All {campaign['num_expected_scenarios']} scenarios for Campaign '{campaign['title']}' are now defined!"

            # Update CampaignControlView (logic for this will be a separate edit attempt)
            print(f"INFO: CampaignControlView update would be triggered here for C#{campaign_id}")

        else: # This is a standalone proposal
            final_user_message_content = f"✅ Proposal P#{proposal_id} ('{title}') created with status: {final_initial_status}!"
            if final_initial_status == "Pending Approval":
                final_user_message_content += " It requires admin approval."
            elif final_initial_status == "Voting":
                final_user_message_content += " Voting has started."
                # Admin notification and public announcements for standalone/voting are assumed handled elsewhere or in full version

        # Send user feedback
        if final_user_view and interaction.followup:
            sent_followup_msg = await interaction.followup.send(content=final_user_message_content, view=final_user_view, ephemeral=False)
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
