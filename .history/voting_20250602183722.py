import discord
from discord.ext import commands
import asyncio
from datetime import datetime, timedelta
import json
import db
import random
import re  # Need re for parse_duration fallback if not using utils
from typing import List, Dict, Any, Optional, Union, Tuple
import traceback

# Import functions/classes from voting_utils and utils
import voting_utils  # Import the module to access its functions
import utils  # Import the module to access its functions
# ========================
# 🔹 INTERACTIVE VOTING UI
# ========================

# In voting.py

# ... (ensure all necessary imports are at the top, including db, voting_utils, utils, discord) ...

async def process_vote(user_id: int, proposal_id: int, vote_data_dict: Dict[str, Any], is_abstain: bool, tokens_invested: Optional[int]) -> Tuple[bool, str]:
    """Process and record a vote using db.record_vote."""
    try:
        print(f"DEBUG: process_vote called for P#{proposal_id} U#{user_id}. Abstain: {is_abstain}, Tokens: {tokens_invested}")
        # Basic proposal and status checks (can be expanded)
        proposal = await db.get_proposal(proposal_id)
        if not proposal:
            return False, "Proposal not found."
        if proposal.get('status') != 'Voting':
            return False, f"Voting is not open for this proposal (status: {proposal.get('status', 'Unknown')})."

        # Deadline check (simplified, more robust checks can be added here or in calling function)
        deadline_data = proposal.get('deadline')
        if deadline_data:
            deadline_dt = utils.parse_datetime(deadline_data) # REVERTED: Assuming utils.parse_datetime handles various formats
            if deadline_dt and datetime.now(deadline_dt.tzinfo) > deadline_dt: # Make timezone aware if necessary
                return False, "Voting has ended for this proposal."

        # vote_data_dict is the mechanism-specific data (e.g., {'option': 'A'} or {'rankings': ['A', 'B']})
        # It should already be validated by the view/modal before this stage.
        vote_json_str = json.dumps(vote_data_dict)

        success = await db.record_vote(
            user_id=user_id,
            proposal_id=proposal_id,
            vote_data=vote_json_str, # This is the mechanism-specific part
            is_abstain=is_abstain,
            tokens_invested=tokens_invested
        )

        if success:
            # Trigger update of the public tracking message asynchronously
            # Ensure proposal object has server_id for fetching guild
            if proposal.get('server_id') and proposal.get('vote_tracking_message_id'):
                 asyncio.create_task(update_voting_message(proposal)) # Fire and forget
            return True, "Vote recorded successfully."
        else:
            return False, "Failed to record vote in the database."

    except Exception as e:
        print(f"CRITICAL ERROR in process_vote for P:{proposal_id} U:{user_id}: {e}")
        traceback.print_exc()
        return False, "An internal error occurred while processing your vote."


class AbstainButton(discord.ui.Button):
    def __init__(self, proposal_id: int):
        super().__init__(label="Abstain from Voting", style=discord.ButtonStyle.secondary, custom_id=f"abstain_{proposal_id}_{random.randint(1000, 9999)}")
        self.proposal_id = proposal_id

    async def callback(self, interaction: discord.Interaction):
        # Ensure the view associated with this interaction is BaseVoteView or subclass
        if not isinstance(self.view, BaseVoteView):
            await interaction.response.send_message("Error: Could not process this action.", ephemeral=True)
            return

        # User check and submission check are handled by BaseVoteView.interaction_check
        # and the submit_callback's disabling logic.

        self.view.is_abstain_vote = True
        # For abstain, we directly call finalize_vote, potentially skipping token investment modal
        # or passing a specific value (e.g., 0 tokens).
        # If it's a campaign, and abstaining should still prompt for tokens (e.g. to "burn" them or "allocate zero"),
        # then this would go through the submit_vote_callback flow.
        # For now, let's assume abstaining in a campaign means 0 tokens for this scenario.
        if self.view.campaign_id is not None:
            await self.view.finalize_vote(interaction, tokens_invested_this_scenario=0)
        else:
            await self.view.finalize_vote(interaction, tokens_invested_this_scenario=None)


