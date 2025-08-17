import discord
from discord.ext import commands
import asyncio
from datetime import datetime, timedelta, timezone
import json
import db
import random
import re  # Need re for parse_duration fallback if not using utils
from typing import List, Dict, Any, Optional, Union, Tuple
import traceback

# Import functions/classes from voting_utils and utils
import voting_utils  # Import the module to access its functions
from voting_utils import (
    PluralityVoting,
    BordaCount,
    ApprovalVoting,
    RunoffVoting,
    CondorcetMethod,
    get_voting_mechanism,
)
import utils  # Import the module to access its functions
# ========================
# 🔹 INTERACTIVE VOTING UI
# ========================

# In voting.py

# ... (ensure all necessary imports are at the top, including db, voting_utils, utils, discord) ...


async def process_vote(user_id: int, proposal_id: int, vote_data_dict: Dict[str, Any], is_abstain: bool, tokens_invested: Optional[int]) -> Tuple[bool, str]:
    """Process and record a vote using db.record_vote."""
    try:
        print(
            f"DEBUG: process_vote called for Proposal #{proposal_id} U#{user_id}. Abstain: {is_abstain}, Tokens: {tokens_invested}")
        # Basic proposal and status checks (can be expanded)
        proposal = await db.get_proposal(proposal_id)
        if not proposal:
            return False, "Proposal not found."
        if proposal.get('status') != 'Voting':
            return False, f"Voting is not open for this proposal (status: {proposal.get('status', 'Unknown')})."

        # Deadline check (simplified, more robust checks can be added here or in calling function)
        deadline_data = proposal.get('deadline')
        if deadline_data:
            try:
                # Assuming deadline_data is an ISO format string from the database
                deadline_dt = datetime.fromisoformat(deadline_data.replace(
                    'Z', '+00:00')) if 'Z' in deadline_data else datetime.fromisoformat(deadline_data)
                # Ensure deadline_dt is timezone-aware for comparison with timezone-aware datetime.now()
                if deadline_dt.tzinfo is None or deadline_dt.tzinfo.utcoffset(deadline_dt) is None:
                    deadline_dt = deadline_dt.replace(
                        tzinfo=timezone.utc)  # Assume UTC if naive

                if datetime.now(timezone.utc) > deadline_dt:
                    return False, "Voting has ended for this proposal."
            except ValueError:
                print(
                    f"ERROR: Could not parse deadline string '{deadline_data}' in process_vote for Proposal #{proposal_id}")
                # Decide handling: either let vote proceed or return error
                # return False, "Error processing proposal deadline."
                pass  # Or let it proceed if parsing fails, though this might be too lenient

        const_vars = await db.get_constitutional_variables(proposal['server_id'])
        privacy = const_vars.get('vote_privacy', {}).get('value', 'public')
        if privacy == 'anonymous':
            await db.get_or_create_vote_identifier(
                proposal['server_id'], user_id, proposal_id, proposal.get(
                    'campaign_id')
            )

        # vote_data_dict is the mechanism-specific data (e.g., {'option': 'A'} or {'rankings': ['A', 'B']})
        # It should already be validated by the view/modal before this stage.
        vote_json_str = json.dumps(vote_data_dict)

        success = await db.record_vote(
            user_id=user_id,
            proposal_id=proposal_id,
            vote_data=vote_json_str,  # This is the mechanism-specific part
            is_abstain=is_abstain,
            tokens_invested=tokens_invested
        )

        if success:
            # Trigger update of the public tracking message asynchronously
            # Ensure proposal object has server_id for fetching guild
            if proposal.get('server_id') and proposal.get('vote_tracking_message_id'):
                asyncio.create_task(update_voting_message(
                    proposal))  # Fire and forget
            return True, "Vote recorded successfully."
        else:
            return False, "Failed to record vote in the database."

    except Exception as e:
        print(
            f"CRITICAL ERROR in process_vote for Proposal #{proposal_id} U#{user_id}: {e}")
        traceback.print_exc()
        return False, "An internal error occurred while processing your vote."


