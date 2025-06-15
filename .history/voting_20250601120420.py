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
    """Process and record a vote"""
    try:
        print(
            f"DEBUG: Processing vote for proposal {proposal_id} from user {user_id}")
        # print(f"DEBUG: Raw vote data received: {vote_data}") # Avoid logging potentially sensitive data unless necessary

        # Check if proposal exists and is in voting stage
        proposal = await db.get_proposal(proposal_id)
        if not proposal:
            print(f"DEBUG: Proposal {proposal_id} not found")
            return False, "Proposal not found or invalid proposal ID."

        if proposal.get('status') != 'Voting':  # Use .get()
            print(
                f"DEBUG: Proposal {proposal_id} is not in voting stage (current status: {proposal.get('status')})")
            return False, f"Voting is not open for this proposal (status: {proposal.get('status', 'Unknown')})."

        # Check if deadline has passed
        # Ensure proposal['deadline'] is a datetime object or parse it
        deadline_data = proposal.get('deadline')  # Use .get() for safety
        deadline_dt = None  # Initialize parsed datetime object

        if isinstance(deadline_data, (str, datetime)):  # Check if data is string or datetime
            try:
                # Attempt to parse the deadline data if it's not already a datetime object
                if isinstance(deadline_data, str):
                    # Use fromisoformat and replace 'Z' if present.
                    # This should handle most common formats.
                    deadline_str_cleaned = deadline_data.replace('Z', '+00:00')
                    deadline_dt = datetime.fromisoformat(deadline_str_cleaned)
                else:
                    deadline_dt = deadline_data  # It's already a datetime object

                if deadline_dt and datetime.now() > deadline_dt:
                    print(
                        f"DEBUG: Deadline has passed for proposal {proposal_id}")
                    # Update proposal status if deadline passed - this is also handled by the periodic task,
                    # but doing it here provides immediate feedback.
                    # It's generally safer to let the periodic task handle closure to avoid race conditions.
                    # So, just return failure message.
                    return False, "Voting has ended for this proposal."
                elif not deadline_dt:  # If parsing failed but data existed
                    print(f"WARNING: Could not parse deadline '{deadline_data}' for expiry check for proposal {proposal_id}. Allowing vote.")
                     # Allow vote to proceed if deadline is unparseable, less disruptive than blocking.
                    pass

            except ValueError:
                print(
                    f"ERROR: Could not parse deadline string for expiry check: '{deadline_data}' for proposal {proposal_id}")
                # Allow vote to proceed if deadline is unparseable
                pass  # Continue processing vote
            except Exception as e:
                print(f"Unexpected error checking deadline expiry: {e}")
                # Log the error but don't block voting based on a deadline parse failure
                pass  # Continue processing vote

        else:
            # No deadline data or unexpected type
            print(f"WARNING: No deadline data or unexpected type for proposal {proposal_id}. Skipping deadline check.")
            pass  # Continue processing vote

        # Validate vote data based on mechanism if not abstaining
        did_abstain = vote_data.get('did_abstain', False)  # Use .get()
        if not did_abstain:
            mechanism_name = proposal.get('voting_mechanism', 'Unknown').lower()  # Use .get()
            # Need options for validation. Fetch from DB.
            proposal_db_id = proposal.get('proposal_id') or proposal.get('id')  # Use .get()
            options = await db.get_proposal_options(proposal_db_id)

            if not options:
                # Fallback if options weren't stored - extract from description (using utils)
                options = utils.extract_options_from_description(
                    proposal.get('description', '')) # Use .get() and utils helper
                if not options:
                    options = ["Yes", "No"]  # Final fallback
                    print(
                        f"WARNING: No options found/extracted for proposal {proposal_id} during vote validation. Using default Yes/No.")

            # Clean and validate submitted options/rankings against the official options list
            if mechanism_name in ["plurality", "dhondt"]:
                chosen_option = vote_data.get('option')  # Use .get()
                # Validate: must be a string and must match one of the official options exactly (after stripping whitespace)
                if not isinstance(chosen_option, str) or chosen_option.strip() not in [opt.strip() for opt in options]:
                    valid_options_str = ", ".join([f"`{o}`" for o in options])
                    print(
                        f"DEBUG: Invalid plurality/dhondt option '{chosen_option}' for proposal {proposal_id}")
                    return False, f"❌ Invalid option '{chosen_option}'. Please choose one of the available options: {valid_options_str}"

                # Store the stripped version for consistency
                vote_data['option'] = chosen_option.strip()

            elif mechanism_name in ["borda", "runoff"]:
                rankings = vote_data.get('rankings', [])  # Use .get()
                if not isinstance(rankings, list):  # Basic type check
                    return False, "❌ Invalid format for rankings. Expected a list."

                # Validate each ranked option, check for duplicates, and if they are in the official options
                cleaned_rankings = [opt.strip() for opt in rankings if isinstance(opt, str) and opt.strip()]  # Clean and filter non-strings/empty

                # Check if all cleaned rankings are in the official options list (case-sensitive match after stripping)
                official_options_stripped = [opt.strip() for opt in options]
                valid_in_options = all(
                    ranked_opt in official_options_stripped for ranked_opt in cleaned_rankings)

                # Check for duplicates *within* the cleaned rankings
                has_duplicates = len(set(cleaned_rankings)
                                     ) != len(cleaned_rankings)

                if not cleaned_rankings or not valid_in_options or has_duplicates:
                    valid_options_str = ", ".join([f"`{o}`" for o in options])
                    print(
                         f"DEBUG: Invalid borda/runoff rankings {rankings} (cleaned: {cleaned_rankings}) for proposal {proposal_id}. Valid in options: {valid_in_options}, Duplicates: {has_duplicates}")
                    return False, f"❌ Invalid or duplicate rankings. Please provide a comma-separated list of *unique* options from the list: {valid_options_str}"

                # Update vote_data with cleaned rankings before storing
                vote_data['rankings'] = cleaned_rankings

            elif mechanism_name == "approval":
                approved = vote_data.get('approved', [])  # Use .get()
                if not isinstance(approved, list):  # Basic type check
                    return False, "❌ Invalid format for approved options. Expected a list."

                # Validate each approved option and filter non-strings/empty
                cleaned_approved = [opt.strip() for opt in approved if isinstance(opt, str) and opt.strip()]  # Clean and filter non-strings/empty

                # Check if all cleaned approved options are in the official options list (case-sensitive match after stripping)
                official_options_stripped = [opt.strip() for opt in options]
                valid_in_options = all(
                    approved_opt in official_options_stripped for approved_opt in cleaned_approved)

                if not cleaned_approved or not valid_in_options:
                    valid_options_str = ", ".join([f"`{o}`" for o in options])
                    print(
                        f"DEBUG: Invalid approval options {approved} (cleaned: {cleaned_approved}) for proposal {proposal_id}. Valid in options: {valid_in_options}")
                    return False, f"❌ Invalid approved options. Please provide a comma-separated list of options from the list: {valid_options_str}"

                # Update vote_data with cleaned approved list before storing
                vote_data['approved'] = cleaned_approved

            # else: unsupported mechanism, assume valid if not abstaining? Or reject? Reject is safer.
            # The UI should prevent unsupported mechanisms, but backend validation is good.
            elif mechanism_name == 'unknown':  # Should not happen based on get_voting_mechanism fallback
                print(f"ERROR: Vote received for unknown mechanism '{mechanism_name}' for proposal {proposal_id}")
                return False, "❌ An internal error occurred (unknown voting mechanism)."

        # If did_abstain is True, no mechanism-specific validation is needed.
        # Ensure vote_data is serializable to JSON before storing
        try:
            json.dumps(vote_data)
        except TypeError as e:
            print(f"CRITICAL ERROR: vote_data is not JSON serializable for user {user_id}, proposal {proposal_id}: {e}")
             # Return an error, don't store bad data
            return False, "❌ An internal error occurred (vote data not serializable)."

        # Record or update vote
        # Store the vote_data dictionary (cleaned if applicable), including `did_abstain`
        # This helper is in db
        existing_vote = await db.get_user_vote(proposal_id, user_id)
        if existing_vote:
            print(
                f"DEBUG: Updating existing vote for user {user_id} on proposal {proposal_id}")
            await db.update_vote(existing_vote.get('vote_id'), vote_data)  # Use .get() for safety
            message = "Your vote has been updated."
        else:
            print(
                f"DEBUG: Recording new vote for user {user_id} on proposal {proposal_id}")
            await db.add_vote(proposal_id, user_id, vote_data)  # This helper is in db
            message = "Your vote has been recorded."

        # Trigger update of the public tracking message
        # This should ideally be done asynchronously or by a separate task
        # To avoid holding up the vote processing.
        # For now, let's call it directly, but be aware this could be slow.
        # Get the guild object
        guild = None

        if proposal and proposal.get('server_id'):  # Ensure proposal and server_id exist
            # Access the bot instance properly
            try:
                import main  # Import here only when needed
                if hasattr(main, 'bot'):  # Check if bot attribute exists
                    guild = main.bot.get_guild(proposal.get('server_id'))

                    # Only try to add to the queue - don't directly call update_vote_tracking
                    if hasattr(main.bot, 'update_queue'):
                        try:
                            await main.bot.update_queue.put({
                                'guild_id': proposal['server_id'],
                                'proposal_id': proposal_id
                            })
                            print(f"DEBUG: Added proposal {proposal_id} to tracking update queue.")
                        except Exception as q_e:
                            print(f"ERROR adding to update queue: {q_e}")
                            # If queue fails, fall back to direct update
                            if guild:
                                await voting_utils.update_vote_tracking(guild, proposal_id)
                    else:
                        print("WARNING: Could not access update queue from process_vote.")
                        # No queue available, so update directly
                        if guild:
                            await voting_utils.update_vote_tracking(guild, proposal_id)
            except Exception as e:
                print(f"ERROR accessing bot instance: {e}")
        else:
            print(f"WARNING: Proposal {proposal_id} missing server_id. Skipping tracking update.")

        # Check if all *invited* voters have voted, and close if so
        # This check should be efficient.
        try:
            # Get all votes *after* the current one was added/updated
            all_votes = await db.get_proposal_votes(proposal_id)  # Fetch again to get latest count
            voted_user_ids = {vote.get('voter_id') for vote in all_votes if vote.get('voter_id') is not None}  # Use a set for efficient lookup, ignore None IDs

            # Get list of user IDs who were explicitly invited
            # This helper is in db
            invited_voters_ids = await db.get_invited_voters_ids(proposal_id) or []  # Get list of user IDs, default to empty list

            if invited_voters_ids:  # Only check 100% completion if invites were tracked
                voted_count = len(voted_user_ids)
                invited_count = len(invited_voters_ids)

                print(
                    f"DEBUG: Proposal {proposal_id} - Voted: {voted_count}, Invited: {invited_count}")

                # Check if *all* invited voters have cast *any* vote (abstain or not)
                # A user has voted if their ID is in voted_user_ids
                all_invited_have_voted = all(
                    user_id in voted_user_ids for user_id in invited_voters_ids)

                # Trigger closure if all invited voters have voted
                if all_invited_have_voted:
                    print(
                        f"DEBUG: All invited voters ({invited_count}) have voted for proposal {proposal_id}. Closing proposal.")

                    # 1) Compute & store results
                    # Use the close_proposal from voting_utils
                    # This also updates status to Passed/Failed/etc.
                    results = await voting_utils.close_proposal(proposal_id)

                    if results:
                        # 2) Mark pending (in case announce fails) - close_proposal already does this
                        # await db.update_proposal(proposal_id, {'results_pending_announcement': 1}) # Redundant

                        # 3) Try to announce right now
                        # Need the bot instance from main
                        if guild:  # Use the guild object fetched earlier
                            print(
                                f"[Immediate] attempting to announce results for proposal {proposal_id}")
                            try:
                                # Need the latest proposal state after close_proposal updated status
                                latest_proposal_state = await db.get_proposal(proposal_id)
                                if latest_proposal_state:
                                    # Use close_and_announce_results from voting_utils
                                    ok = await voting_utils.close_and_announce_results(guild, latest_proposal_state, results)  # Pass the latest proposal dict here
                                else:
                                    print(
                                        f"WARNING: Failed to refetch latest proposal state {proposal_id} after closure.")
                                    ok = False  # Cannot announce without latest state

                            except Exception as e:
                                ok = False
                                print(
                                    f"[Immediate] exception announcing for proposal {proposal_id}: {e}")
                                import traceback
                                traceback.print_exc()

                            if ok:
                                # 4) clear pending flag - close_and_announce_results already does this on success
                                # await db.update_proposal(proposal_id, {'results_pending_announcement': 0}) # Redundant
                                print(
                                    f"[Immediate] results announced and pending flag cleared for {proposal_id}")
                                message += " ✅ Results have been posted."
                            else:
                                print(
                                    f"[Immediate] announcement failed for proposal {proposal_id}; will retry periodically.")
                                message += " Results recorded—will retry announcement shortly."
                                # The pending flag remains 1, so the periodic task will pick it up.
                        else:
                            print(
                                f"[Immediate] Announcement skipped for proposal {proposal_id} - Guild not found.")
                            message += " Results recorded—will retry announcement shortly."

                    else:
                        print(
                            f"WARNING: Close proposal {proposal_id} returned no results upon 100% vote completion.")
                        message += " Voting complete, but could not calculate results."

            else:  # No invited_voters_ids tracked
                print(
                    f"DEBUG: Invite tracking not available for proposal {proposal_id}. Cannot determine if all invited voters have voted.")
                # Update tracking message here if it exists, even if not closing - this is already handled by the update_vote_tracking call above.

        except Exception as e:
            print(
                f"Error checking vote completion for proposal {proposal_id}: {e}")
            import traceback
            traceback.print_exc()  # Print full stack trace for debugging

        # Log vote for tracking purposes
        print(
            f"DEBUG: Vote recorded for proposal {proposal_id} by user {user_id}. Tracking updated.")

        # Return success status and message
        return True, message

    except Exception as e:
        print(
            f"CRITICAL ERROR processing vote for user {user_id} proposal {proposal_id}: {e}")
        import traceback
        traceback.print_exc()
        return False, f"An unexpected error occurred while processing your vote: {str(e)}"