class TokenInvestmentModal(discord.ui.Modal, title="Invest Tokens"):
    def __init__(self, base_vote_view: 'BaseVoteView', remaining_tokens: int):
        super().__init__()
        self.base_vote_view = base_vote_view
        self.remaining_tokens = remaining_tokens

        self.token_input = discord.ui.TextInput(
            label=f"Tokens to Invest (Max: {remaining_tokens})",
            placeholder=f"Enter a number (0-{remaining_tokens})",
            required=True,
            min_length=1,
            max_length=len(str(remaining_tokens)) if remaining_tokens > 0 else 1
        )
        self.add_item(self.token_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            tokens_to_invest_str = self.token_input.value
            if not tokens_to_invest_str.isdigit():
                await interaction.response.send_message("❌ Invalid input. Please enter a number.", ephemeral=True)
                return

            tokens_to_invest = int(tokens_to_invest_str)

            if not (0 <= tokens_to_invest <= self.remaining_tokens):
                await interaction.response.send_message(
                    f"❌ Invalid amount. You must invest between 0 and {self.remaining_tokens} tokens.",
                    ephemeral=True
                )
                return

            # Defer here if finalize_vote is slow, then follow up. For now, direct call.
            # await interaction.response.defer(ephemeral=True, thinking=True) # Optional
            await self.base_vote_view.finalize_vote(interaction, tokens_invested_this_scenario=tokens_to_invest)
            # Confirmation is handled by finalize_vote updating the original message

        except ValueError:
            await interaction.response.send_message("❌ Invalid input. Please enter a whole number.", ephemeral=True)
        except Exception as e:
            print(f"Error in TokenInvestmentModal on_submit: {e}")
            traceback.print_exc()
            await interaction.response.send_message("An error occurred while submitting your token investment.", ephemeral=True)


class BaseVoteView(discord.ui.View):
    def __init__(self,
                 proposal_id: int,
                 options: List[str],
                 user_id: int,
                 allow_abstain: bool = True,
                 campaign_id: Optional[int] = None,
                 campaign_details: Optional[Dict[str, Any]] = None,
                 user_remaining_tokens: Optional[int] = None): # Added campaign params
        super().__init__(timeout=86400)  # 24-hour timeout for voting
        self.proposal_id = proposal_id
        self.options = options
        self.user_id = user_id # The user this DM is intended for
        self.is_submitted = False
        self.is_abstain_vote = False
        self.selected_mechanism_vote_data: Dict[str, Any] = {} # To store mechanism-specific choices before final submission

        self.campaign_id = campaign_id
        self.campaign_details = campaign_details
        self.user_remaining_tokens = user_remaining_tokens

        self.add_mechanism_items()  # Populate with mechanism-specific buttons/selects
        if allow_abstain:
            self.add_item(AbstainButton(proposal_id=self.proposal_id))

    def add_mechanism_items(self):
        # This method should be overridden by subclasses to add specific voting UI elements
        # For example, buttons for plurality, select menus for ranked choice, etc.
        # Each item's callback should:
        # 1. Perform its specific logic (e.g., record the selected option).
        # 2. Call self.submit_vote_callback(interaction) to proceed.
        raise NotImplementedError("Subclasses must implement add_mechanism_items")

    def has_selection(self) -> bool:
        # Subclasses should implement this to check if a valid vote selection has been made
        # (e.g., an option is selected for plurality, rankings are set for Borda)
        # This is used to enable/disable a general "Submit Vote" button if one exists,
        # or for validation before proceeding.
        # For views where item interaction directly triggers submission, this might be less critical.
        return bool(self.selected_mechanism_vote_data) or self.is_abstain_vote

    async def submit_vote_callback(self, interaction: discord.Interaction):
        """
        This callback is triggered by mechanism-specific item interactions (e.g., clicking an option button).
        It decides whether to show TokenInvestmentModal or finalize directly.
        """
        if self.is_submitted:
            await interaction.response.send_message("You have already submitted your vote for this proposal.", ephemeral=True)
            return

        # Validation of mechanism-specific selection should happen in the item's callback
        # before this method is called, or right here.
        # For example, PluralityVoteView's option_callback sets self.selected_mechanism_vote_data

        if not self.is_abstain_vote and not self.has_selection():
             await interaction.response.send_message("Please make a selection before submitting.", ephemeral=True)
             return

        if self.is_abstain_vote: # Handled by AbstainButton's callback directly calling finalize_vote
            # This path should ideally not be hit if AbstainButton calls finalize_vote directly.
            # Kept for safety, assuming 0 tokens for campaign abstain.
            print(f"DEBUG: BaseVoteView.submit_vote_callback reached for abstain vote for P#{self.proposal_id}")
            await self.finalize_vote(interaction, tokens_invested_this_scenario=0 if self.campaign_id else None)
        elif self.campaign_id is not None and self.user_remaining_tokens is not None:
            # It's a campaign vote, present the token investment modal
            if self.user_remaining_tokens == 0: # No tokens left, auto-invest 0
                 await interaction.response.defer(ephemeral=True, thinking=True) # Acknowledge interaction
                 await self.finalize_vote(interaction, tokens_invested_this_scenario=0)
            else:
                token_modal = TokenInvestmentModal(base_vote_view=self, remaining_tokens=self.user_remaining_tokens)
                await interaction.response.send_modal(token_modal)
        else:
            # Not a campaign vote, or abstain for non-campaign, finalize directly
            await interaction.response.defer(ephemeral=True, thinking=True) # Acknowledge interaction
            await self.finalize_vote(interaction, tokens_invested_this_scenario=None)

    async def finalize_vote(self, interaction: discord.Interaction, tokens_invested_this_scenario: Optional[int]):
        """
        Finalizes the vote recording process after all selections (including tokens) are made.
        """
        if self.is_submitted: # Should have been caught by submit_vote_callback or modal prevented re-submission
            # If modal calls this directly, this check is still useful.
            # Respond to the modal's interaction if it's fresh.
            if not interaction.response.is_done():
                await interaction.response.send_message("You have already submitted your vote for this proposal.", ephemeral=True)
            else: # If original interaction was deferred, follow up.
                await interaction.followup.send("You have already submitted your vote for this proposal.", ephemeral=True)
            return

        self.is_submitted = True # Mark as submitted early to prevent race conditions

        try:
            # Ensure interaction is acknowledged if not already done (e.g., by modal or deferral)
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=True, thinking=True)

            mechanism_specific_data = self.get_mechanism_vote_data() if not self.is_abstain_vote else {}

            # Call the refactored global process_vote which now calls db.record_vote
            success, message = await process_vote(
                user_id=self.user_id,
                proposal_id=self.proposal_id,
                vote_data_dict=mechanism_specific_data, # This is the content of the 'vote_data' JSON column
                is_abstain=self.is_abstain_vote,
                tokens_invested=tokens_invested_this_scenario
            )

            confirmation_message_content = f"### ✅ Vote Confirmed for P#{self.proposal_id}!\n"

            if not success:
                confirmation_message_content = f"### ❌ Vote Failed for P#{self.proposal_id}\n"
                confirmation_message_content += f"Reason: {message}"
                if interaction.message: # Original DM message
                    await interaction.edit_original_response(content=confirmation_message_content, view=None)
                else: # Should not happen if modal came from a message
                    await interaction.followup.send(content=confirmation_message_content, ephemeral=True)
                return

            # If vote succeeded and it's a campaign vote with tokens invested:
            if self.campaign_id is not None and tokens_invested_this_scenario is not None and tokens_invested_this_scenario > 0:
                db_update_success = await db.update_user_remaining_tokens(
                    campaign_id=self.campaign_id,
                    user_id=self.user_id,
                    tokens_spent=tokens_invested_this_scenario
                )
                if db_update_success:
                    self.user_remaining_tokens = (self.user_remaining_tokens or 0) - tokens_invested_this_scenario
                    confirmation_message_content += f"You invested **{tokens_invested_this_scenario}** tokens.\n"
                    confirmation_message_content += f"Your new remaining campaign token balance: **{self.user_remaining_tokens}**."
                else:
                    # This is a partial failure state - vote recorded, but token update failed. Critical.
                    confirmation_message_content += f"Your vote was recorded, but there was an issue updating your token balance. Please contact an admin. You invested {tokens_invested_this_scenario} tokens."
                    print(f"CRITICAL: Vote for P#{self.proposal_id} U#{self.user_id} recorded, but FAILED to update tokens for C#{self.campaign_id}. Tokens spent: {tokens_invested_this_scenario}")
            elif self.campaign_id is not None: # Campaign vote, but 0 tokens or abstain
                 confirmation_message_content += f"You invested **0** tokens for this scenario.\n"
                 confirmation_message_content += f"Your remaining campaign token balance: **{self.user_remaining_tokens}**."
            else: # Not a campaign vote
                confirmation_message_content += message # "Your vote has been recorded/updated."

            # Disable all components in the view
            for item in self.children:
                if isinstance(item, (discord.ui.Button, discord.ui.Select)):
                    item.disabled = True

            if interaction.message: # Original DM message from which the view/modal originated
                 await interaction.edit_original_response(content=confirmation_message_content, view=self) # Show disabled view
            else: # Should not happen if interaction has a message
                 await interaction.followup.send(content=confirmation_message_content, ephemeral=True) # Fallback

        except Exception as e:
            self.is_submitted = False # Rollback submission status on error if vote didn't go through
            print(f"Error in BaseVoteView.finalize_vote for P#{self.proposal_id} U#{self.user_id}: {e}")
            traceback.print_exc()
            # Try to inform user on the original interaction if possible
            error_response = "An unexpected error occurred while finalizing your vote. Please try again or contact an admin."
            if not interaction.response.is_done():
                await interaction.response.send_message(error_response, ephemeral=True)
            else:
                try: # Can't edit if the original response was the modal itself.
                    if interaction.message: # If there's an underlying message (e.g. the DM)
                        await interaction.edit_original_response(content=error_response, view=None)
                    else: # Fallback if no message to edit
                        await interaction.followup.send(error_response, ephemeral=True)
                except discord.NotFound: # If the original message or interaction is gone
                     print(f"Could not send error to user {self.user_id} for P#{self.proposal_id}, interaction/message not found.")
                except Exception as e_int:
                     print(f"Further error trying to send error to user: {e_int}")


    def get_mechanism_vote_data(self) -> Dict[str, Any]:
        # This should be overridden by subclasses to return the data specific to their mechanism
        # e.g., for Plurality: return {"option": self.selected_option}
        #      for Ranked: return {"rankings": self.selected_rankings}
        # This data will be JSON serialized and stored in the database.
        return self.selected_mechanism_vote_data # Relies on subclasses populating this

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # Check if the interaction user is the one this DM was intended for
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This voting form is not for you.", ephemeral=True)
            return False
        # Check if vote already submitted (handled by individual callbacks too, but good as a general check)
        if self.is_submitted:
            await interaction.response.send_message("You have already voted on this proposal using this message.", ephemeral=True)
            return False
        return True