class AbstainButton(discord.ui.Button):
    def __init__(self, proposal_id: int):
        super().__init__(label="Abstain from Voting", style=discord.ButtonStyle.secondary,
                         custom_id=f"abstain_{proposal_id}_{random.randint(1000, 9999)}")
        self.proposal_id = proposal_id

    async def callback(self, interaction: discord.Interaction):
        # Ensure the view associated with this interaction is BaseVoteView or subclass
        if not isinstance(self.view, BaseVoteView):
            await interaction.response.send_message("Error: Could not process this action.", ephemeral=True)
            return

        # User check and submission check are handled by BaseVoteView.interaction_check
        # and the submit_callback's disabling logic.

        self.view.is_abstain_vote = True
        # For abstain, we directly call finalize_vote. Ensure interaction is deferred first.
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=True)

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
            max_length=len(str(remaining_tokens)
                           ) if remaining_tokens > 0 else 1
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

            await self.base_vote_view.finalize_vote(interaction, tokens_invested_this_scenario=tokens_to_invest)

        except ValueError:
            if interaction.response.is_done():
                await interaction.followup.send("❌ Invalid input. Please enter a whole number.", ephemeral=True)
            else:
                await interaction.response.send_message("❌ Invalid input. Please enter a whole number.", ephemeral=True)
        except Exception as e:
            print(f"Error in TokenInvestmentModal on_submit: {e}")
            traceback.print_exc()
            if interaction.response.is_done():
                await interaction.followup.send("An error occurred while submitting your token investment. Please try again.", ephemeral=True)
            else:
                await interaction.response.send_message("An error occurred while submitting your token investment. Please try again.", ephemeral=True)