class AbstainButton(discord.ui.Button):
    def __init__(self, proposal_id: int, campaign_id: Optional[int] = None, scenario_order: Optional[int] = None): # Added campaign context
        super().__init__(
            label="Abstain from Voting",
            style=discord.ButtonStyle.secondary,
            custom_id=f"abstain_vote_{proposal_id}" + (f"_{campaign_id}_{scenario_order}" if campaign_id else ""), # Ensure custom_id is unique if needed
            row=3 # Example row
        )
        self.proposal_id = proposal_id
        self.campaign_id = campaign_id
        self.scenario_order = scenario_order

    async def callback(self, interaction: discord.Interaction):
        # User check is handled by BaseVoteView.interaction_check if this button is part of it
        # and that interaction_check is called before this callback.
        # For a standalone button, or if BaseVoteView's interaction_check isn't sufficient:
        # if interaction.user.id != self.view.original_interaction_user_id: # Assuming view has this attribute
        #     await interaction.response.send_message("This is not for you.", ephemeral=True)
        #     return

        if self.view.is_submitted: # Assuming view has 'is_submitted'
            await interaction.response.send_message("You have already submitted your vote for this scenario.", ephemeral=True)
            return

        vote_data = {"did_abstain": True}

        # If it's a campaign, we might still need to show token investment modal for abstention if abstentions cost tokens (unlikely)
        # or if abstaining still needs to be 'confirmed' in the campaign flow.
        # For now, assume abstaining in a campaign doesn't involve token logic directly here,
        # but is just recorded. The submit_callback in BaseVoteView will handle token investment.

        # We'll let the main submit_callback on the BaseVoteView handle the TokenInvestmentModal if it's a campaign.
        # Here, we just set the internal state that the user chose to abstain.
        self.view.is_abstain_vote = True # Add a new attribute to the view
        for item in self.view.children: # Disable other options if any
            if isinstance(item, discord.ui.Select) or (isinstance(item, discord.ui.Button) and item.label != self.label):
                item.disabled = True
        self.disabled = True # Disable abstain button itself.

        # Enable submit button as a choice has been made.
        submit_button = discord.utils.get(self.view.children, label="Submit Vote")
        if submit_button:
            submit_button.disabled = False

        await interaction.response.edit_message(view=self.view)
        # No direct vote processing here. User still needs to hit "Submit Vote".