class PluralityVoteView(BaseVoteView):
    """Interactive UI for plurality voting"""

    def add_mechanism_items(self):
        self.selected_option = None
        # Add option buttons (up to 5 per row), ensure they are on rows above the submit buttons (row 4)
        # Max items in a view - (Abstain + Submit buttons)
        max_button_items = 25 - 2
        num_options_to_buttonize = min(len(self.options), max_button_items)

        for i in range(num_options_to_buttonize):
            option = self.options[i]

            button_label = option
            if len(button_label) > 80:
                button_label = button_label[:77] + "..."

            button = discord.ui.Button(
                label=button_label,
                style=discord.ButtonStyle.secondary,
                custom_id=f"plurality_{self.proposal_id}_{i}",
                row=i // 5  # Assign rows starting from 0
            )
            button.callback = self.option_callback
            self.add_item(button)

    def has_selection(self):
        return self.selected_option is not None

    def get_mechanism_vote_data(self) -> Dict[str, Any]:
        return {"option": self.selected_option}

    async def option_callback(self, interaction: discord.Interaction):
        # User check is already handled by BaseVoteView.interaction_check
        # Submission check is already handled in BaseVoteView.submit_callback and item disabled state

        button_id = interaction.data["custom_id"]
        try:
            parts = button_id.split("_")
            if len(parts) == 3 and parts[0] == "plurality" and int(parts[1]) == self.proposal_id:
                option_index = int(parts[2])
                if 0 <= option_index < len(self.options):
                    self.selected_option = self.options[option_index] # Set the selected option state
                    # Update button styles in memory. These will be applied when finalize_vote eventually edits the message.
                    for child in self.children:
                        if isinstance(child, discord.ui.Button) and child.custom_id.startswith(f"plurality_{self.proposal_id}_"):
                            try:
                                child_option_index_for_style = int(child.custom_id.split("_")[2])
                                original_option_for_button_style = self.options[child_option_index_for_style]

                                if original_option_for_button_style == self.selected_option:
                                    child.style = discord.ButtonStyle.primary
                                else:
                                    child.style = discord.ButtonStyle.secondary
                            except (IndexError, ValueError):
                                pass  # Ignore invalid buttons for styling
                else:
                     raise ValueError("Option index out of bounds")
            else:
                raise ValueError("Invalid custom_id format")

        except (IndexError, ValueError) as e:
            print(
                f"Error parsing plurality button custom_id '{button_id}': {e}")
            # Respond to the interaction to prevent "Interaction failed"
            if not interaction.response.is_done():
                await interaction.response.send_message("Error processing your selection. Please try again.", ephemeral=True)
            else:
                await interaction.followup.send("Error processing your selection. Please try again.", ephemeral=True)
            return

        # Proceed to submit/finalize the vote.
        # submit_vote_callback will handle responding to the interaction (e.g. send_modal or defer).
        await self.submit_vote_callback(interaction)