class BaseVoteView(discord.ui.View):
    def __init__(self,
                 proposal_id: int,
                 options: List[str],
                 user_id: int,
                 allow_abstain: bool = True,
                 campaign_id: Optional[int] = None,
                 campaign_details: Optional[Dict[str, Any]] = None,
                 user_remaining_tokens: Optional[int] = None):  # Added campaign params
        super().__init__(timeout=86400)  # 24-hour timeout for voting
        self.proposal_id = proposal_id
        self.options = options
        self.user_id = user_id  # The user this DM is intended for
        self.is_submitted = False
        self.is_abstain_vote = False
        # To store mechanism-specific choices before final submission
        self.selected_mechanism_vote_data: Dict[str, Any] = {}

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
        raise NotImplementedError(
            "Subclasses must implement add_mechanism_items")

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
            # Ensure we respond if the interaction isn't already done (e.g. by a previous deferral in Plurality option_callback)
            if not interaction.response.is_done():
                await interaction.response.send_message("You have already submitted your vote for this proposal.", ephemeral=True)
            else:
                await interaction.followup.send("You have already submitted your vote for this proposal.", ephemeral=True)
            return

        # Validation of mechanism-specific selection should happen in the item's callback
        # before this method is called, or right here.
        # For example, PluralityVoteView's option_callback sets self.selected_mechanism_vote_data

        if not self.is_abstain_vote and not self.has_selection():
            if not interaction.response.is_done():
                await interaction.response.send_message("Please make a selection before submitting.", ephemeral=True)
            else:
                await interaction.followup.send("Please make a selection before submitting.", ephemeral=True)
            return

        if self.is_abstain_vote:  # Handled by AbstainButton's callback directly calling finalize_vote
            # This path should ideally not be hit if AbstainButton calls finalize_vote directly after deferring.
            print(
                f"DEBUG: BaseVoteView.submit_vote_callback reached for abstain vote for Proposal #{self.proposal_id}. This should be rare.")
            # Ensure interaction is acknowledged if not already (AbstainButton should have deferred)
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=True, thinking=True)
            await self.finalize_vote(interaction, tokens_invested_this_scenario=0 if self.campaign_id else None)
        elif self.campaign_id is not None and self.user_remaining_tokens is not None:  # Check initial view token count
            # Fetch fresh tokens from DB for accuracy before showing modal or auto-investing 0
            fresh_user_remaining_tokens = await db.get_user_remaining_tokens(self.campaign_id, self.user_id)
            if fresh_user_remaining_tokens is None:
                # This case means the user might not be enrolled or DB error.
                # self.user_remaining_tokens might be from view init, could be stale or from initial enrollment.
                # Defaulting to 0 if DB fetch fails to prevent locking up or allowing investment.
                print(
                    f"WARNING: Failed to fetch fresh tokens for U#{self.user_id} C#{self.campaign_id} in submit_vote_callback. Using 0.")
                fresh_user_remaining_tokens = 0
                # Send an error message and stop further processing for this vote path.
                if not interaction.response.is_done():
                    await interaction.response.send_message("Error: Could not verify your current token balance. Please try again later.", ephemeral=True)
                else:
                    await interaction.followup.send("Error: Could not verify your current token balance. Please try again later.", ephemeral=True)
                return

            if fresh_user_remaining_tokens == 0:  # No tokens left, auto-invest 0
                if not interaction.response.is_done():
                    await interaction.response.defer(ephemeral=True, thinking=True)
                await self.finalize_vote(interaction, tokens_invested_this_scenario=0)
            else:
                # It's a campaign vote with tokens, present the token investment modal.
                # This will be the first response to the interaction.
                try:
                    token_modal = TokenInvestmentModal(
                        base_vote_view=self, remaining_tokens=fresh_user_remaining_tokens)
                    await interaction.response.send_modal(token_modal)
                except discord.errors.InteractionResponded:
                    print(
                        f"WARNING: Interaction already responded to when trying to send modal for P#{self.proposal_id} U#{self.user_id}.")
                    await interaction.followup.send("An error occurred while trying to process your selection. Please try again.", ephemeral=True)
                except Exception as e:
                    print(
                        f"ERROR: Unexpected error sending modal for P#{self.proposal_id} U#{self.user_id}: {e}")
                    traceback.print_exc()
                    if not interaction.response.is_done():
                        await interaction.response.send_message("An unexpected error occurred. Please try again.", ephemeral=True)
                    else:
                        await interaction.followup.send("An unexpected error occurred. Please try again.", ephemeral=True)

        else:
            # Not a campaign vote, or other direct finalize cases
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=True, thinking=True)
            await self.finalize_vote(interaction, tokens_invested_this_scenario=None)

    async def finalize_vote(self, interaction: discord.Interaction, tokens_invested_this_scenario: Optional[int]):
        """
        Finalizes the vote recording process after all selections (including tokens) are made.
        """
        if self.is_submitted:  # Should have been caught by submit_vote_callback or modal prevented re-submission
            # Ensure interaction is acknowledged if not already done.
            if not interaction.response.is_done():
                try:
                    await interaction.response.send_message("You have already submitted your vote for this proposal.", ephemeral=True)
                # Can happen if a quick double click leads here twice.
                except discord.InteractionResponded:
                    await interaction.followup.send("You have already submitted your vote for this proposal.", ephemeral=True)
            else:
                await interaction.followup.send("You have already submitted your vote for this proposal.", ephemeral=True)
            return

        # Ensure the interaction has been deferred if we're doing async work.
        # This is especially important if this function is called directly after a modal.
        # Modal on_submit defers the interaction before calling this.
        # Button callbacks (Plurality option_callback, AbstainButton callback) should defer before calling this path.
        if not interaction.response.is_done():
            print(
                f"WARNING: finalize_vote called with interaction not yet responded/deferred. P#{self.proposal_id}. Forcing defer.")
            try:
                await interaction.response.defer(ephemeral=True, thinking=True)
            except discord.InteractionResponded:
                # Already responded, which is unexpected here but we'll proceed.
                pass

        # Determine vote data based on whether it's an abstain vote
        if self.is_abstain_vote:
            # Standardized abstain data
            actual_vote_data = {"choice": "abstain"}
        else:
            # Get from subclass (Plurality, Ranked, etc.)
            actual_vote_data = self.get_mechanism_vote_data()

        # Safety check for empty vote data if not abstaining
        if not self.is_abstain_vote and not actual_vote_data:
            print(
                f"ERROR: finalize_vote called for non-abstain vote but get_mechanism_vote_data() returned empty. P#{self.proposal_id} U#{self.user_id}")
            # Attempt to respond to the interaction to prevent hanging.
            await interaction.edit_original_response(content="❌ Error: Your selection was not recognized. Please try again.", view=None)
            return

        success = False
        message = "An unexpected error occurred."
        new_remaining_tokens = self.user_remaining_tokens

        # Campaign-specific token validation and update
        if self.campaign_id is not None and tokens_invested_this_scenario is not None:
            # Re-fetch current tokens for atomicity BEFORE recording the vote.
            current_db_tokens = await db.get_user_remaining_tokens(self.campaign_id, self.user_id)

            if current_db_tokens is None:  # User not enrolled or error
                message = "❌ Error: Could not verify your token balance for the campaign."
            elif tokens_invested_this_scenario > current_db_tokens:
                message = f"❌ Error: You tried to invest {tokens_invested_this_scenario} tokens, but you only have {current_db_tokens} left."
            else:
                # Proceed with vote recording and token update
                success, message = await process_vote(
                    self.user_id, self.proposal_id, actual_vote_data, self.is_abstain_vote, tokens_invested_this_scenario
                )
                if success:
                    token_update_success = await db.update_user_remaining_tokens(
                        self.campaign_id, self.user_id, tokens_invested_this_scenario
                    )
                    if token_update_success:
                        new_remaining_tokens = current_db_tokens - tokens_invested_this_scenario
                        message = f"✅ Vote recorded for P#{self.proposal_id} with {tokens_invested_this_scenario} tokens. You have {new_remaining_tokens} tokens left for this campaign."
                        # Also update the view's token count for immediate display if necessary (though DM is usually ephemeral)
                        self.user_remaining_tokens = new_remaining_tokens
                else:
                    # Vote recorded, but token update failed. This is a critical state.
                    message = f"⚠️ Your vote for P#{self.proposal_id} was recorded, but updating your token balance failed. Please contact an admin."
                    print(
                        f"CRITICAL: Vote recorded for P#{self.proposal_id} U#{self.user_id} but token update failed for C#{self.campaign_id}.")
                    # Consider if the vote should be reversed or flagged. For now, alert user.
                    success = False  # Mark as overall failure for UI purposes.
        else:
            # Non-campaign vote
            success, message = await process_vote(
                self.user_id, self.proposal_id, actual_vote_data, self.is_abstain_vote, None
            )
            message = f"✅ Vote recorded for P#{self.proposal_id}." if success else f"❌ {message}"

        # --- Unified Message Content & View Update ---
        # This will be used for the interaction response.
        final_content = message

        self.is_submitted = True  # Mark as submitted
        for item_child in self.children:  # Use different var name to avoid conflict
            if isinstance(item_child, (discord.ui.Button, discord.ui.Select)):
                item_child.disabled = True
        # Ensure the view reflects the selection for Plurality if it's not abstain
        if isinstance(self, PluralityVoteView) and not self.is_abstain_vote and self.selected_option:
            for item_child in self.children:
                if isinstance(item_child, discord.ui.Button) and item_child.label == self.selected_option:
                    item_child.style = discord.ButtonStyle.primary
                elif isinstance(item_child, discord.ui.Button) and item_child.custom_id and item_child.custom_id.startswith(f"plurality_option_{self.proposal_id}_"):
                    item_child.style = discord.ButtonStyle.secondary

        # --- Unified Interaction Response ---
        try:
            # This is the primary way to respond to an interaction that was deferred (especially with thinking=True).
            # It edits the original message from which the interaction (button click, modal submit) originated.
            await interaction.edit_original_response(content=final_content, view=self)
            print(
                f"DEBUG: finalize_vote successfully edited original response for P#{self.proposal_id} U#{self.user_id}. Campaign: {self.campaign_id is not None}. Abstain: {self.is_abstain_vote}")

        except discord.NotFound:
            print(
                f"NotFound error in finalize_vote for P#{self.proposal_id} U#{self.user_id}. Original message likely deleted.")
            # As a fallback, try sending a new ephemeral message if the original is gone.
            try:
                await interaction.followup.send(content=final_content, ephemeral=True)
            except Exception as followup_err:
                print(
                    f"Failed to send followup after NotFound in finalize_vote: {followup_err}")
        except discord.HTTPException as e:
            print(
                f"HTTPException in finalize_vote responding for P#{self.proposal_id} U#{self.user_id}: {e}")
            traceback.print_exc()
            # Fallback for other HTTP errors
            try:
                await interaction.followup.send(content=f"There was an issue updating the message, but: {final_content}", ephemeral=True)
            except Exception as final_followup_err:
                print(
                    f"Failed to send final followup after HTTPException in finalize_vote: {final_followup_err}")

        # If the vote was successful and it's a non-campaign proposal, or if tracking is generally desired,
        # queue an update for the public tracking message.
        if success and self.proposal_id:
            # Re-fetch proposal for server_id
            proposal_data = await db.get_proposal(self.proposal_id)
            # and proposal_data.get('vote_tracking_message_id'): # Tracking msg ID might not exist yet
            if proposal_data and proposal_data.get('server_id'):
                # The update_vote_tracking in voting_utils handles getting/creating the tracking message.
                guild = interaction.client.get_guild(
                    proposal_data['server_id'])
                if guild:
                    # Update the queue in main.py instead of direct call
                    if hasattr(interaction.client, 'update_queue'):
                        await interaction.client.update_queue.put({
                            'guild_id': guild.id,
                            'proposal_id': self.proposal_id
                        })
                        print(
                            f"DEBUG: Queued tracker update for P#{self.proposal_id} after vote in finalize_vote.")
                    else:
                        print(
                            f"WARNING: Bot has no update_queue. Cannot queue tracker update for P#{self.proposal_id}.")

                else:
                    print(
                        f"WARNING: Could not get guild {proposal_data['server_id']} for tracker update from finalize_vote.")

    def get_mechanism_vote_data(self) -> Dict[str, Any]:
        # This should be overridden by subclasses to return the data specific to their mechanism
        # e.g., for Plurality: return {"option": self.selected_option}
        #      for Ranked: return {"rankings": self.selected_rankings}
        # This data will be JSON serialized and stored in the database.
        # Relies on subclasses populating this
        return self.selected_mechanism_vote_data

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

    def __init__(self, proposal_id: int, options: List[str], user_id: int, allow_abstain: bool = True, campaign_id: Optional[int] = None, campaign_details: Optional[Dict[str, Any]] = None, user_remaining_tokens: Optional[int] = None):
        super().__init__(proposal_id, options, user_id, allow_abstain,
                         campaign_id, campaign_details, user_remaining_tokens)
        # Stores the label of the selected option
        self.selected_option: Optional[str] = None

    def add_mechanism_items(self):
        # Add buttons for each option
        for i, option_text in enumerate(self.options):
            button = discord.ui.Button(
                label=option_text,
                style=discord.ButtonStyle.secondary,  # Initial style
                custom_id=f"plurality_option_{self.proposal_id}_{i}"
            )
            button.callback = self.option_callback  # Assign the callback
            self.add_item(button)

    def has_selection(self) -> bool:
        return self.selected_option is not None

    def get_mechanism_vote_data(self) -> Dict[str, Any]:
        return {"option": self.selected_option} if self.selected_option else {}

    async def option_callback(self, interaction: discord.Interaction):
        # User check is already handled by BaseVoteView.interaction_check
        # Submission check is already handled in BaseVoteView.submit_callback and item disabled state

        # Deferral is now handled by submit_vote_callback or if modal is sent, that's the first response.
        # No deferral here to allow submit_vote_callback to send a modal as the initial response if needed.
        if interaction.response.is_done():
            # If already responded (e.g. by a quick double click that got through initial checks)
            # we can't proceed. Log this and inform user if possible.
            print(
                f"WARNING: PluralityVoteView.option_callback called but interaction already responded. P#{self.proposal_id} U#{self.user_id}")
            # Attempt to send a followup if possible, though it might also fail.
            try:
                await interaction.followup.send("Your previous action is still processing or an error occurred. Please wait a moment.", ephemeral=True)
            except:  # noqa
                pass  # If followup fails, nothing much more to do here.
            return

        custom_id_parts = interaction.data['custom_id'].split('_')
        option_index = int(custom_id_parts[-1])
        self.selected_option = self.options[option_index]
        # Update based on selection
        self.selected_mechanism_vote_data = self.get_mechanism_vote_data()

        # Button style updates are now handled in finalize_vote before the edit_original_response,
        # so the view passed to edit_original_response will have the correct styles.
        # No need to edit the message here just for button styles.

        # Proceed to the general submit callback
        await self.submit_vote_callback(interaction)


