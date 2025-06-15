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
import sys # Added for getattr

# Import functions/classes from voting_utils and utils
import voting_utils  # Import the module to access its functions
import utils  # Import the module to access its functions
# ========================
# 🔹 INTERACTIVE VOTING UI
# ========================

# In voting.py

# ... (ensure all necessary imports are at the top, including db, voting_utils, utils, discord) ...

async def process_vote(user_id: int, proposal_id: int, vote_data: Dict[str, Any]) -> Tuple[bool, str]:
    """Process and record a vote, including campaign token logic and updated vote_data structure."""
    try:
        print(f"DEBUG: Processing vote for proposal {proposal_id} from user {user_id} with vote_data: {vote_data}")

        proposal = await db.get_proposal(proposal_id)
        if not proposal:
            return False, "Proposal not found or invalid proposal ID."
        if proposal.get('status') != 'Voting':
            return False, f"Voting is not open for this proposal (status: {proposal.get('status', 'Unknown')})."

        # Deadline check
        deadline_data = proposal.get('deadline')
        if deadline_data:
            try:
                deadline_dt = datetime.fromisoformat(deadline_data.replace('Z', '+00:00')) if isinstance(deadline_data, str) else deadline_data
                if datetime.now() > deadline_dt:
                    return False, "Voting has ended for this proposal."
            except ValueError:
                print(f"ERROR: Could not parse deadline string for expiry check: '{deadline_data}' for proposal {proposal_id}")
                pass # Allow vote if deadline unparseable, but log it

        # Extract core voting decision and campaign-specific fields
        is_abstain = vote_data.get('did_abstain', False)
        tokens_invested = vote_data.get('tokens_invested') # Will be None if not a campaign vote or no tokens invested

        # Prepare the vote_details_for_json (data that goes into the vote_data JSON column)
        # This excludes the fields that are now separate columns in the 'votes' table.
        vote_details_for_json = {k: v for k, v in vote_data.items() if k not in ['did_abstain', 'tokens_invested']}

        # Validate mechanism-specific vote data if not abstaining
        if not is_abstain:
            mechanism_name = proposal.get('voting_mechanism', 'Unknown').lower()
            options = await db.get_proposal_options(proposal_id) # Fetch options for validation
            if not options: options = ["Yes", "No"] # Fallback

            # Simplified validation example (actual validation is more complex and exists in the older process_vote)
            # This part needs to be re-integrated with the comprehensive validation logic from the original process_vote
            if mechanism_name in ["plurality", "dhondt"]:
                chosen_option = vote_details_for_json.get('option')
                if not isinstance(chosen_option, str) or chosen_option.strip() not in [opt.strip() for opt in options]:
                    return False, f"Invalid option '{chosen_option}'. Please choose from available options."
                vote_details_for_json['option'] = chosen_option.strip()
            elif mechanism_name in ["borda", "runoff"]:
                rankings = vote_details_for_json.get('rankings', [])
                # (Add comprehensive validation for rankings: type, uniqueness, valid options)
                if not rankings: return False, "Rankings cannot be empty for this mechanism."
                vote_details_for_json['rankings'] = [r.strip() for r in rankings]
            elif mechanism_name == "approval":
                approved = vote_details_for_json.get('approved', [])
                # (Add comprehensive validation for approved: type, valid options)
                if not approved: return False, "Approved options cannot be empty for this mechanism."
                vote_details_for_json['approved'] = [a.strip() for a in approved]
            # Add more validation as per original process_vote if needed

        # Record the vote in the database
        # db.record_vote now expects: user_id, proposal_id, vote_data_json_str, is_abstain, tokens_invested
        record_success = await db.record_vote(
            user_id=user_id,
            proposal_id=proposal_id,
            vote_data=json.dumps(vote_details_for_json), # Serialize only the mechanism-specific part
            is_abstain=is_abstain,
            tokens_invested=tokens_invested
        )

        if not record_success:
            return False, "Failed to record vote in the database."

        message = "Your vote has been updated." if await db.get_user_vote(proposal_id, user_id) else "Your vote has been recorded."

        # Update vote tracking message (simplified, actual logic involves bot instance)
        guild = None
        if proposal.get('server_id'):
            try:
                # This is a common pattern, consider a helper if used frequently
                import main
                if hasattr(main, 'bot') and main.bot:
                    guild = main.bot.get_guild(proposal.get('server_id'))
                    if guild and hasattr(main.bot, 'update_queue') and main.bot.update_queue:
                        await main.bot.update_queue.put({'guild_id': proposal['server_id'], 'proposal_id': proposal_id})
                    elif guild: # Fallback if queue doesn't exist
                        await voting_utils.update_vote_tracking(guild, proposal_id)
            except ImportError:
                print("WARN: main or main.bot not available in process_vote for tracking update.")
            except Exception as e_track:
                print(f"Error during vote tracking update: {e_track}")

        # Check for 100% vote completion to auto-close (simplified)
        all_votes = await db.get_proposal_votes(proposal_id)
        invited_voter_ids = await db.get_invited_voters_ids(proposal_id) or []
        if invited_voter_ids and len(all_votes) >= len(invited_voter_ids):
            voted_user_ids = {vote.get('user_id') for vote in all_votes}
            if all(uid in voted_user_ids for uid in invited_voter_ids):
                print(f"DEBUG: All {len(invited_voter_ids)} invited users voted for P#{proposal_id}. Auto-closing.")
                results = await voting_utils.close_proposal(proposal_id, guild) # Pass guild if available for announcement
                if results:
                    message += " All invited users have voted. Proposal closed and results processed."
                    # Announcement logic is now within close_proposal or a subsequent task
                else:
                    message += " All invited users have voted, but result processing failed."

        return True, message

    except Exception as e:
        print(f"CRITICAL ERROR in process_vote for P#{proposal_id} U#{user_id}: {e}")
        traceback.print_exc()
        return False, f"An unexpected error occurred: {str(e)}"


