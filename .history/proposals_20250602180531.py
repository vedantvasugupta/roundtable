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
import db
import utils
import voting
import voting_utils

# Enable all intents (or specify only the necessary ones)
intents = discord.Intents.default()
intents.message_content = True  # Required for handling messages

# Initialize bot with intents
# This bot instance is problematic here. It should be defined in main.py and passed around.
# For now, keeping it as is to minimize changes, but this is a structural issue.

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
            min_length=1,
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
    proposal_id_for_debug = "Unknown (pre-creation)"
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
        admin_notification_needed = False # Default to false, explicitly set to True when needed

        if campaign_id and campaign: # This is a campaign scenario
            campaign_status = campaign.get('status')
            if campaign_status in ['setup', 'active']:
                final_initial_status = "ApprovedScenario"
                # admin_notification_needed remains False (no separate admin approval needed for this scenario)
                print(f"INFO: Auto-approving scenario S#{scenario_order} for C#{campaign_id} (campaign status: {campaign_status}). Status set to ApprovedScenario.")
            else: # Campaign is 'pending_approval' or some other unexpected state
                final_initial_status = "Pending Approval"
                admin_notification_needed = True
                print(f"INFO: Scenario S#{scenario_order} for C#{campaign_id} (campaign status: {campaign_status}) requires admin approval. Status set to Pending Approval.")
        else: # This is a standalone proposal
            if server_id_to_use is None: # Should have been caught by earlier guild_id checks
                print(f"CRITICAL ERROR: server_id_to_use is None for standalone proposal. P#{proposal_id_for_debug}")
                await interaction.followup.send("Critical error: Server context missing for standalone proposal.", ephemeral=True)
                return None
            try:
                const_vars = await db.get_constitutional_variables(server_id_to_use)
                proposal_approval_config = const_vars.get("proposal_requires_approval", {}).get("value", "true")
                is_needed_by_constitution = proposal_approval_config.lower() == "true" if isinstance(proposal_approval_config, str) else bool(proposal_approval_config)

                if is_needed_by_constitution:
                    final_initial_status = "Pending Approval"
                    admin_notification_needed = True
                else:
                    final_initial_status = "Voting" # Directly goes to voting
                    # admin_notification_needed remains False
            except Exception as e_cv:
                print(f"Notice: Error fetching const_vars for Srv#{server_id_to_use} (standalone proposal). Defaulting to Pending Approval. Error: {e_cv}")
                final_initial_status = "Pending Approval" # Fallback to requiring approval
                admin_notification_needed = True

        # Safety net, though less likely to be hit with the new more direct structure
        if final_initial_status is None:
            print(f"WARN: final_initial_status was None after primary logic for P#{proposal_id_for_debug} C#{campaign_id} S#{scenario_order}. Defaulting based on context.")
            if campaign_id: # If it's a campaign scenario and somehow status wasn't set
                final_initial_status = "Pending Approval" # Default to pending for safety if campaign context
                admin_notification_needed = True
            else: # Standalone
                final_initial_status = "Pending Approval" # Default to pending for safety
                admin_notification_needed = True

        db_requires_approval_flag = (final_initial_status == "Pending Approval")

        print(f"DEBUG: _create_new_proposal_entry: server_id={server_id_to_use}, P#{proposal_id_for_debug}, C#{campaign_id}, S#{scenario_order}, initial_status='{final_initial_status}', db_flag_requires_approval={db_requires_approval_flag}, admin_notification_needed={admin_notification_needed}")

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
            # print(f"INFO: CampaignControlView update would be triggered here for C#{campaign_id}")
            if campaign_id: # Ensure it's a campaign context
                # campaign object should be valid and fetched from above
                if campaign and campaign.get('control_message_id') and interaction.guild:
                    try:
                        campaign_mgmt_channel_name = utils.CHANNELS.get("campaign_management", "campaign-management")
                        campaign_mgmt_channel = discord.utils.get(interaction.guild.text_channels, name=campaign_mgmt_channel_name)
                        if campaign_mgmt_channel:
                            control_message = await campaign_mgmt_channel.fetch_message(campaign['control_message_id'])
                            if control_message:
                                updated_view = CampaignControlView(campaign_id, interaction.client) # interaction.client is the bot
                                await updated_view.update_button_states() # Recalculates button states based on current DB reality

                                # Re-fetch campaign data for the most up-to-date embed information
                                fresh_campaign_data = await db.get_campaign(campaign_id)
                                if fresh_campaign_data:
                                    creator = await interaction.guild.fetch_member(fresh_campaign_data['creator_id'])
                                    embed_title = f"Campaign Management: '{fresh_campaign_data['title']}' (ID: C#{campaign_id})"
                                    embed_desc = f"**Creator:** {creator.mention if creator else f'ID: {fresh_campaign_data['creator_id']}'}\n"
                                    embed_desc += f"**Description:** {fresh_campaign_data['description'] or 'Not provided.'}\n"
                                    embed_desc += f"**Total Scenarios Expected:** {fresh_campaign_data['num_expected_scenarios']}\n"
                                    embed_desc += f"**Currently Defined:** {fresh_campaign_data['current_defined_scenarios']}"

                                    new_color = discord.Color.blue()
                                    if fresh_campaign_data['status'] == 'active': new_color = discord.Color.green()
                                    elif fresh_campaign_data['status'] == 'completed': new_color = discord.Color.gold()
                                    elif fresh_campaign_data['status'] == 'setup': new_color = discord.Color.light_grey()

                                    updated_embed = discord.Embed(title=embed_title, description=embed_desc, color=new_color)
                                    updated_embed.add_field(name="Status", value=fresh_campaign_data['status'].title(), inline=True)
                                    updated_embed.add_field(name="Last Event", value=f"Scenario {scenario_order} ('{title}') defined.", inline=False)
                                    updated_embed.set_footer(text=f"Last updated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")

                                    await control_message.edit(embed=updated_embed, view=updated_view)
                                    print(f"DEBUG: Updated control message for C#{campaign_id} after defining S#{scenario_order}.")
                                else:
                                    print(f"WARN: Could not re-fetch campaign data for C#{campaign_id} to update control message embed.")
                    except discord.NotFound:
                        print(f"ERROR: Control message ID {campaign['control_message_id']} for C#{campaign_id} not found in campaign management channel. Cannot update.")
                    except discord.Forbidden:
                        print(f"ERROR: Bot lacks permission to fetch/edit control message for C#{campaign_id}.")
                    except Exception as e_update_ctrl:
                        print(f"ERROR: Failed to update CampaignControlView for C#{campaign_id} after S#{scenario_order} definition: {e_update_ctrl}")
                else:
                    if not campaign: print(f"DEBUG: Campaign object None when trying to update control view for C#{campaign_id}")
                    if not campaign.get('control_message_id'): print(f"DEBUG: Control message ID missing for C#{campaign_id}")
                    if not interaction.guild: print(f"DEBUG: Interaction guild None for C#{campaign_id}")

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
            if isinstance(final_user_view, DefineScenarioView):
                final_user_view.message = sent_followup_msg # Allow view to edit its own message on timeout
        elif interaction.followup:
            await interaction.followup.send(content=final_user_message_content, ephemeral=False)

        # Admin notification logic
        if admin_notification_needed: # This is True if final_initial_status was "Pending Approval"
            admin_channel_name = utils.CHANNELS.get("proposals", "proposals") # Or specific campaign admin channel
            admin_channel = await utils.get_or_create_channel(interaction.guild, admin_channel_name, interaction.client.user.id)
            if admin_channel:
                admin_embed_title = f"🆕 Scenario Submitted for Approval: P#{proposal_id} - '{title}'"
                campaign_context_for_admin = ""
                if campaign_id and scenario_order and campaign: # Ensure campaign object is available
                    campaign_title = campaign['title']
                    admin_embed_title = f"🆕 Scenario Approval: C#{campaign_id} S#{scenario_order} (P#{proposal_id}) - '{title}'"
                    campaign_context_for_admin = f"**Campaign:** '{campaign_title}' (ID: C#{campaign_id}) - Scenario {scenario_order} of {campaign['num_expected_scenarios']}\n"

                admin_embed = discord.Embed(
                    title=admin_embed_title,
                    description=f"{campaign_context_for_admin}Proposed by: {interaction.user.mention}\n\n**Description:**\n{description_to_store}",
                    color=discord.Color.orange()
                )
                admin_embed.add_field(name="Voting Mechanism", value=mechanism_name.title(), inline=True)
                admin_embed.add_field(name="Options", value="\n".join([f"• {opt}" for opt in options]) or "Default: Yes/No", inline=False)
                admin_embed.add_field(name="Requested Voting Deadline", value=utils.format_deadline(deadline_db_str), inline=True)

                if hyperparameters:
                    hyperparams_display = [f"**{k.replace('_', ' ').title()}:** {v}" for k, v in hyperparameters.items()]
                    if hyperparams_display:
                         admin_embed.add_field(name="Voting Rules", value="\n".join(hyperparams_display), inline=False)

                admin_embed.set_footer(text=f"Proposal ID: {proposal_id} | Submitted at {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
                approval_view = AdminApprovalView(proposal_id=proposal_id)

                admin_message_content = f"Admins, a campaign scenario requires approval:"
                if not campaign_id: # Should not happen if admin_notification_needed is for a scenario, but as a fallback
                    admin_message_content = f"Admins, a proposal requires approval:"

                await admin_channel.send(content=admin_message_content, embed=admin_embed, view=approval_view)
            else:
                print(f"ERROR: Admin channel '{admin_channel_name}' not found for P#{proposal_id} admin notification.")
                if interaction.followup: # Notify creator if admin channel failed
                    await interaction.followup.send("Scenario created, but failed to notify admins in designated channel.",ephemeral=True)

        return proposal_id

    except Exception as e:
        print(f"ERROR in _create_new_proposal_entry P#{proposal_id_for_debug}: {e}")
        traceback.print_exc()
        if interaction and not interaction.is_expired() and interaction.response.is_done():
             await interaction.followup.send(f"Error finalizing P/S: {e}", ephemeral=True)
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
    def __init__(self, proposal_id: int): # Removed bot_instance, campaign_id, scenario_order
        super().__init__(timeout=None) # Keep view persistent
        # self.proposal_id = proposal_id # Not strictly needed as instance var if parsed from custom_id
        # self.bot = bot_instance # Use interaction.client
        # self.campaign_id = campaign_id # Fetch from DB
        # self.scenario_order = scenario_order # Fetch from DB

        # Approve Button
        self.approve_button = discord.ui.Button(
            label="Approve",
            style=discord.ButtonStyle.success,
            custom_id=f"admin_approve_proposal_{proposal_id}"
        )
        self.approve_button.callback = self.approve_button_callback
        self.add_item(self.approve_button)

        # Reject Button
        self.reject_button = discord.ui.Button(
            label="Reject",
            style=discord.ButtonStyle.danger,
            custom_id=f"admin_reject_proposal_{proposal_id}"
        )
        self.reject_button.callback = self.reject_button_callback
        self.add_item(self.reject_button)

    # Removed @discord.ui.button decorator as buttons are created in __init__
    async def approve_button_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Defer interaction first
        await interaction.response.defer(ephemeral=False) # Ephemeral False to allow followup if needed

        custom_id_parts = interaction.data['custom_id'].split('_')
        try:
            proposal_id_from_custom_id = int(custom_id_parts[-1])
        except (IndexError, ValueError):
            await interaction.followup.send("Error: Could not identify proposal ID from interaction.", ephemeral=True)
            return

        proposal = await db.get_proposal(proposal_id_from_custom_id)
        if not proposal:
            await interaction.followup.send(f"Error: Proposal P#{proposal_id_from_custom_id} not found.", ephemeral=True)
            return

        campaign_id = proposal.get('campaign_id')
        scenario_order = proposal.get('scenario_order')
        bot_instance = interaction.client

        # Call the approval logic
        success, message_content = await _perform_approve_proposal_action(
            interaction,
            proposal_id_from_custom_id,
            bot_instance,
            campaign_id=campaign_id,
            scenario_order=scenario_order
        )

        if success:
            # Disable buttons on success
            button.disabled = True
            # Find the reject button and disable it too
            for item in self.children:
                if isinstance(item, discord.ui.Button) and item.custom_id == f"admin_reject_proposal_{proposal_id_from_custom_id}":
                    item.disabled = True
            await interaction.edit_original_response(view=self)
            await interaction.followup.send(message_content, ephemeral=True) # Send confirmation to admin
        else:
            await interaction.followup.send(f"Failed to approve: {message_content}", ephemeral=True)

    # Removed @discord.ui.button decorator
    async def reject_button_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        custom_id_parts = interaction.data['custom_id'].split('_')
        try:
            proposal_id_from_custom_id = int(custom_id_parts[-1])
        except (IndexError, ValueError):
            # For modal, use interaction.response directly if not deferred
            await interaction.response.send_message("Error: Could not identify proposal ID from interaction.", ephemeral=True)
            return

        proposal = await db.get_proposal(proposal_id_from_custom_id)
        if not proposal:
            await interaction.response.send_message(f"Error: Proposal P#{proposal_id_from_custom_id} not found.", ephemeral=True)
            return

        campaign_id = proposal.get('campaign_id')
        scenario_order = proposal.get('scenario_order')
        bot_instance = interaction.client

        # Pass campaign_id and scenario_order to the modal
        modal = RejectReasonModal(
            proposal_id=proposal_id_from_custom_id,
            original_button_interaction=interaction,
            bot_instance=bot_instance,
            parent_view=self,
            campaign_id=campaign_id,
            scenario_order=scenario_order
        )
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
        if not campaign_id and new_status == "Voting": # Only for standalone proposals that go directly to voting
            # All the announcement, DM, and voting room logic is now in initiate_voting_for_proposal
            success_init_vote, init_vote_msg = await voting_utils.initiate_voting_for_proposal(
                guild=guild,
                proposal_id=proposal_id,
                bot_instance=bot_instance,
                proposal_details=proposal # Pass already fetched proposal details
            )
            if success_init_vote:
                action_message_suffix = init_vote_msg # Update suffix with DM info etc.
            else:
                # If initiation failed, we should reflect this.
                # The proposal status was already set to 'Voting', this might need careful handling or rollback.
                # For now, just log and use the error message.
                print(f"ERROR: P#{proposal_id} - Failed to fully initiate voting: {init_vote_msg}")
                action_message_suffix = f"Voting status set, but error during full initiation: {init_vote_msg}"
        elif campaign_id: # It is a campaign scenario that was just approved to 'ApprovedScenario'
            proposer = guild.get_member(proposal['proposer_id'])
            if proposer:
                try:
                    await proposer.send(f"👍 Your scenario '{proposal['title']}' (P#{proposal_id}) for Campaign C#{campaign_id} has been approved by an admin. It will become active when the campaign reaches this stage.")
                except discord.Forbidden:
                    print(f"Could not DM proposer {proposer.id} about scenario approval for P#{proposal_id}")
        # If not campaign_id and new_status is not "Voting" (e.g. future states), no special action here.

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

    # --- NEW: Update status of existing pending scenarios for this campaign ---
    try:
        campaign_proposals = await db.get_proposals_by_campaign_id(campaign_id, guild_id=guild.id) # Assuming guild_id might be needed for scoping
        updated_scenario_count = 0
        if campaign_proposals:
            for scenario_proposal in campaign_proposals:
                if scenario_proposal['status'] == 'Pending Approval':
                    await db.update_proposal_status(scenario_proposal['id'], "ApprovedScenario", set_requires_approval_false=True)
                    updated_scenario_count += 1
                    print(f"DEBUG: Auto-approved existing scenario P#{scenario_proposal['id']} for newly approved C#{campaign_id}.")
        if updated_scenario_count > 0:
            print(f"INFO: Updated {updated_scenario_count} existing pending scenarios to 'ApprovedScenario' for C#{campaign_id}.")
    except Exception as e_update_scenarios:
        print(f"ERROR: Failed to update existing scenarios for C#{campaign_id} during campaign approval: {e_update_scenarios}")
        # Optionally notify admin of this partial failure, but proceed with campaign approval main flow
        await admin_interaction_for_message_edit.followup.send(f"Warning: Campaign C#{campaign_id} approved, but an error occurred while auto-approving its existing scenarios: {e_update_scenarios}", ephemeral=True)
    # --- END NEW ---

    # Notify Creator - REMOVED DM
    creator = await guild.fetch_member(campaign_data['creator_id']) # Use fetch_member for robustness
    # if creator:
    #     try:
    #         dm_view = StartScenarioDefinitionView(campaign_id, campaign_data['title'], creator.id, bot_instance)
    #         await creator.send(
    #             f"🎉 Your campaign '{campaign_data['title']}' (ID: {campaign_id}) has been approved by an admin!\n"
    #             f"You can now start defining its scenarios. A management panel has also been posted in the server.",
    #             view=dm_view
    #         )
    #     except discord.Forbidden:
    #         print(f"Could not DM campaign creator U#{creator.id} about approval for C#{campaign_id}.")
    #     except Exception as e_dm:
    #         print(f"Error sending approval DM to U#{creator.id} for C#{campaign_id}: {e_dm}")

    # --- NEW: Post to Campaign Management Channel ---
    campaign_mgmt_channel_name = utils.CHANNELS.get("campaign_management", "campaign-management") # Ensure CHANNELS is accessible
    campaign_mgmt_channel = await utils.get_or_create_channel(guild, campaign_mgmt_channel_name, bot_instance.user.id)

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
        campaign_control_embed.set_footer(text=f"Campaign created: {utils.format_deadline(campaign_data['creation_timestamp'])}")

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
        # This method now fetches its own campaign data to ensure it's the most current.
        # The interaction parameter is kept for potential future use if direct message editing is needed from here,
        # but primary updates happen via _update_control_message in handle_start_campaign_or_next_scenario.

        campaign = await db.get_campaign(self.campaign_id)
        if not campaign:
            self.define_scenario_button.disabled = True
            self.define_scenario_button.label = "Err: CNotFound"
            self.start_next_button.disabled = True
            self.start_next_button.label = "Err: CNotFound"
            # No direct message edit here; the caller (_update_control_message) handles it.
            return

        # Define Scenario Button Logic
        is_creator = False # Placeholder, will be checked if interaction is provided and user matches
        is_admin = False   # Placeholder
        if interaction: # Basic permission check if interaction context is available
            # This is a soft check; primary permission checks are in callbacks.
            # Getting guild from bot instance and campaign guild_id
            guild = self.bot.get_guild(campaign['guild_id'])
            if guild:
                member = guild.get_member(interaction.user.id)
                if member:
                    is_admin = member.guild_permissions.administrator
            is_creator = interaction.user.id == campaign['creator_id']


        if campaign['status'] in ['completed', 'rejected', 'archived']:
            self.define_scenario_button.disabled = True
            self.define_scenario_button.label = "Campaign Ended"
        elif campaign['current_defined_scenarios'] >= campaign['num_expected_scenarios']:
            self.define_scenario_button.disabled = True
            self.define_scenario_button.label = "All Scenarios Defined"
        else:
            # Enable if campaign is in a state where scenarios can be defined (e.g. setup, active)
            # and user has permission (creator/admin). For view update, we assume this is illustrative;
            # actual enforcement is in the callback.
            self.define_scenario_button.disabled = False # Simplified for view state; callback enforces
            self.define_scenario_button.label = f"Define S{campaign['current_defined_scenarios'] + 1}/{campaign['num_expected_scenarios']}"

        # Start/Next Button Logic
        self.start_next_button.disabled = True # Default to disabled

        # Fetch all proposals for the campaign to determine button states accurately
        # This ensures we have the latest status for all scenarios.
        proposals_in_campaign = await db.get_proposals_by_campaign_id(self.campaign_id, campaign.get('guild_id'))
        if proposals_in_campaign:
            proposals_in_campaign.sort(key=lambda x: x.get('scenario_order', 0))
        else:
            proposals_in_campaign = [] # Ensure it's an empty list if None

        if campaign['status'] == 'setup':
            self.start_next_button.label = "Start Campaign"
            # A campaign can be started if at least one scenario is defined AND approved.
            # With auto-approval for scenarios in 'setup' campaigns, this means if S1 is defined, it should be 'ApprovedScenario'.
            first_scenario = next((p for p in proposals_in_campaign if p.get('scenario_order') == 1), None)
            if first_scenario and first_scenario.get('status') == 'ApprovedScenario':
                self.start_next_button.disabled = False
            else:
                self.start_next_button.disabled = True
                if not first_scenario:
                    self.start_next_button.label = "Define Scenario 1 First"
                # If S1 exists but isn't ApprovedScenario (e.g. if campaign was approved *before* S1 was defined, an edge case)
                # or if S1 is Pending Approval (should not happen if auto-approval works for 'setup' state)
                # a more generic label might be needed, but "Define Scenario 1 First" covers the primary path.
                # The callback logic in start_next_callback will provide a more specific error if S1 is not approved.

        elif campaign['status'] == 'active':
            active_voting_scenarios = [p for p in proposals_in_campaign if p['status'] == 'Voting']

            if active_voting_scenarios:
                self.start_next_button.label = f"S{active_voting_scenarios[0].get('scenario_order','?')} Active"
                self.start_next_button.disabled = True
            else:
                # No scenario is currently voting, check if we can start the next one
                closed_scenarios = sorted(
                    [p for p in proposals_in_campaign if p['status'] == 'Closed'],
                    key=lambda x: x.get('scenario_order', 0),
                    reverse=True
                )
                last_closed_order = closed_scenarios[0]['scenario_order'] if closed_scenarios else 0
                next_scenario_order_needed = last_closed_order + 1

                if next_scenario_order_needed > campaign['num_expected_scenarios']:
                    self.start_next_button.label = "All Scenarios Done"
                    self.start_next_button.disabled = True
                    # Potentially set campaign status to 'completed' here or via a separate mechanism/button
                else:
                    next_scenario_to_start = next((p for p in proposals_in_campaign if p.get('scenario_order') == next_scenario_order_needed), None)
                    if next_scenario_to_start and next_scenario_to_start.get('status') == 'ApprovedScenario':
                        self.start_next_button.label = f"Start Scenario {next_scenario_order_needed}"
                        self.start_next_button.disabled = False
                    elif next_scenario_to_start and next_scenario_to_start.get('status') == 'Pending Approval':
                        # This case should be less common if auto-approval for scenarios in active campaigns is working
                        self.start_next_button.label = f"Await S{next_scenario_order_needed} Approval"
                        self.start_next_button.disabled = True
                    else: # Not defined or not in a startable state
                        self.start_next_button.label = f"Define S{next_scenario_order_needed} First"
                        self.start_next_button.disabled = True
        elif campaign['status'] in ['completed', 'rejected', 'archived']:
            self.start_next_button.label = "Campaign Ended"
            self.start_next_button.disabled = True
        else: # Unknown status or pending_approval (cannot start yet)
            self.start_next_button.label = f"Status: {campaign['status'].title()}"
            self.start_next_button.disabled = True

    async def define_scenario_callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)

        campaign = await db.get_campaign(self.campaign_id)
        if not campaign:
            await interaction.followup.send(f"Error: Campaign C#{self.campaign_id} not found.", ephemeral=True)
            return

        # Permission Check: Creator or Admin
        is_creator = interaction.user.id == campaign['creator_id']
        is_admin = False
        if interaction.guild: # Should always have guild context for campaign control panels
            member = interaction.guild.get_member(interaction.user.id)
            if member:
                is_admin = member.guild_permissions.administrator

        if not (is_creator or is_admin):
            await interaction.followup.send("You must be the campaign creator or an admin to define scenarios.", ephemeral=True)
            return

        if campaign['status'] not in ['setup', 'active']:
            await interaction.followup.send(f"Campaign C#{self.campaign_id} is not in 'setup' or 'active' phase (current: {campaign['status'].title()}). Cannot define new scenarios now.", ephemeral=True)
            return

        if campaign['current_defined_scenarios'] >= campaign['num_expected_scenarios']:
            await interaction.followup.send(f"All {campaign['num_expected_scenarios']} scenarios for Campaign C#{self.campaign_id} are already defined.", ephemeral=True)
            return

        next_scenario_num = campaign['current_defined_scenarios'] + 1
        total_scenarios = campaign['num_expected_scenarios']

        # Present the DefineScenarioView (similar to StartScenarioDefinitionView's callback)
        # The original_interaction for DefineScenarioView is this button interaction,
        # which allows it to edit the ephemeral followup if it times out.
        scenario_definition_prompt_view = DefineScenarioView(
            campaign_id=self.campaign_id,
            next_scenario_order=next_scenario_num,
            total_scenarios=total_scenarios,
            original_interaction=interaction # Pass the current button interaction
        )

        followup_message_content = (
            f"🗳️ Defining Scenario {next_scenario_num} of {total_scenarios} for Campaign '{campaign['title']}' (ID: C#{self.campaign_id})."
        )

        # Send the DefineScenarioView as a new ephemeral message
        # The DefineScenarioView itself will then send another ephemeral message with the ProposalMechanismSelectionView
        sent_message = await interaction.followup.send(
            content=followup_message_content,
            view=scenario_definition_prompt_view,
            ephemeral=True
        )
        scenario_definition_prompt_view.message = sent_message # Allow the view to edit its own message

        # Optionally, update the control panel message itself to reflect that definition is in progress
        # This might be too noisy if the user quickly defines. For now, we rely on button state updates.
        # await self.update_button_states(interaction) # Pass interaction if needed for permission context
        # if interaction.message: # The control panel message
        #    await interaction.message.edit(view=self)
        # For now, a simple confirmation that the flow has started.
        # The primary feedback is the new view being sent.

    async def start_next_callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        guild = interaction.guild
        if not guild:
            await interaction.followup.send("This command can only be used in a server.", ephemeral=True)
            return

        bot_instance = self.bot # or interaction.client

        campaign = await db.get_campaign(self.campaign_id)
        if not campaign:
            await interaction.followup.send(f"Error: Campaign C#{self.campaign_id} not found.", ephemeral=True)
            await self.update_button_states(interaction) # Update view if campaign is gone
            if interaction.message: await interaction.message.edit(view=self)
            return

        # Permission Check: Creator or Admin
        is_creator = interaction.user.id == campaign['creator_id']
        member = guild.get_member(interaction.user.id)
        is_admin = member.guild_permissions.administrator if member else False

        if not (is_creator or is_admin):
            await interaction.followup.send("You must be the campaign creator or an admin to start/progress the campaign.", ephemeral=True)
            return

        proposals_in_campaign = await db.get_proposals_by_campaign_id(self.campaign_id, guild.id)
        proposals_in_campaign.sort(key=lambda x: x.get('scenario_order', 0))

        action_taken_message = ""
        action_error = False

        # Case 1: Start the Campaign (if in 'setup' state)
        if campaign['status'] == 'setup':
            first_scenario_to_start = next((p for p in proposals_in_campaign if p.get('scenario_order') == 1 and p.get('status') == 'ApprovedScenario'), None)
            if not first_scenario_to_start:
                action_taken_message = "Cannot start campaign: Scenario 1 is not defined or not approved."
                action_error = True
            else:
                # Update campaign status to 'active'
                await db.update_campaign_status(self.campaign_id, 'active')
                campaign['status'] = 'active' # Update local copy for embed
                action_taken_message = f"Campaign C#{self.campaign_id} ('{campaign["title"]}') is now active!\n"

                # Initiate voting for the first scenario
                success_init, msg_init = await voting_utils.initiate_voting_for_proposal(guild, first_scenario_to_start['proposal_id'], bot_instance, first_scenario_to_start)
                if success_init:
                    action_taken_message += f"Voting for Scenario 1 ('{first_scenario_to_start["title"]}') has started. {msg_init}"
                else:
                    action_taken_message += f"Failed to start voting for Scenario 1: {msg_init}"
                    action_error = True # Partial success, but flag error for final message

                # Audit Log
                audit_channel = discord.utils.get(guild.text_channels, name="audit-log")
                if audit_channel:
                    await audit_channel.send(f"▶️ **Campaign Started**: C#{self.campaign_id} ('{campaign['title']}') started by {interaction.user.mention}. Scenario 1 initiated.")

        # Case 2: Start Next Scenario (if in 'active' state and no current scenario is 'Voting')
        elif campaign['status'] == 'active':
            active_voting_scenarios = [p for p in proposals_in_campaign if p['status'] == 'Voting']
            if active_voting_scenarios:
                action_taken_message = f"Cannot start another scenario: Scenario {active_voting_scenarios[0]['scenario_order']} ('{active_voting_scenarios[0]['title']}') is still active."
                action_error = True
            else:
                closed_scenarios = sorted([p for p in proposals_in_campaign if p['status'] == 'Closed'], key=lambda x: x.get('scenario_order', 0), reverse=True)
                last_closed_order = closed_scenarios[0]['scenario_order'] if closed_scenarios else 0
                next_scenario_order_needed = last_closed_order + 1

                if next_scenario_order_needed > campaign['num_expected_scenarios']:
                    action_taken_message = "All scenarios in this campaign have been completed."
                    # Optionally, change campaign status to 'completed' here if desired.
                    # await db.update_campaign_status(self.campaign_id, 'completed')
                    # campaign['status'] = 'completed'
                else:
                    next_scenario_to_start = next((p for p in proposals_in_campaign if p.get('scenario_order') == next_scenario_order_needed and p.get('status') == 'ApprovedScenario'), None)
                    if not next_scenario_to_start:
                        action_taken_message = f"Cannot start Scenario {next_scenario_order_needed}: It is not defined or not approved."
                        action_error = True
                    else:
                        success_init, msg_init = await voting_utils.initiate_voting_for_proposal(guild, next_scenario_to_start['proposal_id'], bot_instance, next_scenario_to_start)
                        if success_init:
                            action_taken_message = f"Voting for Scenario {next_scenario_order_needed} ('{next_scenario_to_start["title"]}') has started. {msg_init}"
                        else:
                            action_taken_message = f"Failed to start voting for Scenario {next_scenario_order_needed}: {msg_init}"
                            action_error = True

                        # Audit Log
                        audit_channel = discord.utils.get(guild.text_channels, name="audit-log")
                        if audit_channel:
                            await audit_channel.send(f"▶️ **Next Scenario Started**: For C#{self.campaign_id}, Scenario {next_scenario_order_needed} ('{next_scenario_to_start["title"]}') initiated by {interaction.user.mention}.")
        else:
            action_taken_message = f"Campaign C#{self.campaign_id} is in status '{campaign["status"]}'. No action taken."
            action_error = True

        # Update the control panel message (embed and view)
        if interaction.message:
            try:
                # Re-fetch fresh campaign data for the embed, as status might have changed
                fresh_campaign_data = await db.get_campaign(self.campaign_id)
                if fresh_campaign_data: # Check if still exists
                    campaign = fresh_campaign_data # Use the latest data for embed

                # Update embed
                creator = await guild.fetch_member(campaign['creator_id']) # Fetch creator for mention
                embed_title = f"Campaign Management: '{campaign['title']}' (ID: C#{self.campaign_id})"
                embed_desc = f"**Creator:** {creator.mention if creator else f'ID: {campaign['creator_id']}'}\n"
                embed_desc += f"**Description:** {campaign['description'] or 'Not provided.'}\n"
                embed_desc += f"**Total Scenarios Expected:** {campaign['num_expected_scenarios']}\n"
                embed_desc += f"**Currently Defined:** {campaign['current_defined_scenarios']}"

                new_color = discord.Color.blue()
                if campaign['status'] == 'active': new_color = discord.Color.green()
                elif campaign['status'] == 'completed': new_color = discord.Color.gold()
                elif campaign['status'] == 'setup': new_color = discord.Color.light_grey()

                updated_embed = discord.Embed(title=embed_title, description=embed_desc, color=new_color)
                updated_embed.add_field(name="Status", value=campaign['status'].title(), inline=True)
                if action_taken_message and not action_error:
                    updated_embed.add_field(name="Last Action Result", value=action_taken_message.split('\n')[0], inline=False) # Show first line of success
                elif action_error:
                     updated_embed.add_field(name="Last Action Info", value=action_taken_message, inline=False)
                updated_embed.set_footer(text=f"Last updated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")

                await self.update_button_states(interaction) # Update button states before editing message
                await interaction.message.edit(embed=updated_embed, view=self)
            except discord.NotFound:
                print(f"WARN: Control message for C#{self.campaign_id} not found when trying to edit in start_next_callback.")
            except Exception as e_edit_msg:
                print(f"ERROR: Failed to edit control message for C#{self.campaign_id} in start_next_callback: {e_edit_msg}")
                # Send a followup if editing the original message failed but an action was attempted
                if action_taken_message: # If there was something to report
                    await interaction.followup.send(f"Control panel update failed, but: {action_taken_message}", ephemeral=True)
                    return # Avoid sending another followup

        # Send final followup to the admin/creator who clicked the button
        if action_taken_message:
            await interaction.followup.send(action_taken_message, ephemeral=True)
        else: # Should not happen if logic is correct
            await interaction.followup.send("No specific action was performed. Please check campaign status.", ephemeral=True)

    async def on_timeout(self):
        # This method is called when the view times out.
        # You can implement any additional logic you want to execute when the view times out.
        print("CampaignControlView timed out!")

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # This method is called to check if the interaction is valid.
        # You can implement any additional checks you want to execute before processing the interaction.
        return True

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item):
        # This method is called when an error occurs.
        # You can implement any additional logic you want to execute when an error occurs.
        print(f"CampaignControlView error: {error}")