class RankedVoteView(BaseVoteView):
    """Interactive UI for ranked voting (Borda/Runoff)"""

    def add_mechanism_items(self):
        self._ranked_options = []
        # Add the initial select menu (it will be on row 0 by default if no other row 0 items are added first,
        # or if its row is explicitly set to 0)
        self._add_rank_select_menu()

        # Add Submit Vote button (initially disabled)
        self.submit_button = discord.ui.Button(
            label="Submit Vote",
            style=discord.ButtonStyle.success,
            custom_id=f"submit_ranked_{self.proposal_id}",
            disabled=True,
            row=4  # Place on bottom row
        )
        self.submit_button.callback = self.submit_vote_button_callback
        self.add_item(self.submit_button)

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

        # Update submit button state - enable if at least one option is ranked
        self.submit_button.disabled = False
        # Update submit button label to show count
        self.submit_button.label = f"Submit Vote ({len(self._ranked_options)} ranked)"

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
            status += "\n*All options ranked! Click 'Submit Vote' to finalize.*"

        if len(status) > 2000:
            status = status[:1997] + "..."

        await interaction.followup.send(status, ephemeral=True)

    async def submit_vote_button_callback(self, interaction: discord.Interaction):
        """Handle the submit vote button click"""
        if not self.has_selection():
            await interaction.response.send_message("Please rank at least one option before submitting.", ephemeral=True)
            return

        # Set the mechanism vote data
        self.selected_mechanism_vote_data = self.get_mechanism_vote_data()

        # Call the base submit callback which handles token investment logic
        await self.submit_vote_callback(interaction)