class BaseVoteView(discord.ui.View):
    """Base view for all interactive voting mechanisms"""

    def __init__(self, proposal_id: int, options: List[str],
                 proposal_hyperparameters: Dict[str, Any], # Added to get allow_abstain
                 campaign_id: Optional[int] = None,
                 scenario_order: Optional[int] = None,
                 user_total_campaign_tokens: Optional[int] = None, # This is the user's *remaining* tokens for the campaign
                 original_interaction_user_id: Optional[int] = None # To lock the view
                ):
        super().__init__(timeout=1800) # 30 mins timeout for example
        self.proposal_id = proposal_id
        self.options = options
        self.proposal_hyperparameters = proposal_hyperparameters if proposal_hyperparameters else {}
        self.campaign_id = campaign_id
        self.scenario_order = scenario_order
        # user_total_campaign_tokens is the REMAINING tokens for the user in this campaign when DM was sent
        self.user_remaining_campaign_tokens_at_dm_send = user_total_campaign_tokens
        self.original_interaction_user_id = original_interaction_user_id

        self.is_submitted = False  # Flag to prevent resubmission
        self.is_abstain_vote = False # Flag to indicate if the user chose to abstain
        self.selected_mechanism_vote_data: Dict[str, Any] = {} # To store mechanism specific choices before final submit

        # Add mechanism-specific controls (implemented by subclasses)
        self.add_mechanism_items() # This should populate buttons/selects for the specific mechanism

        # Add Abstain button only if allowed by proposal hyperparameters
        self.allow_abstain = self.proposal_hyperparameters.get('allow_abstain', True) # Default true if not specified
        if self.allow_abstain:
            abstain_button = AbstainButton(proposal_id, campaign_id, scenario_order)
            self.add_item(abstain_button)

        # Add submit button
        self.submit_button = discord.ui.Button(
            label="Submit Vote",
            style=discord.ButtonStyle.success,
            custom_id=f"submit_vote_{self.proposal_id}" + (f"_{self.campaign_id}_{self.scenario_order}" if self.campaign_id else ""),
            disabled=True, # Start disabled, enabled when a choice is made or abstain is clicked
            row=4
        )
        self.submit_button.callback = self.submit_vote_callback # Changed from self.submit_callback
        self.add_item(self.submit_button)

    def add_mechanism_items(self):
        """Implemented by subclasses to add mechanism-specific UI elements."""
        raise NotImplementedError("Subclasses must implement add_mechanism_items")

    def has_selection(self) -> bool:
        """Implemented by subclasses to check if a valid selection has been made for the mechanism."""
        raise NotImplementedError("Subclasses must implement has_selection")

    def get_mechanism_vote_data(self) -> Dict[str, Any]:
        """
        Implemented by subclasses to retrieve the vote data specific to the mechanism.
        This data is captured when the user interacts with mechanism items (e.g., option_callback).
        It should be stored in self.selected_mechanism_vote_data by those callbacks.
        """
        return self.selected_mechanism_vote_data

    async def submit_vote_callback(self, interaction: discord.Interaction): # Renamed
        if self.is_submitted:
            await interaction.response.send_message("You have already submitted your vote.", ephemeral=True)
            return

        # Defer immediately
        await interaction.response.defer(ephemeral=True, thinking=True)

        if self.campaign_id:
            # Present TokenInvestmentModal
            # Pass user's remaining tokens at the time DM was sent as the max they can invest for THIS scenario
            token_modal = TokenInvestmentModal(
                parent_view=self, # Pass reference to this view
                proposal_id=self.proposal_id,
                campaign_id=self.campaign_id,
                scenario_order=self.scenario_order,
                # Critical: use user_remaining_campaign_tokens_at_dm_send as the max for this modal
                max_tokens_to_invest=self.user_remaining_campaign_tokens_at_dm_send
            )
            await interaction.followup.send_modal(token_modal)
            # The modal's on_submit will call self.process_vote_after_token_investment
        else:
            # Not a campaign, process vote directly
            await self.finalize_vote(interaction, tokens_invested_this_scenario=None)

    async def finalize_vote(self, interaction: discord.Interaction, tokens_invested_this_scenario: Optional[int]):
        """Actually processes and records the vote after all inputs (including tokens if applicable) are gathered."""
        # This method is called either directly for non-campaign votes,
        # or by TokenInvestmentModal's on_submit for campaign votes.
        # Ensure interaction is responded to if coming from modal submit (modal submit itself defers its interaction)

        final_vote_data = {}
        if self.is_abstain_vote:
            final_vote_data["did_abstain"] = True
        else:
            final_vote_data = self.get_mechanism_vote_data()
            final_vote_data["did_abstain"] = False # Explicitly set if not abstaining

        # Add campaign-specific data to the vote record
        if self.campaign_id and tokens_invested_this_scenario is not None:
            # Validate tokens_invested_this_scenario again against current DB state if paranoid, or trust modal.
            # For now, trust modal.
            final_vote_data["tokens_invested"] = tokens_invested_this_scenario
        else:
            final_vote_data["tokens_invested"] = None # Ensure it's None for non-campaign or if not applicable

        # Attempt to record vote in DB (this now happens via process_vote's call to db.record_vote)
        # The process_vote function handles DB interaction and validation against proposal status/deadline.
        # It also updates vote tracking.
        # We need to ensure process_vote in voting.py is adapted to take these new campaign fields if needed,
        # or that db.record_vote correctly handles them.
        # For now, assume db.record_vote is already updated to handle 'tokens_invested' and 'is_abstain'.

        success, message = await process_vote( # process_vote is an existing global function in voting.py
            user_id=interaction.user.id,
            proposal_id=self.proposal_id,
            vote_data=final_vote_data # This now includes tokens_invested and did_abstain
        )

        if success:
            self.is_submitted = True
            for item in self.children:
                item.disabled = True

            feedback_message = f"✅ Your vote for Proposal #{self.proposal_id} has been recorded: {message}"
            if self.campaign_id and tokens_invested_this_scenario is not None:
                feedback_message = f"✅ Your vote for Scenario {self.scenario_order} (P#{self.proposal_id}) with **{tokens_invested_this_scenario} token(s)** invested has been recorded. {message}"

            # Edit the original DM message
            if interaction.message:
                try:
                    # We need to construct a new embed or modify the existing one to show vote confirmation.
                    # For simplicity, just edit content.
                    await interaction.message.edit(content=feedback_message, view=self) # view=self to show disabled buttons
                except discord.HTTPException as e:
                    print(f"Error editing DM message after vote: {e}")
                    # If editing DM fails, send a followup on the interaction that triggered finalize_vote
                    if interaction.response.is_done(): # If submit_vote_callback deferred
                        await interaction.followup.send(feedback_message, ephemeral=True)
                    else: # If modal on_submit deferred (it should have)
                        await interaction.followup.send(feedback_message, ephemeral=True)


            # Update user's campaign tokens in the database if it was a campaign vote
            if self.campaign_id and tokens_invested_this_scenario is not None and tokens_invested_this_scenario > 0:
                token_update_success = await db.update_user_remaining_tokens(
                    campaign_id=self.campaign_id,
                    user_id=interaction.user.id,
                    tokens_spent=tokens_invested_this_scenario
                )
                if not token_update_success:
                    # Log this error, potentially inform user. This is a desync.
                    print(f"CRITICAL: Failed to update user tokens for C:{self.campaign_id} U:{interaction.user.id} after vote.")
                    # Could send another followup to user about token update issue.
                    if interaction.response.is_done():
                         await interaction.followup.send("⚠️ Your vote was recorded, but there was an issue updating your campaign token balance. Please contact an admin.", ephemeral=True)


        else: # Vote processing failed
            # Message already contains reason for failure from process_vote
            # Enable submit button again if not campaign (for campaign, modal flow would restart or show error)
            if not self.campaign_id:
                self.submit_button.disabled = False # Re-enable submit button if vote failed for non-campaign

            if interaction.message: # Try to edit DM
                try:
                    await interaction.message.edit(content=f"❌ Vote submission failed: {message}", view=self)
                except discord.HTTPException: # Fallback to followup
                     if interaction.response.is_done():
                        await interaction.followup.send(f"❌ Vote submission failed: {message}", ephemeral=True)
            else: # No DM message (shouldn't happen for DM views), send followup
                 if interaction.response.is_done():
                    await interaction.followup.send(f"❌ Vote submission failed: {message}", ephemeral=True)

        # If the initial interaction for finalize_vote was from a modal submit (which defers),
        # and we haven't sent a followup for success/failure from the modal context, do so.
        # However, the logic above tries to edit the DM message first.
        # If interaction.response.is_done() is true here, it means the deferral from submit_vote_callback
        # or modal's on_submit wasn't followed by a .send() or .edit_message() from THAT interaction's perspective.
        # The DM message edit is preferred. If that fails, a followup to the *current* interaction is sent.
        # This should be sufficient.


    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # Lock the view to the original user if original_interaction_user_id is set
        if self.original_interaction_user_id and interaction.user.id != self.original_interaction_user_id:
            await interaction.response.send_message("This voting interface is not for you.", ephemeral=True)
            return False

        if self.is_submitted:
            await interaction.response.send_message("You have already submitted your vote for this proposal/scenario.", ephemeral=True)
            return False
        return True