class AbstainButton(discord.ui.Button):
    def __init__(self, proposal_id: int, campaign_id: Optional[int] = None, scenario_order: Optional[int] = None):
        custom_id_suffix = f"_{campaign_id}_{scenario_order}" if campaign_id and scenario_order else ""
        super().__init__(
            label="Abstain from Voting",
            style=discord.ButtonStyle.secondary,
            custom_id=f"abstain_vote_{proposal_id}{custom_id_suffix}",
            row=3
        )
        self.proposal_id = proposal_id
        # self.campaign_id = campaign_id # Not strictly needed on button if view handles context
        # self.scenario_order = scenario_order

    async def callback(self, interaction: discord.Interaction):
        # interaction_check in BaseVoteView should handle user and submission status
        if not await self.view.interaction_check(interaction): # Explicitly call view's check
             return

        self.view.is_abstain_vote = True
        self.view.selected_mechanism_vote_data = {} # Clear any mechanism selection

        # Visually indicate abstain is chosen and disable other options
        self.style = discord.ButtonStyle.primary
        self.disabled = True

        for item in self.view.children:
            if item != self and item != self.view.submit_button: # Don't disable self or submit
                if hasattr(item, 'disabled'):
                    item.disabled = True

        self.view.submit_button.disabled = False # Enable submit button

        await interaction.response.edit_message(view=self.view)
        # Optional: followup message
        # await interaction.followup.send("You have chosen to abstain. Click 'Submit Vote' to confirm.", ephemeral=True)