class ApprovalVoteView(BaseVoteView):
    """Interactive UI for approval voting"""

    def add_mechanism_items(self):
        self._approved_options = []
        max_button_items = 25 - 2  # Reserve space for submit button and possibly abstain
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

        # Add Submit Vote button (initially disabled)
        self.submit_button = discord.ui.Button(
            label="Submit Vote",
            style=discord.ButtonStyle.success,
            custom_id=f"submit_approval_{self.proposal_id}",
            disabled=True,  # Initially disabled until at least one option is selected
            row=4  # Place on last row
        )
        self.submit_button.callback = self.submit_button_callback
        self.add_item(self.submit_button)

    def has_selection(self):
        return len(self._approved_options) > 0

    def get_mechanism_vote_data(self) -> Dict[str, Any]:
        return {"approved": self._approved_options}

    def _update_submit_button(self):
        """Update the submit button state based on current selections"""
        self.submit_button.disabled = not self.has_selection()
        if self.has_selection():
            self.submit_button.label = f"Submit Vote ({len(self._approved_options)} selected)"
        else:
            self.submit_button.label = "Submit Vote"

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

        # Defer the interaction response *before* any async operations or sending followups.
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=True)
        else:
            print(
                f"WARNING: ApprovalVoteView.option_callback interaction already responded. P#{self.proposal_id}")

        clicked_button = discord.utils.get(
            self.children, custom_id=button_id)

        if original_option in self._approved_options:
            self._approved_options.remove(original_option)
            if clicked_button:
                clicked_button.style = discord.ButtonStyle.secondary
        else:
            self._approved_options.append(original_option)
            if clicked_button:
                clicked_button.style = discord.ButtonStyle.primary

        self.selected_mechanism_vote_data = self.get_mechanism_vote_data()

        # Update submit button state
        self._update_submit_button()

        # Edit the original message to reflect button style changes immediately.
        try:
            await interaction.edit_original_response(view=self)
        except discord.HTTPException as e:
            print(
                f"Error editing message in ApprovalVoteView.option_callback: {e}")

        # Send ephemeral status update about current selections
        if self._approved_options:
            status = "**Currently Approved Options:**\n"
            sorted_approved = sorted(self._approved_options)
            status_lines = [f"• {opt}" for opt in sorted_approved]
            status_text = "\n".join(status_lines[:10])
            if len(sorted_approved) > 10:
                status_text += f"\n...and {len(sorted_approved) - 10} more."
            status += status_text
            status += f"\n\n*Click 'Submit Vote' when you're ready to finalize your choices.*"
        else:
            status = "*No options approved yet. Select one or more options you support.*"

        if len(status) > 2000:
            status = status[:1997] + "..."

        # Send status as a followup
        try:
            await interaction.followup.send(status, ephemeral=True)
        except discord.HTTPException as e:
            print(
                f"Error sending followup status in ApprovalVoteView.option_callback: {e}")

        # NOTE: Do NOT call submit_vote_callback here anymore!
        # User must click the Submit Vote button when ready

    async def submit_button_callback(self, interaction: discord.Interaction):
        """Handle the Submit Vote button click"""
        if not self.has_selection():
            await interaction.response.send_message("Please select at least one option before submitting.", ephemeral=True)
            return

        # Proceed to the general submit callback
        await self.submit_vote_callback(interaction)

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
            print(
                f"ERROR: send_voting_dm: proposal_id missing from proposal data: {proposal}")
            return False

        # Determine voting mechanism and instantiate the correct view
        mechanism = proposal.get('voting_mechanism', 'plurality').lower()
        hyperparameters = proposal.get('hyperparameters', {})

        # FIX: Ensure hyperparameters is a dict, not a string
        if isinstance(hyperparameters, str):
            try:
                hyperparameters = json.loads(
                    hyperparameters) if hyperparameters.strip() else {}
            except json.JSONDecodeError:
                print(
                    f"WARNING: Failed to parse hyperparameters JSON for P#{proposal_id}: {hyperparameters}")
                hyperparameters = {}
        elif not isinstance(hyperparameters, dict):
            hyperparameters = {}

        allow_abstain = hyperparameters.get(
            'allow_abstain', True) if isinstance(hyperparameters, dict) else True

        # Campaign-specific information
        campaign_id = proposal.get('campaign_id')
        campaign_details: Optional[Dict[str, Any]] = None
        user_remaining_tokens: Optional[int] = None

        embed_content = f"## 🗳️ Vote Now: {proposal.get('title', 'N/A')}\n"
        embed_content += f"**Proposal ID:** #{proposal_id}\n"

        if campaign_id:
            campaign_details = await db.get_campaign(campaign_id)
            if campaign_details:
                # Enroll voter if not already part of the campaign (idempotent)
                await db.enroll_voter_in_campaign(campaign_id, member.id, campaign_details['total_tokens_per_voter'])
                user_remaining_tokens = await db.get_user_remaining_tokens(campaign_id, member.id)
                if user_remaining_tokens is None:  # Should not happen after enroll
                    user_remaining_tokens = campaign_details['total_tokens_per_voter']
                    print(
                        f"WARN: User {member.id} had no token record for C#{campaign_id} after attempting enrollment. Defaulting to campaign total.")

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
        options_text = "\n**Options:**\n" + \
            "\n".join([f"- `{option}`" for option in options]) + "\n"

        embed_content += rules_text
        # Add a separator before options for clarity
        embed_content += "\n---\n"
        embed_content += options_text

        deadline_data = proposal.get('deadline')
        if deadline_data:
            formatted_deadline = format_deadline(
                deadline_data)  # Use existing helper
            embed_content += f"\n**Deadline:** {formatted_deadline}\n"
        else:
            embed_content += "\n**Deadline:** Not set.\n"

        # Determine vote privacy settings and fetch anonymous identifier if needed
        const_vars = await db.get_constitutional_variables(proposal['server_id'])
        privacy = const_vars.get('vote_privacy', {}).get('value', 'public')
        identifier_embed = None
        vote_identifier = None
        if privacy == 'anonymous':
            vote_identifier = await db.get_or_create_vote_identifier(
                proposal['server_id'], member.id, proposal_id, campaign_id
            )
            identifier_embed = discord.Embed(
                description=(
                    f"Your anonymous voting identifier is **{vote_identifier}**."
                ),
                color=discord.Color.gold(),
            )

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
        elif mechanism in ['borda', 'runoff', 'condorcet']:
            # Assuming RankedVoteView handles ranked mechanisms based on a hyperparameter or internal logic if needed,
            # or we might need BordaVoteView and RunoffVoteView subclasses.
            # For now, RankedVoteView is a general placeholder.
            # Add specific hyperparams if needed by RankedVoteView
            # Add specific hyperparams if needed by RankedVoteView
            vote_view = RankedVoteView(**view_args)
        elif mechanism == 'approval':
            vote_view = ApprovalVoteView(**view_args)
        else:
            # Fallback or error for unsupported mechanisms in DM voting
            print(
                f"Warning: Unsupported mechanism '{mechanism}' for DM voting view for Proposal #{proposal_id}. Defaulting to Plurality-like."
            )
           # Fallback, or handle error
            vote_view = PluralityVoteView(**view_args)

        # Create embed
        embed = discord.Embed(description=embed_content,
                              color=discord.Color.blue())

        # Send DM including identifier embed when applicable
        if identifier_embed:
            await member.send(embeds=[embed, identifier_embed], view=vote_view)
        else:
            await member.send(embed=embed, view=vote_view)
        print(
            f"✅ Voting DM sent to {member.name} for proposal #{proposal_id} (Mechanism: {mechanism}, Options: {len(options)}, Allow Abstain: {allow_abstain})")
        return True
    except discord.Forbidden:
        print(
            f"⚠️ Failed to send DM to {member.name} (ID: {member.id}) - DMs likely disabled.")
        return False
    except Exception as e:
        print(
            f"❌ Error sending voting DM to {member.name} for proposal #{proposal.get('proposal_id', 'UNKNOWN')}: {e}")
        traceback.print_exc()
        return False


