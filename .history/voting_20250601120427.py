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
    # ... (keep as is - interacts with self.view) ...
    """Button for casting an Abstain vote"""

    def __init__(self, proposal_id: int):
        super().__init__(
            label="Vote to Abstain",
            style=discord.ButtonStyle.secondary,
            custom_id=f"abstain_btn_{proposal_id}",
            row=4  # Explicitly place this button on row 4 (at the bottom)
        )
        self.is_selected = False

    async def callback(self, interaction: discord.Interaction):
        # ... (user check and submission check - keep as is) ...
        if interaction.user.id != self.view.user_id:
            await interaction.response.send_message("This is not your vote interface!", ephemeral=True)
            return
        if self.view.is_submitted:
            await interaction.response.send_message("Your vote has already been submitted and cannot be changed.", ephemeral=True)
            return

        self.is_selected = not self.is_selected
        self.style = discord.ButtonStyle.primary if self.is_selected else discord.ButtonStyle.secondary
        self.label = "Abstain Selected ✅" if self.is_selected else "Vote to Abstain"

        # Toggle state of other voting controls
        for child in self.view.children:  # Use self.view
            if isinstance(child, (discord.ui.Button, discord.ui.Select)) and not child.custom_id.startswith("submit_") and child.custom_id != self.custom_id:
                child.disabled = self.is_selected
            elif isinstance(child, discord.ui.Button) and child.custom_id.startswith("submit_"):
                child.disabled = not (
                    self.is_selected or self.view.has_selection())  # Use self.view

        # Use self.view
        await interaction.response.edit_message(view=self.view)
        await interaction.followup.send(f"You have chosen to **{'Abstain' if self.is_selected else 'Not Abstain'}**.", ephemeral=True)


class BaseVoteView(discord.ui.View):
    """Base view for all interactive voting mechanisms"""

    def __init__(self, proposal_id: int, options: List[str],
                 campaign_id: Optional[int] = None,
                 scenario_order: Optional[int] = None,
                 user_total_campaign_tokens: Optional[int] = None,
                 # other params like original_interaction_user_id if needed for interaction checks
                 ):
        super().__init__(timeout=None)
        self.proposal_id = proposal_id
        self.options = options
        self.campaign_id = campaign_id
        self.scenario_order = scenario_order
        self.user_total_campaign_tokens = user_total_campaign_tokens
        self.tokens_invested_this_scenario: Optional[int] = None # Will be set by TokenInvestmentModal
        # ... rest of init ...

    def add_mechanism_items(self):
        """Placeholder for adding mechanism-specific buttons/selects"""
        pass

    def has_selection(self):
        """Checks if the user has made a valid mechanism-specific selection"""
        return False  # Subclasses must override this

    async def submit_callback(self, interaction: discord.Interaction):
        """Handle vote submission"""
        # Only allow the original user to interact
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This is not your vote!", ephemeral=True)
            return

        # If already submitted, don't allow changes
        if self.is_submitted:
            await interaction.response.send_message("Your vote has been submitted and cannot be changed.", ephemeral=True)
            return

        # Check if Abstain is selected
        abstain_button: Optional[AbstainButton] = discord.utils.get(
            self.children, custom_id=f"abstain_btn_{self.proposal_id}")
        # did_abstain is only True if the button exists and is selected
        did_abstain = abstain_button.is_selected if abstain_button and self.allow_abstain else False

        if not did_abstain and not self.has_selection():
            await interaction.response.send_message("Please select an option or choose to abstain before submitting!", ephemeral=True)
            return

        # Additional check: if abstaining is not allowed, did_abstain must be false.
        if not self.allow_abstain and did_abstain:
            await interaction.response.send_message("Abstaining is not allowed for this proposal.", ephemeral=True)
            # Do not proceed with submission if abstain is disallowed but was somehow selected.
            # This is a safeguard; the button shouldn't even be present if not self.allow_abstain.
            return

        # Mark as submitted
        self.is_submitted = True

        # Disable all buttons and select menus
        for child in self.children:
            child.disabled = True

        # Build vote data
        vote_data = {"did_abstain": did_abstain}
        if not did_abstain:
            mechanism_data = self.get_mechanism_vote_data()
            vote_data.update(mechanism_data)

        # Process the vote (this function is defined below)
        success, message = await process_vote(interaction.user.id, self.proposal_id, vote_data)

        # Update the message (disable components)
        try:
            await interaction.response.edit_message(view=self)
        except Exception as e:
            print(
                f"WARNING: Could not edit message to disable view components for proposal {self.proposal_id}, user {self.user_id}: {e}")

        # Send confirmation message
        try:
            await interaction.followup.send(message, ephemeral=True)
        except Exception as e:
            print(
                f"WARNING: Could not send final vote confirmation message to user {self.user_id}: {e}")

    def get_mechanism_vote_data(self) -> Dict[str, Any]:
        """Placeholder to get mechanism-specific vote data"""
        return {}  # Subclasses will override this

    # Add interaction_check to the BaseView or its subclasses
    # Adding it here makes it apply to all interactive vote views (DM votes)
    # Only the original user should be able to interact with their specific DM vote view
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Check if the user interacting is the user the view was sent to."""
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This vote interface is not for you!", ephemeral=True)
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
                    self.selected_option = self.options[option_index]
                else:
                     raise ValueError("Option index out of bounds")
            else:
                raise ValueError("Invalid custom_id format")

        except (IndexError, ValueError) as e:
            print(
                f"Error parsing plurality button custom_id '{button_id}': {e}")
            await interaction.response.send_message("Error processing button click.", ephemeral=True)
            return

        # Update button styles - compare using the *original* selected option
        for child in self.children:
            if isinstance(child, discord.ui.Button) and child.custom_id.startswith(f"plurality_{self.proposal_id}_"):
                try:
                    child_option_index = int(child.custom_id.split("_")[2])
                    original_option_for_button = self.options[child_option_index]

                    if original_option_for_button == self.selected_option:
                        child.style = discord.ButtonStyle.primary
                    else:
                        child.style = discord.ButtonStyle.secondary
                except (IndexError, ValueError):
                    pass  # Ignore invalid buttons

        # Enable submit button if *any* option is selected OR abstain is selected
        submit_button = discord.utils.get(
            self.children, custom_id=f"submit_vote_{self.proposal_id}")
        abstain_button: Optional[AbstainButton] = discord.utils.get(
            self.children, custom_id=f"abstain_btn_{self.proposal_id}")

        # Submit button is enabled if:
        # 1. An option is selected (self.selected_option is not None)
        # OR
        # 2. Abstain is allowed AND the abstain button exists AND it is selected.
        should_enable_submit = self.selected_option is not None or \
                               (self.allow_abstain and abstain_button and abstain_button.is_selected)

        submit_button.disabled = not should_enable_submit

        await interaction.response.edit_message(view=self)
        await interaction.followup.send(f"You selected: **{self.selected_option}**", ephemeral=True)


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
            # Removed user_id and allow_abstain from BaseVoteView constructor call, assuming they are handled differently or not needed there for DM view setup
            # Add campaign context to the view constructor
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