class TokenInvestmentModal(discord.ui.Modal, title="Invest Campaign Tokens"):
    def __init__(self, parent_view: 'BaseVoteView', proposal_id: int, campaign_id: int, scenario_order: int, max_tokens_to_invest: Optional[int]):
        super().__init__()
        self.parent_view = parent_view
        self.proposal_id = proposal_id
        self.campaign_id = campaign_id
        self.scenario_order = scenario_order
        self.max_tokens_to_invest = max_tokens_to_invest if max_tokens_to_invest is not None else 0 # Default to 0 if None

        self.tokens_input = discord.ui.TextInput(
            label=f"Tokens to invest (Max: {self.max_tokens_to_invest})",
            placeholder=f"Enter a number (0 to {self.max_tokens_to_invest})",
            required=True,
            min_length=1,
            max_length=len(str(self.max_tokens_to_invest)) if self.max_tokens_to_invest > 0 else 1
        )
        self.add_item(self.tokens_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True) # Defer modal submission
        try:
            tokens_to_invest = int(self.tokens_input.value)
            if not (0 <= tokens_to_invest <= self.max_tokens_to_invest):
                await interaction.followup.send(f"Invalid token amount. Please enter between 0 and {self.max_tokens_to_invest}.", ephemeral=True)
                return
        except ValueError:
            await interaction.followup.send("Invalid input. Please enter a number for tokens.", ephemeral=True)
            return

        # Call back to the parent view to finalize the vote with token info
        await self.parent_view.finalize_vote(interaction, tokens_invested_this_scenario=tokens_to_invest)
        # The finalize_vote method will handle sending the final ephemeral message or editing the DM.