# (TokenInvestmentModal definition will go here or be imported if defined elsewhere)
# ... (PluralityVoteView, RankedVoteView, ApprovalVoteView subclasses) ...
# Their __init__ calls super() and should not need direct changes for campaign_id etc.
# Their option_callback methods will need to:
# 1. Set self.selected_mechanism_vote_data
# 2. Enable self.submit_button
# 3. Set self.is_abstain_vote = False
# 4. Disable other options (like other radio buttons in plurality)

# Example of how PluralityVoteView option_callback might change:
# class PluralityVoteView(BaseVoteView):
#     # ... (add_mechanism_items, has_selection, get_mechanism_vote_data) ...
#     async def option_callback(self, interaction: discord.Interaction):
#         # Interaction check handled by BaseVoteView
#         selected_option = interaction.data['values'][0]
#         self.selected_mechanism_vote_data = {'option': selected_option}
#         self.is_abstain_vote = False
#
#         # Disable other option buttons and abstain, enable submit
#         for item in self.view.children:
#             if isinstance(item, discord.ui.Button):
#                 if item.custom_id == interaction.custom_id: # This is the clicked button
#                     item.style = discord.ButtonStyle.primary
#                     item.disabled = True # Or keep enabled if re-selection is allowed before submit
#                 elif item.label == "Submit Vote":
#                     item.disabled = False
#                 else: # Other option buttons or abstain button
#                     item.style = discord.ButtonStyle.secondary
#                     item.disabled = True # Disable other options
#
#         await interaction.response.edit_message(view=self.view)