async def send_campaign_scenario_dms_to_user(member: discord.Member, scenarios_data: List[Dict[str, Any]]) -> bool:
    """
    Sends multiple DMs to a user for a batch of campaign scenarios.
    Each DM will use the same initial token balance provided in scenarios_data.
    Args:
        member: The discord.Member to send DMs to.
        scenarios_data: A list of dictionaries, where each dict contains:
            'proposal_dict': The full proposal dictionary for the scenario.
            'options_list': List of option strings for the scenario.
            'campaign_id': The ID of the campaign.
            'campaign_title': The title of the campaign.
            'user_initial_tokens_for_dm_batch': The user's token balance at the start of this DM batch.
    Returns:
        True if all DMs were attempted (some may fail individually), False if a major error occurs early.
    """
    if not scenarios_data:
        print(
            f"WARN: send_campaign_scenario_dms_to_user called for {member.name} with no scenario data.")
        return True  # No DMs to send, not a failure of this function itself

    print(
        f"INFO: Preparing to send batch of {len(scenarios_data)} scenario DMs to {member.name} for C#{scenarios_data[0].get('campaign_id')}")

    # It's important that `send_voting_dm` or a similar specialized function
    # is adapted to take the `user_initial_tokens_for_dm_batch` and pass it correctly
    # to the View initialization.
    # For now, we will adapt the core logic of `send_voting_dm` here for each scenario.

    num_successful_sends = 0
    num_failed_sends = 0

    for scenario_info in scenarios_data:
        proposal = scenario_info['proposal_dict']
        options = scenario_info['options_list']
        campaign_id = scenario_info['campaign_id']
        campaign_title = scenario_info['campaign_title']
        # THIS IS KEY: Use the token balance that was fetched ONCE for the whole batch for this user
        user_tokens_for_this_dm_view = scenario_info['user_initial_tokens_for_dm_batch']

        try:
            proposal_id = proposal.get('proposal_id')
            if not proposal_id:
                print(
                    f"ERROR: send_campaign_scenario_dms_to_user: proposal_id missing for {member.name}, scenario: {proposal.get('title')}")
                num_failed_sends += 1
                continue

            mechanism = proposal.get('voting_mechanism', 'plurality').lower()
            hyperparameters = proposal.get('hyperparameters', {})

            # FIX: Ensure hyperparameters is a dict, not a string
            if isinstance(hyperparameters, str):
                try:
                    hyperparameters = json.loads(
                        hyperparameters) if hyperparameters.strip() else {}
                except json.JSONDecodeError:
                    print(
                        f"WARNING: Failed to parse hyperparameters JSON for P#{proposal_id}: {hyperparameters}")
                    hyperparameters = {}
            elif not isinstance(hyperparameters, dict):
                hyperparameters = {}

            allow_abstain = hyperparameters.get(
                'allow_abstain', True) if isinstance(hyperparameters, dict) else True

            embed_content = f"## 🗳️ Vote Now (Campaign Scenario): {proposal.get('title', 'N/A')}\n"
            embed_content += f"**Campaign:** {campaign_title} (C#{campaign_id})\n"
            embed_content += f"**Proposal ID:** #{proposal_id} (Scenario S#{proposal.get('scenario_order', 'N/A')})\n"
            embed_content += f"**Your Remaining Campaign Tokens (at start of this batch):** {user_tokens_for_this_dm_view if user_tokens_for_this_dm_view is not None else 'N/A'}\n\n"
            embed_content += f"**Description:** {proposal.get('description', 'No description provided.')}\n"

            rules_text = f"\n**Voting Mechanism:** {mechanism.title()}\n"
            if hyperparameters:
                rules_text += "**Parameters:**\n"
                for key, value in hyperparameters.items():
                    rules_text += f"  - {key.replace('_', ' ').title()}: {value}\n"

            options_text = "\n**Options:**\n" + \
                "\n".join([f"- `{option}`" for option in options]) + "\n"
            embed_content += rules_text
            embed_content += "\n---\n"
            embed_content += options_text

            deadline_data = proposal.get('deadline')
            if deadline_data:
                formatted_deadline = format_deadline(deadline_data)
                embed_content += f"\n**Deadline:** {formatted_deadline}\n"
            else:
                embed_content += "\n**Deadline:** Not set.\n"

            view_args = {
                "proposal_id": proposal_id,
                "options": options,
                "user_id": member.id,
                "allow_abstain": allow_abstain,
                "campaign_id": campaign_id,
                # Potentially pass this in scenario_info if fetched once
                "campaign_details": await db.get_campaign(campaign_id),
                # CRITICAL: pass the batch-consistent token count
                "user_remaining_tokens": user_tokens_for_this_dm_view
            }

            vote_view: BaseVoteView
            if mechanism == 'plurality':
                vote_view = PluralityVoteView(**view_args)
            elif mechanism in ['borda', 'runoff', 'condorcet']:
                vote_view = RankedVoteView(**view_args)
            elif mechanism == 'approval':
                vote_view = ApprovalVoteView(**view_args)
            else:
                print(
                    f"Warning: Unsupported mechanism '{mechanism}' for DM view for P#{proposal_id}. Defaulting.")
                vote_view = PluralityVoteView(**view_args)

            embed = discord.Embed(description=embed_content,
                                  color=discord.Color.blue())
            await member.send(embed=embed, view=vote_view)
            print(
                f"✅ Campaign Scenario DM sent to {member.name} for P#{proposal_id} (C#{campaign_id}, S#{proposal.get('scenario_order')})")
            num_successful_sends += 1

        except discord.Forbidden:
            print(
                f"⚠️ Failed to send Campaign Scenario DM to {member.name} (ID: {member.id}) for P#{proposal.get('proposal_id')} - DMs likely disabled.")
            num_failed_sends += 1
        except Exception as e:
            print(
                f"❌ Error sending Campaign Scenario DM to {member.name} for P#{proposal.get('proposal_id')}: {e}")
            traceback.print_exc()
            num_failed_sends += 1

    print(
        f"INFO: Batch DM sending for {member.name} for C#{scenarios_data[0].get('campaign_id')}: {num_successful_sends} sent, {num_failed_sends} failed.")
    # Returns True if any attempt was made, individual failures are logged.
    # Could be changed to `return num_successful_sends > 0` if at least one must succeed.
    return True

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