class BaseVoteView(discord.ui.View):
    def __init__(self, proposal_id: int, options: List[str],
                 proposal_hyperparameters: Dict[str, Any],
                 campaign_id: Optional[int] = None,
                 scenario_order: Optional[int] = None,
                 user_total_campaign_tokens: Optional[int] = None, # This is user's REMAINING tokens for CAMPAIGN
                 original_interaction_user_id: Optional[int] = None
                ):
        super().__init__(timeout=1800) # 30 mins
        self.proposal_id = proposal_id
        self.options = options
        self.proposal_hyperparameters = proposal_hyperparameters if proposal_hyperparameters else {}
        self.campaign_id = campaign_id
        self.scenario_order = scenario_order
        self.user_remaining_campaign_tokens_at_dm_send = user_total_campaign_tokens
        self.original_interaction_user_id = original_interaction_user_id

        self.is_submitted = False
        self.is_abstain_vote = False
        self.selected_mechanism_vote_data: Dict[str, Any] = {}

        self.allow_abstain = self.proposal_hyperparameters.get('allow_abstain', True)

        self.add_mechanism_items()

        if self.allow_abstain:
            abstain_button = AbstainButton(proposal_id, campaign_id, scenario_order)
            self.add_item(abstain_button)

        custom_id_suffix = f"_{self.campaign_id}_{self.scenario_order}" if self.campaign_id and self.scenario_order else ""
        self.submit_button = discord.ui.Button(
            label="Submit Vote",
            style=discord.ButtonStyle.success,
            custom_id=f"submit_vote_{self.proposal_id}{custom_id_suffix}",
            disabled=True,
            row=4
        )
        self.submit_button.callback = self.submit_vote_callback
        self.add_item(self.submit_button)

    def add_mechanism_items(self):
        raise NotImplementedError("Subclasses must implement add_mechanism_items")

    def has_selection(self) -> bool:
        # Must be true if is_abstain_vote is true, or if mechanism has specific selection
        if self.is_abstain_vote:
            return True
        # Subclasses will provide specific logic for their selections
        raise NotImplementedError("Subclasses must implement has_selection for mechanism choices")

    def get_mechanism_vote_data(self) -> Dict[str, Any]:
        return self.selected_mechanism_vote_data

    async def submit_vote_callback(self, interaction: discord.Interaction):
        if not await self.interaction_check(interaction): # Handles submitted and user checks
            return

        if not self.is_abstain_vote and not self.has_selection(): # Check if a choice was made
             await interaction.response.send_message("Please make a selection or choose to abstain before submitting.", ephemeral=True)
             return

        # Defer immediately as we might send a modal
        await interaction.response.defer(ephemeral=True, thinking=True)

        if self.campaign_id:
            max_tokens = self.user_remaining_campaign_tokens_at_dm_send
            # If for some reason max_tokens is None (e.g., error fetching), default to 0 to be safe or handle error
            if max_tokens is None:
                print(f"Warning: user_remaining_campaign_tokens_at_dm_send is None for C:{self.campaign_id} U:{interaction.user.id}. Defaulting max invest to 0.")
                max_tokens = 0

            token_modal = TokenInvestmentModal(
                parent_view=self,
                proposal_id=self.proposal_id,
                campaign_id=self.campaign_id,
                scenario_order=self.scenario_order,
                max_tokens_to_invest=max_tokens
            )
            # Send modal as a followup to the deferred interaction
            await interaction.followup.send_modal(token_modal)
        else:
            await self.finalize_vote(interaction, tokens_invested_this_scenario=None)

    async def finalize_vote(self, interaction: discord.Interaction, tokens_invested_this_scenario: Optional[int]):
        # interaction here is the one that triggered submit_vote_callback or the modal's on_submit.
        # It should already be deferred.

        final_vote_data = {}
        if self.is_abstain_vote:
            final_vote_data["did_abstain"] = True
        else:
            final_vote_data = self.get_mechanism_vote_data()
            final_vote_data["did_abstain"] = False

        if self.campaign_id: # This check ensures tokens_invested is only added for campaigns
            final_vote_data["tokens_invested"] = tokens_invested_this_scenario
        else:
            final_vote_data["tokens_invested"] = None

        success, message = await process_vote(
            user_id=interaction.user.id,
            proposal_id=self.proposal_id,
            vote_data=final_vote_data
        )

        feedback_message_content = ""
        if success:
            self.is_submitted = True
            for item in self.children:
                item.disabled = True

            feedback_message_content = f"✅ Your vote for Proposal #{self.proposal_id} has been recorded. ({message})"
            if self.campaign_id and tokens_invested_this_scenario is not None:
                feedback_message_content = f"✅ Your vote for Scenario {self.scenario_order} (P#{self.proposal_id}) with **{tokens_invested_this_scenario} token(s)** invested has been recorded. ({message})"

            # Update user's campaign tokens in the database
            if self.campaign_id and tokens_invested_this_scenario is not None and tokens_invested_this_scenario > 0:
                token_update_success = await db.update_user_remaining_tokens(
                    campaign_id=self.campaign_id,
                    user_id=interaction.user.id,
                    tokens_spent=tokens_invested_this_scenario
                )
                if not token_update_success:
                    print(f"CRITICAL: Failed to update user tokens for C:{self.campaign_id} U:{interaction.user.id} after vote.")
                    # Append to feedback message for user
                    feedback_message_content += "\n⚠️ *There was an issue updating your campaign token balance. Please contact an admin.*"
        else: # Vote processing failed
            feedback_message_content = f"❌ Vote submission failed: {message}"
            # Do not disable buttons if submit failed, allow retry (unless it was campaign modal flow where modal handles retry)
            if not self.campaign_id: # For non-campaigns, re-enable submit
                 self.submit_button.disabled = False


        # Attempt to edit the original DM message first.
        # The interaction that calls finalize_vote (either from submit_vote_callback or modal on_submit)
        # should already be deferred. We use its followup to send the final status.
        if interaction.message and interaction.message.author.id == interaction.client.user.id: # Check if it's a DM from the bot
            try:
                # Create a simple embed for the final DM status
                final_embed = interaction.message.embeds[0] if interaction.message.embeds else discord.Embed(title="Vote Submitted")
                final_embed.description = feedback_message_content # Replace description with outcome
                final_embed.clear_fields() # Clear old fields like options etc.
                await interaction.message.edit(embed=final_embed, view=self) # view=self shows disabled buttons
                # If edit is successful, we don't need a followup from the interaction that called finalize_vote
                return
            except discord.HTTPException as e:
                print(f"Error editing DM message after vote: {e}. Will send followup.")

        # If DM edit fails or not applicable, send a followup to the interaction that called finalize_vote
        if interaction.response.is_done(): # Check if interaction was deferred
            await interaction.followup.send(feedback_message_content, ephemeral=True)
        else:
            # This case should ideally not be reached if interactions are deferred.
            try:
                await interaction.response.send_message(feedback_message_content, ephemeral=True)
            except discord.InteractionResponded: # If somehow already responded
                 await interaction.followup.send(feedback_message_content, ephemeral=True)


    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.original_interaction_user_id and interaction.user.id != self.original_interaction_user_id:
            await interaction.response.send_message("This voting interface is not for you.", ephemeral=True)
            return False

        if self.is_submitted:
            # If interaction is for an already submitted view, just inform them.
            # Don't defer if it's just a check for an already submitted view.
            # Check if response has already been sent for this specific interaction.
            if not interaction.response.is_done():
                 await interaction.response.send_message("You have already submitted your vote for this proposal/scenario.", ephemeral=True)
            else: # If already responded (e.g. from a quick double click), try followup.
                try:
                    await interaction.followup.send("You have already submitted your vote for this proposal/scenario.", ephemeral=True)
                except discord.HTTPException: # Can happen if initial response was also a followup
                    pass
            return False
        return True