# ========================
# 🔹 CORE VOTING LOGIC & DM HANDLING
# ========================

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
        hyperparameters = proposal_details.get('hyperparameters') # This should be a dict if present

        # Campaign-specific information initialization
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
        dm_embed.description = description # Set description after potential title change

        if campaign_id and campaign_data: # Fetch and display tokens only if campaign context is confirmed
            user_remaining_campaign_tokens = await db.get_user_remaining_tokens(campaign_id, member.id)
            if user_remaining_campaign_tokens is None:
                campaign_total_tokens = campaign_data.get('total_tokens_per_voter')
                if campaign_total_tokens is not None:
                    enrolled = await db.enroll_voter_in_campaign(campaign_id, member.id, campaign_total_tokens)
                    if enrolled:
                        user_remaining_campaign_tokens = await db.get_user_remaining_tokens(campaign_id, member.id)
                        # Check again after enrollment attempt
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
        elif campaign_id and not campaign_data: # Campaign ID was present but data fetch failed
            dm_embed.add_field(name="Campaign Info", value="Error retrieving campaign details for token display.", inline=False)

        dm_embed.add_field(name="Voting Mechanism", value=voting_mechanism.title(), inline=True)
        dm_embed.add_field(name="Deadline", value=utils.format_deadline(deadline_str), inline=True)

        if hyperparameters and isinstance(hyperparameters, dict):
            hyperparams_display = []
            if "allow_abstain" in hyperparameters:
                hyperparams_display.append(f"Abstain Allowed: {'Yes' if hyperparameters['allow_abstain'] else 'No'}")
            # Mechanism-specific hyperparameter display for DMs
            if voting_mechanism == "plurality" and "winning_threshold_percentage" in hyperparameters and hyperparameters["winning_threshold_percentage"] is not None:
                hyperparams_display.append(f"Win Threshold: {hyperparameters['winning_threshold_percentage']}%")
            elif voting_mechanism == "dhondt" and "num_seats" in hyperparameters:
                hyperparams_display.append(f"Seats: {hyperparameters['num_seats']}")

            if hyperparams_display:
                dm_embed.add_field(name="Key Rules", value="; ".join(hyperparams_display), inline=False)

        # Dynamically get the view class based on mechanism name
        view_class_name = f"{voting_mechanism.title().replace(' ', '')}VoteView" # e.g. PluralityVoteView
        # Ensure the current module (voting.py) is correctly referenced for getattr
        current_module = sys.modules[__name__]
        view_class = getattr(current_module, view_class_name, BaseVoteView) # Fallback to BaseVoteView if specific not found

        vote_view = view_class(
            proposal_id=proposal_id,
            options=options,
            proposal_hyperparameters=hyperparameters,
            campaign_id=campaign_id,
            scenario_order=scenario_order,
            user_total_campaign_tokens=user_remaining_campaign_tokens # Pass current remaining tokens for modal validation
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