class RankedVoteView(BaseVoteView):
    """Interactive UI for ranked voting (Borda/Runoff)"""

    def add_mechanism_items(self):
        self._ranked_options = []
        # Add the initial select menu (it will be on row 0 by default if no other row 0 items are added first,
        # or if its row is explicitly set to 0)
        self._add_rank_select_menu()

    def _add_rank_select_menu(self):
        """Helper to create and add a select menu for the next ranking position"""
        # Remove existing select menu if any before adding a new one
        existing_select = discord.utils.get(
            self.children, custom_id=f"rank_select_{self.proposal_id}")
        if existing_select:
            self.remove_item(existing_select)

        remaining_options = [
            opt for opt in self.options if opt not in self._ranked_options]

        if not remaining_options:
            return  # No more options to rank, don't add a select menu

        rank_position = len(self._ranked_options) + 1
        placeholder_text = f"Select your #{rank_position} choice"
        if len(placeholder_text) > 100:
            placeholder_text = placeholder_text[:97] + "..."

        select = discord.ui.Select(
            placeholder=placeholder_text,
            custom_id=f"rank_select_{self.proposal_id}",
            min_values=1,
            max_values=1,
            row=0  # Ensure select menu is on row 0
        )

        select_options = []
        for option in remaining_options:
            label = option
            if len(label) > 100:  # Corrected typo here
                label = label[:97] + "..."
            value = option
            if len(value) > 100:
                value = value[:97] + "..."

            select_options.append(
                discord.SelectOption(label=label, value=value))

        select.options = select_options
        select.callback = self.rank_callback

        # Add the new select menu. Only pass the item.
        self.add_item(select)  # <--- Corrected add_item usage

    def has_selection(self):
        return len(self._ranked_options) > 0

    def get_mechanism_vote_data(self) -> Dict[str, Any]:
        return {"rankings": self._ranked_options}

    async def rank_callback(self, interaction: discord.Interaction):
        # User check and submission check handled by BaseVoteView

        selected_option_value = interaction.data["values"][0]

        if selected_option_value not in self.options or selected_option_value in self._ranked_options:
            print(
                f"WARNING: User {self.user_id} selected invalid/already ranked option '{selected_option_value}' for proposal {self.proposal_id}")
            await interaction.response.send_message(f"Error: '{selected_option_value}' is not a valid option to rank next.", ephemeral=True)
            # Re-send the view to refresh the select options
            await interaction.edit_original_response(view=self)
            return

        self._ranked_options.append(selected_option_value)

        self._add_rank_select_menu()  # Adds the next select menu

        await interaction.response.edit_message(view=self)

        status = "**Your Current Ranking:**\n"
        for i, option in enumerate(self._ranked_options):
            status += f"{i+1}. {option}\n"

        remaining_options_after = [
            opt for opt in self.options if opt not in self._ranked_options]
        if remaining_options_after:
            status += f"\n*Remaining options to rank: {len(remaining_options_after)}*"
        else:
            status += "\n*All options ranked! You can now submit your vote.*"

        if len(status) > 2000:
            status = status[:1997] + "..."

        await interaction.followup.send(status, ephemeral=True)