class PluralityVoteView(BaseVoteView):
    """Interactive UI for plurality voting"""

    def add_mechanism_items(self):
        self.selected_option_value: Optional[str] = None # Store the value of the selected option
        for i, option_text in enumerate(self.options):
            btn = discord.ui.Button(
                label=option_text[:80], # Max label length
                style=discord.ButtonStyle.secondary,
                custom_id=f"plurality_{self.proposal_id}_{i}"
                # Row will be auto-managed or can be set explicitly if needed
            )
            btn.callback = self.option_callback
            self.add_item(btn)

    def has_selection(self) -> bool:
        return self.selected_option_value is not None

    def get_mechanism_vote_data(self) -> Dict[str, Any]:
        return {"option": self.selected_option_value} if self.selected_option_value else {}

    async def option_callback(self, interaction: discord.Interaction):
        if not await self.view.interaction_check(interaction): return

        custom_id = interaction.data["custom_id"]
        clicked_option_index = int(custom_id.split("_")[-1])
        self.selected_option_value = self.options[clicked_option_index]
        self.is_abstain_vote = False # A specific option was chosen

        for item in self.children:
            if isinstance(item, discord.ui.Button):
                if item.custom_id == custom_id: # Clicked button
                    item.style = discord.ButtonStyle.primary
                    item.disabled = True # Optionally disable after selection
                elif item.custom_id.startswith(f"plurality_{self.proposal_id}_"): # Other plurality option buttons
                    item.style = discord.ButtonStyle.secondary
                    item.disabled = True # Disable other options
                elif item.custom_id.startswith(f"abstain_vote_{self.proposal_id}"): # Abstain button
                    item.style = discord.ButtonStyle.secondary # Reset style
                    item.disabled = True # Disable abstain if an option is chosen

        self.submit_button.disabled = False
        await interaction.response.edit_message(view=self)
        # await interaction.followup.send(f"You selected: {self.selected_option_value}. Click 'Submit Vote'.", ephemeral=True)


class RankedVoteView(BaseVoteView):
    """Interactive UI for ranked voting (Borda/Runoff)"""

    def add_mechanism_items(self):
        self.current_ranking: List[str] = []
        self._add_rank_select_menu()

    def _add_rank_select_menu(self):
        # Remove existing select menu
        existing_select = discord.utils.get(self.children, custom_id=f"rank_select_{self.proposal_id}")
        if existing_select:
            self.remove_item(existing_select)

        remaining_options = [opt for opt in self.options if opt not in self.current_ranking]
        if not remaining_options: # All options ranked
            self.submit_button.disabled = False # Enable submit if all ranked
            return

        rank_num = len(self.current_ranking) + 1
        select = discord.ui.Select(
            placeholder=f"Select your #{utils.get_ordinal_suffix(rank_num)} choice",
            custom_id=f"rank_select_{self.proposal_id}",
            options=[discord.SelectOption(label=opt[:100], value=opt) for opt in remaining_options]
        )
        select.callback = self.rank_callback
        self.add_item(select)

    def has_selection(self) -> bool:
        # For ranked, consider a selection made if at least one option is ranked
        return len(self.current_ranking) > 0

    def get_mechanism_vote_data(self) -> Dict[str, Any]:
        return {"rankings": self.current_ranking}

    async def rank_callback(self, interaction: discord.Interaction):
        if not await self.view.interaction_check(interaction): return

        selected_value = interaction.data["values"][0]
        self.current_ranking.append(selected_value)
        self.is_abstain_vote = False

        # Disable abstain button if a ranking is made
        abstain_btn = discord.utils.get(self.children, custom_id=lambda x: x and x.startswith(f"abstain_vote_{self.proposal_id}"))
        if abstain_btn:
            abstain_btn.disabled = True
            abstain_btn.style = discord.ButtonStyle.secondary

        self._add_rank_select_menu() # Re-adds select menu with remaining options (or enables submit)

        # Enable submit if at least one ranking, or all options are ranked
        self.submit_button.disabled = not self.has_selection()

        await interaction.response.edit_message(view=self)
        # rank_status = "\n".join(f"{i+1}. {opt}" for i, opt in enumerate(self.current_ranking))
        # await interaction.followup.send(f"Current ranking:\n{rank_status}", ephemeral=True)


class ApprovalVoteView(BaseVoteView):
    """Interactive UI for approval voting"""

    def add_mechanism_items(self):
        self.approved_values: List[str] = []
        for i, option_text in enumerate(self.options):
            btn = discord.ui.Button(
                label=option_text[:80],
                style=discord.ButtonStyle.secondary,
                custom_id=f"approval_{self.proposal_id}_{i}"
            )
            btn.callback = self.option_callback
            self.add_item(btn)

    def has_selection(self) -> bool:
        return len(self.approved_values) > 0

    def get_mechanism_vote_data(self) -> Dict[str, Any]:
        return {"approved": self.approved_values}

    async def option_callback(self, interaction: discord.Interaction):
        if not await self.view.interaction_check(interaction): return

        custom_id = interaction.data["custom_id"]
        clicked_option_index = int(custom_id.split("_")[-1])
        option_value = self.options[clicked_option_index]

        clicked_button = discord.utils.get(self.children, custom_id=custom_id)

        if option_value in self.approved_values:
            self.approved_values.remove(option_value)
            if clicked_button: clicked_button.style = discord.ButtonStyle.secondary
        else:
            self.approved_values.append(option_value)
            if clicked_button: clicked_button.style = discord.ButtonStyle.primary

        self.is_abstain_vote = False

        # Disable abstain button if any approval selection is made
        abstain_btn = discord.utils.get(self.children, custom_id=lambda x: x and x.startswith(f"abstain_vote_{self.proposal_id}"))
        if abstain_btn:
            abstain_btn.disabled = self.has_selection() # Disable if has selection, enable if not
            if self.has_selection(): abstain_btn.style = discord.ButtonStyle.secondary


        self.submit_button.disabled = not (self.has_selection() or self.is_abstain_vote) # Enable if has selection or abstain chosen
        await interaction.response.edit_message(view=self)
        # approved_list_str = ", ".join(self.approved_values) if self.approved_values else "None"
        # await interaction.followup.send(f"Approved: {approved_list_str}. Click 'Submit Vote'.", ephemeral=True)