class ApprovalVoteView(BaseVoteView):
    """Interactive UI for approval voting"""

    def add_mechanism_items(self):
        self._approved_options = []
        max_button_items = 25 - 2
        num_options_to_buttonize = min(len(self.options), max_button_items)

        for i in range(num_options_to_buttonize):
            option = self.options[i]

            button_label = option
            if len(button_label) > 80:
                button_label = button_label[:77] + "..."

            button = discord.ui.Button(
                label=button_label,
                style=discord.ButtonStyle.secondary,
                custom_id=f"approve_{self.proposal_id}_{i}",
                row=i // 5  # Assign rows starting from 0
            )
            button.callback = self.option_callback
            self.add_item(button)

    def has_selection(self):
        return len(self._approved_options) > 0

    def get_mechanism_vote_data(self) -> Dict[str, Any]:
        return {"approved": self._approved_options}

    async def option_callback(self, interaction: discord.Interaction):
        # User check and submission check handled by BaseVoteView

        button_id = interaction.data["custom_id"]
        original_option = None
        try:
            parts = button_id.split("_")
            if len(parts) == 3 and parts[0] == "approve" and int(parts[1]) == self.proposal_id:
                option_index = int(parts[2])
                if 0 <= option_index < len(self.options):
                    original_option = self.options[option_index]
                else:
                     raise ValueError("Option index out of bounds")
            else:
                raise ValueError("Invalid custom_id format")

        except (IndexError, ValueError) as e:
            print(
                f"Error parsing approval button custom_id '{button_id}': {e}")
            await interaction.response.send_message("Error processing button click.", ephemeral=True)
            return

        if original_option is None:
            print(
                f"WARNING: Could not map clicked button custom_id '{button_id}' back to an original option for proposal {self.proposal_id}")
            await interaction.response.send_message("Error processing option selection.", ephemeral=True)
            return

        if original_option in self._approved_options:
            self._approved_options.remove(original_option)
            clicked_button = discord.utils.get(
                self.children, custom_id=button_id)
            if clicked_button:
                clicked_button.style = discord.ButtonStyle.secondary
        else:
            self._approved_options.append(original_option)
            clicked_button = discord.utils.get(
                self.children, custom_id=button_id)
            if clicked_button:
                clicked_button.style = discord.ButtonStyle.primary

        submit_button = discord.utils.get(
            self.children, custom_id=f"submit_vote_{self.proposal_id}")
        abstain_button: Optional[AbstainButton] = discord.utils.get(
            self.children, custom_id=f"abstain_btn_{self.proposal_id}")

        # Submit button is enabled if:
        # 1. An option is selected (self.selected_option is not None)
        # OR
        # 2. Abstain is allowed AND the abstain button exists AND it is selected.
        should_enable_submit = len(self._approved_options) > 0 or \
                               (self.allow_abstain and abstain_button and abstain_button.is_selected)

        submit_button.disabled = not should_enable_submit

        await interaction.response.edit_message(view=self)

        if self._approved_options:
            status = "**Currently Approved Options:**\n"
            sorted_approved = sorted(self._approved_options)
            status_lines = [f"• {opt}" for opt in sorted_approved]
            status_text = "\n".join(status_lines[:10])
            if len(sorted_approved) > 10:
                status_text += f"\n...and {len(sorted_approved) - 10} more."
            status += status_text
        else:
            status = "*No options approved yet. Select one or more options you support.*"

        if len(status) > 2000:
            status = status[:1997] + "..."

        await interaction.followup.send(status, ephemeral=True)