async def send_voting_dm(member: discord.Member, proposal_details: Dict[str, Any], options: List[str]):
    """Sends a DM to a member with proposal details and voting options."""
    try:
        if member.bot:
            print(f"DEBUG: Skipping DM to bot user {member.name} ({member.id}).")
            return False

        proposal_id = proposal_details['proposal_id']
        title = proposal_details['title']
        description = proposal_details.get('description', "A new proposal is open for voting.")
        deadline_str = proposal_details.get('deadline', 'Not specified')
        voting_mechanism = proposal_details.get('voting_mechanism', 'Unknown')
        hyperparameters = proposal_details.get('hyperparameters')

        campaign_id = proposal_details.get('campaign_id')
        scenario_order = proposal_details.get('scenario_order')
        user_remaining_campaign_tokens: Optional[int] = None
        campaign_title: Optional[str] = None

        dm_embed_title = f"🗳️ Vote Now: {title} (P#{proposal_id})"
        if campaign_id:
            campaign_data = await db.get_campaign(campaign_id)
            if campaign_data:
                campaign_title = campaign_data.get('title', 'Unnamed Campaign')
                dm_embed_title = f"⚖️ Campaign Vote: '{campaign_title}' - Scenario {scenario_order}: {title} (P#{proposal_id})"

        dm_embed = discord.Embed(title=dm_embed_title, color=discord.Color.blurple())
        dm_embed.description = description

        if campaign_id and campaign_data:
            user_remaining_campaign_tokens = await db.get_user_remaining_tokens(campaign_id, member.id)
            if user_remaining_campaign_tokens is None:
                campaign_total_tokens = campaign_data.get('total_tokens_per_voter')
                if campaign_total_tokens is not None:
                    enrolled = await db.enroll_voter_in_campaign(campaign_id, member.id, campaign_total_tokens)
                    if enrolled:
                        user_remaining_campaign_tokens = await db.get_user_remaining_tokens(campaign_id, member.id)
                        if user_remaining_campaign_tokens is not None:
                            dm_embed.add_field(name="Campaign Tokens", value=f"You have **{user_remaining_campaign_tokens}** tokens for this campaign.", inline=False)
                        else:
                             dm_embed.add_field(name="Campaign Tokens", value="Error fetching your token balance (post-enroll).", inline=False)
                    else:
                        dm_embed.add_field(name="Campaign Tokens", value="Could not enroll you in the campaign to get tokens.", inline=False)
                else:
                    dm_embed.add_field(name="Campaign Tokens", value="Campaign total token info unavailable for enrollment.", inline=False)
            else:
                dm_embed.add_field(name="Campaign Tokens", value=f"You have **{user_remaining_campaign_tokens}** tokens remaining for this campaign.", inline=False)
        elif campaign_id and not campaign_data:
            dm_embed.add_field(name="Campaign Info", value="Error retrieving campaign details for token display.", inline=False)

        dm_embed.add_field(name="Voting Mechanism", value=voting_mechanism.title(), inline=True)
        dm_embed.add_field(name="Deadline", value=utils.format_deadline(deadline_str), inline=True)

        if hyperparameters and isinstance(hyperparameters, dict):
            # ... (hyperparameter display logic as before)
            hyperparams_display = []
            if "allow_abstain" in hyperparameters: # This comes from proposal_hyperparameters
                hyperparams_display.append(f"Abstain Allowed: {'Yes' if hyperparameters.get('allow_abstain', True) else 'No'}")
            if voting_mechanism == "plurality" and "winning_threshold_percentage" in hyperparameters and hyperparameters["winning_threshold_percentage"] is not None:
                hyperparams_display.append(f"Win Threshold: {hyperparameters['winning_threshold_percentage']}%")
            elif voting_mechanism == "dhondt" and "num_seats" in hyperparameters:
                hyperparams_display.append(f"Seats: {hyperparameters['num_seats']}")

            if hyperparams_display:
                dm_embed.add_field(name="Key Rules", value="; ".join(hyperparams_display), inline=False)


        view_class_name = f"{voting_mechanism.title().replace(' ', '')}VoteView"
        current_module = sys.modules[__name__]
        view_class = getattr(current_module, view_class_name, BaseVoteView)

        vote_view = view_class(
            proposal_id=proposal_id,
            options=options,
            proposal_hyperparameters=hyperparameters if hyperparameters else {}, # Pass full dict
            campaign_id=campaign_id,
            scenario_order=scenario_order,
            user_total_campaign_tokens=user_remaining_campaign_tokens,
            original_interaction_user_id=member.id # Pass the member.id to lock the view
        )

        dm_channel = await member.create_dm()
        await dm_channel.send(embed=dm_embed, view=vote_view)
        print(f"SUCCESS: Sent voting DM to {member.name} ({member.id}) for P#{proposal_id} (Campaign: {campaign_id})")
        return True
    except discord.Forbidden:
        print(f"FAILED: Could not send DM to {member.name} ({member.id}) - DMs disabled or bot blocked.")
        return False
    except Exception as e:
        print(f"CRITICAL ERROR in send_voting_dm for P#{proposal_details.get('proposal_id', 'Unknown')} to user {member.id}: {e}")
        traceback.print_exc()
        return False

# ... (rest of file: format_deadline, update_voting_message etc.)

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