# Keep EarlyTerminationView and ConfirmTerminationView classes if they are here
# They should inherit from discord.ui.View and handle their own checks/callbacks

# ========================
# 🔹 CORE VOTING LOGIC & DM HANDLING
# ========================

async def send_voting_dm(member: discord.Member, proposal: Dict, options: List[str]) -> bool:
    """Sends a direct message to a member with voting options for a proposal."""
    try:
        proposal_id = proposal.get('proposal_id')
        if not proposal_id:
            print(f"ERROR: send_voting_dm: proposal_id missing from proposal data: {proposal}")
            return False

        # Determine voting mechanism and instantiate the correct view
        mechanism = proposal.get('voting_mechanism', 'plurality').lower()
        hyperparameters = proposal.get('hyperparameters', {})
        allow_abstain = hyperparameters.get('allow_abstain', True) if isinstance(hyperparameters, dict) else True

        # Campaign-specific information
        campaign_id = proposal.get('campaign_id')
        campaign_details: Optional[Dict[str, Any]] = None
        user_remaining_tokens: Optional[int] = None

        embed_content = f"## 🗳️ Vote Now: {proposal.get('title', 'N/A')}\n"
        embed_content += f"**Proposal ID:** P#{proposal_id}\n"

        if campaign_id:
            campaign_details = await db.get_campaign(campaign_id)
            if campaign_details:
                # Enroll voter if not already part of the campaign (idempotent)
                await db.enroll_voter_in_campaign(campaign_id, member.id, campaign_details['total_tokens_per_voter'])
                user_remaining_tokens = await db.get_user_remaining_tokens(campaign_id, member.id)
                if user_remaining_tokens is None: # Should not happen after enroll
                    user_remaining_tokens = campaign_details['total_tokens_per_voter']
                    print(f"WARN: User {member.id} had no token record for C#{campaign_id} after attempting enrollment. Defaulting to campaign total.")

                embed_content += f"**Campaign:** {campaign_details.get('title', 'N/A')}\n"
                embed_content += f"**Total Scenarios in Campaign:** {campaign_details.get('num_expected_scenarios', 'N/A')}\n"
                embed_content += f"**Your Remaining Campaign Tokens:** {user_remaining_tokens if user_remaining_tokens is not None else 'N/A'}\n\n"
            else:
                embed_content += "**Campaign:** Error fetching campaign details.\n\n"

        embed_content += f"**Description:** {proposal.get('description', 'No description provided.')}\n"

        # Voting rules and options (improved formatting)
        rules_text = f"\n**Voting Mechanism:** {mechanism.title()}\n"
        if hyperparameters:
            rules_text += "**Parameters:**\n"
            for key, value in hyperparameters.items():
                rules_text += f"  - {key.replace('_', ' ').title()}: {value}\n"

        # Ensure options are clearly listed
        options_text = "\n**Options:**\n" + "\n".join([f"- `{option}`" for option in options]) + "\n"

        embed_content += rules_text
        # Add a separator before options for clarity
        embed_content += "\n---\n"
        embed_content += options_text

        deadline_data = proposal.get('deadline')
        if deadline_data:
            formatted_deadline = format_deadline(deadline_data) # Use existing helper
            embed_content += f"\n**Deadline:** {formatted_deadline}\n"
        else:
            embed_content += "\n**Deadline:** Not set.\n"

        # Construct the view
        view_args = {
            "proposal_id": proposal_id,
            "options": options,
            "user_id": member.id,
            "allow_abstain": allow_abstain,
            "campaign_id": campaign_id,
            "campaign_details": campaign_details,
            "user_remaining_tokens": user_remaining_tokens
        }

        vote_view: BaseVoteView
        if mechanism == 'plurality':
            vote_view = PluralityVoteView(**view_args)
        elif mechanism == 'borda' or mechanism == 'runoff':
            # Assuming RankedVoteView handles both based on a hyperparameter or internal logic if needed,
            # or we might need BordaVoteView and RunoffVoteView subclasses.
            # For now, RankedVoteView is a general placeholder.
            vote_view = RankedVoteView(**view_args) # Add specific hyperparams if needed by RankedVoteView
        elif mechanism == 'approval':
            vote_view = ApprovalVoteView(**view_args)
        # elif mechanism == 'dhondt': # D'Hondt might need a more complex view or be informational
        #     vote_view = DHondtVoteView(**view_args) # Example, if such a view exists
        else:
            # Fallback or error for unsupported mechanisms in DM voting
            print(f"Warning: Unsupported mechanism '{mechanism}' for DM voting view for P#{proposal_id}. Defaulting to Plurality-like.")
            vote_view = PluralityVoteView(**view_args) # Fallback, or handle error

        # Create embed
        embed = discord.Embed(description=embed_content, color=discord.Color.blue())

        # Send DM
        await member.send(embed=embed, view=vote_view)
        print(f"✅ Voting DM sent to {member.name} for proposal #{proposal_id} (Mechanism: {mechanism}, Options: {len(options)}, Allow Abstain: {allow_abstain})")
        return True
    except discord.Forbidden:
        print(f"⚠️ Failed to send DM to {member.name} (ID: {member.id}) - DMs likely disabled.")
        return False
    except Exception as e:
        print(f"❌ Error sending voting DM to {member.name} for proposal #{proposal.get('proposal_id', 'UNKNOWN')}: {e}")
        traceback.print_exc()
        return False

# ... (process_vote, close_proposal, and other functions - keep as is or adapt as needed) ...

# ========================
# 🔹 RESULT CALCULATION (Moved to voting_utils.py for cleaner separation)
# ========================

# The calculate_results function previously here is now in voting_utils.py
# And it is updated there to handle filtering abstain votes and adding the count.

# ========================
# 🔹 HELPER FUNCTIONS (Keep relevant ones here)
# ========================

def format_deadline(deadline_data):
    """Format the deadline data (string or datetime) for display"""
    if isinstance(deadline_data, str):
        try:
            deadline = datetime.fromisoformat(
                deadline_data.replace('Z', '+00:00'))
        except ValueError:
            return "Invalid Date"
    else:  # Assume datetime
        deadline = deadline_data

    return deadline.strftime("%Y-%m-%d %H:%M UTC")


# The update_voting_message function is part of the display logic.
# It might need adjustment if the embed structure changes significantly,
# but the core logic of fetching and editing the message should remain similar.
# Let's keep it here for now.
async def update_voting_message(proposal):
    """Update the main voting message with current vote count and time remaining"""
    # This function is currently not used in favor of update_vote_tracking.
    # We can potentially remove or refactor it.
    # For now, let's leave it but note it's superseded by update_vote_tracking
    print(
        f"INFO: update_voting_message called for proposal {proposal.get('proposal_id')}, but update_vote_tracking is now preferred.")
    pass  # This function is effectively replaced by update_vote_tracking for progress updates

# Keep the calculation methods (count_votes) within the mechanism classes in voting_utils.py.
# They will now only receive non-abstain votes.

# The main calculate_results function needs to be in voting_utils.py as it's called by
# check_expired_proposals and close_proposal (also in voting_utils.py) and also by
# the 100% vote check in process_vote (in voting.py). Putting it in voting_utils.py
# breaks the circular import between voting.py and voting_utils.py.
# Let's move the core calculate_results there and update it.

# The check_expired_proposals function should also be in voting_utils.py.
# close_and_announce_results should also be in voting_utils.py.

# We need to carefully manage imports to avoid circular dependencies.
# voting_utils.py should ideally contain pure functions for calculation and formatting results.
# voting.py should contain the interactive UI and vote processing logic.
# db.py should contain database interactions.
# proposals.py should contain proposal creation and management.
# main.py should contain bot setup, event handlers, and command dispatch.

# Let's refactor calculate_results and related functions into voting_utils.py
# And ensure imports are correct. The version of calculate_results that handles
# filtering abstain votes must be in voting_utils.py now.

# --- END OF FILE voting.py ---
