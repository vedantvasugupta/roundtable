import utils
import discord
import asyncio
from datetime import datetime
import db  # Assuming db can be imported here
import json
from typing import List, Dict, Any, Optional, Union, Tuple

# Import functions/classes from voting and proposals only if strictly necessary and non-circular
# Avoid importing the whole module if only specific items are needed.
# For example, formatting functions might need proposal data structures.
# extract_options_from_description is needed here for fallback, so import it.
# create_progress_bar is needed for formatting results, so import it from utils.
# Used as fallback for options
from db import get_proposal_options
# Used for formatting output
from utils import create_progress_bar, format_time_remaining, extract_options_from_description

# ========================
# 🔹 VOTING MECHANISMS (COUNTING LOGIC)
# ========================
# Keep these here, they implement the counting logic for *non-abstain* votes.


class PluralityVoting:
    @staticmethod
    def count_votes(votes: List[Dict], options: List[str], hyperparameters: Optional[Dict[str, Any]] = None):
        """Counts votes for Plurality voting, considering all options and hyperparameters.

        Args:
            votes: A list of vote records (dictionaries), excluding abstain votes.
            options: A list of all valid option strings for this proposal.
            hyperparameters: An optional dictionary of hyperparameters, e.g., {'winning_threshold_percentage': 40}.
        """
        if hyperparameters is None:
            hyperparameters = {}

        results = {option: 0 for option in options}
        total_effective_votes = len(votes)

        for vote in votes:
            vote_data = vote.get('vote_data')
            if not isinstance(vote_data, dict):
                print(f"WARNING: Expected dict for vote_data in Plurality. Got {type(vote_data)}. Vote: {vote}")
                continue

            chosen_option = vote_data.get('option')
            if chosen_option is None:
                print(f"WARNING: 'option' key missing or None in vote_data for Plurality. Vote: {vote}")
                continue
            if not isinstance(chosen_option, str):
                print(f"WARNING: Expected string for 'option'. Got {type(chosen_option)}. Vote: {vote}")
                continue

            if chosen_option in results: # Only count votes for valid options
                results[chosen_option] += 1
            else:
                print(f"WARNING: Vote for unknown option '{chosen_option}' in Plurality. Valid: {options}. Vote: {vote}")

        sorted_results = sorted(results.items(), key=lambda x: x[1], reverse=True)

        winner = None
        reason_for_no_winner = None # Initialize reason

        if total_effective_votes > 0 and sorted_results:
            top_option_name, top_option_votes = sorted_results[0]

            # Use 'winning_threshold_percentage' from hyperparameters
            winning_threshold_config = hyperparameters.get('winning_threshold_percentage')

            if winning_threshold_config is not None:
                try:
                    # Assuming winning_threshold_config is already an int/float (e.g., 40 for 40%)
                    threshold_percentage_value = float(winning_threshold_config)
                    if not (0 <= threshold_percentage_value <= 100):
                        print(f"WARNING: Invalid winning_threshold_percentage value '{threshold_percentage_value}'. Must be 0-100. Using simple majority.")
                        reason_for_no_winner = "Invalid threshold configuration."
                        # Fallback to simple majority
                        if top_option_votes > total_effective_votes / 2:
                            winner = top_option_name
                        elif top_option_votes == total_effective_votes / 2 and len(sorted_results) > 1 and sorted_results[1][1] == top_option_votes:
                             reason_for_no_winner = "Tie (simple majority)"
                        else:
                            reason_for_no_winner = "No majority (simple majority fallback)"

                    else:
                        required_votes = (threshold_percentage_value / 100.0) * total_effective_votes
                        if top_option_votes > required_votes:
                            winner = top_option_name
                        else:
                            reason_for_no_winner = f"Threshold of {threshold_percentage_value}% not met. Top option had { (top_option_votes / total_effective_votes * 100) if total_effective_votes > 0 else 0 :.2f}%."
                            # Check for tie even if threshold not met by top
                            if len(sorted_results) > 1 and sorted_results[1][1] == top_option_votes:
                                reason_for_no_winner += " (Tie for highest votes)"

                except ValueError:
                    print(f"WARNING: Invalid format for winning_threshold_percentage '{winning_threshold_config}'. Using simple majority.")
                    reason_for_no_winner = "Invalid threshold format."
                    # Fallback to simple majority
                    if top_option_votes > total_effective_votes / 2:
                        winner = top_option_name
                    elif top_option_votes == total_effective_votes / 2 and len(sorted_results) > 1 and sorted_results[1][1] == top_option_votes:
                        reason_for_no_winner = "Tie (simple majority)"
                    else:
                        reason_for_no_winner = "No majority (simple majority fallback)"
            else:
                # Default: simple majority (more than 50% of votes)
                if top_option_votes > total_effective_votes / 2:
                    winner = top_option_name
                # Handle cases like a perfect tie in a 2-option scenario (e.g., 5 votes for A, 5 for B out of 10)
                elif top_option_votes == total_effective_votes / 2 and len(sorted_results) > 1 and sorted_results[1][1] == top_option_votes:
                    reason_for_no_winner = "Tie (simple majority)"
                else:
                    reason_for_no_winner = "No majority (simple majority)"
        elif not sorted_results:
            reason_for_no_winner = "No votes cast."
        else: # total_effective_votes is 0
            reason_for_no_winner = "No effective votes cast."


        return {
            'mechanism': 'plurality',
            'results': sorted_results,
            'winner': winner,
            'total_effective_votes': total_effective_votes,
            'reason_for_no_winner': reason_for_no_winner if winner is None else None
        }

    @staticmethod
    def get_description():
        return "Each voter selects one option. The option with the most votes wins."

    @staticmethod
    def get_vote_instructions():
        # Instructions are now generated in voting.py's get_voting_instructions
        return "Instructions defined in voting.py"


class BordaCount:
    """Borda count voting system - gives points based on rankings"""


    @staticmethod
    def count_votes(votes: List[Dict]):
        all_options_set = set()
        valid_votes = []
        for vote in votes:
            vote_data = vote.get('vote_data')
            if not isinstance(vote_data, dict):
                print(
                    f"WARNING: Expected dict for vote_data but got {type(vote_data)} in BordaCount.count_votes. Skipping vote: {vote}")
                continue

            rankings = vote_data.get('rankings', [])
            if not isinstance(rankings, list):
                print(
                    f"WARNING: Expected list for 'rankings' but got {type(rankings)} in BordaCount.count_votes. Skipping vote: {vote}")
                continue

            # Ensure all items in rankings are strings
            valid_rankings = [opt for opt in rankings if isinstance(opt, str)]
            if len(valid_rankings) != len(rankings):
                print(
                    f"WARNING: Non-string options found in rankings for BordaCount. Filtering invalid options in vote: {vote}")

            if valid_rankings:  # Only process votes with valid rankings
                all_options_set.update(valid_rankings)
                # Store processed rankings in the valid_votes list if needed later, or just the vote_data
                # Let's store the original vote dict but ensure valid_rankings is used below
                # Update rankings in the dict for consistency
                vote['vote_data']['rankings'] = valid_rankings
                valid_votes.append(vote)

        all_options = list(all_options_set)
        if not valid_votes or not all_options:
            return {'mechanism': 'borda', 'results': [], 'winner': None, }

        points = {option: 0 for option in all_options}

        for vote in valid_votes:  # Iterate over valid votes
            # Access the cleaned rankings list
            # Use the potentially updated list
            rankings = vote['vote_data']['rankings']
            ranked_options_count = len(rankings)
            for i, option in enumerate(rankings):
                points[option] += (ranked_options_count - 1) - i

        # Convert to sorted list of tuples
        sorted_results = sorted(
            points.items(), key=lambda x: x[1], reverse=True)

        # Determine winner (highest points)
        # Winner only if points > 0
        winner = sorted_results[0][0] if sorted_results and sorted_results[0][1] > 0 else None

        return {
            'mechanism': 'borda',
            'results': sorted_results,  # List of (option, points) tuples
            'winner': winner,
        }

    @staticmethod
    def get_description():
        return "Voters rank options. Points are assigned based on rank, and the option with the most points wins."

    @staticmethod
    def get_vote_instructions():
        # Instructions are now generated in voting.py's get_voting_instructions
        return "Instructions defined in voting.py"


class ApprovalVoting:
    """Approval voting system"""

    @staticmethod
    def count_votes(votes: List[Dict], options: List[str], hyperparameters: Optional[Dict[str, Any]] = None):
        """Count approval votes (only non-abstain votes are passed here)

        Args:
            votes: A list of vote records (dictionaries), excluding abstain votes.
            options: A list of all valid option strings for this proposal.
            hyperparameters: An optional dictionary of hyperparameters (currently unused for approval counting).
        """
        results = {option: 0 for option in options} # Initialize with all official options

        for vote in votes:
            vote_data = vote.get('vote_data')
            if not isinstance(vote_data, dict):
                print(
                    f"WARNING: Expected dict for vote_data but got {type(vote_data)} in ApprovalVoting.count_votes. Skipping vote: {vote}")
                continue

            approved_list_from_vote = vote_data.get('approved', [])
            if not isinstance(approved_list_from_vote, list):
                print(
                    f"WARNING: Expected list for 'approved' in vote_data but got {type(approved_list_from_vote)}. Skipping vote: {vote}")
                continue

            for approved_option in approved_list_from_vote:
                if not isinstance(approved_option, str):
                    print(
                        f"WARNING: Expected string for option in approved list but got {type(approved_option)}. Skipping option: {approved_option}")
                    continue

                if approved_option in results: # Only count valid, predefined options
                    results[approved_option] += 1
                else:
                    print(f"WARNING: Vote for unknown option '{approved_option}' in Approval. Valid: {options}. Vote: {vote}")

        # Convert to sorted list of tuples
        sorted_results = sorted(
            results.items(), key=lambda x: x[1], reverse=True)

        # Determine winner (highest approvals)
        winner = sorted_results[0][0] if sorted_results and sorted_results[0][1] > 0 else None

        return {
            'mechanism': 'approval',
            'results': sorted_results,  # List of (option, count) tuples
            'winner': winner,
            'total_effective_votes': sum(r[1] for r in sorted_results) # Add this for format_vote_results
        }

    @staticmethod
    def get_description():
        return "Voters can approve of any number of options. The option with the most approvals wins."

    @staticmethod
    def get_vote_instructions():
        # Instructions are now generated in voting.py's get_voting_instructions
        return "Instructions defined in voting.py"


class RunoffVoting:
    """Instant runoff voting system"""

    @staticmethod
    def count_votes(votes: List[Dict]):
        all_options_set = set()
        valid_votes = []
        for vote in votes:
            vote_data = vote.get('vote_data')
            if not isinstance(vote_data, dict):
                print(
                    f"WARNING: Expected dict for vote_data but got {type(vote_data)} in RunoffVoting.count_votes. Skipping vote: {vote}")
                continue

            rankings = vote_data.get('rankings', [])
            if not isinstance(rankings, list):
                print(
                    f"WARNING: Expected list for 'rankings' but got {type(rankings)} in RunoffVoting.count_votes. Skipping vote: {vote}")
                continue

             # Ensure all items in rankings are strings
            valid_rankings = [opt for opt in rankings if isinstance(opt, str)]
            if len(valid_rankings) != len(rankings):
                print(
                    f"WARNING: Non-string options found in rankings for RunoffVoting. Filtering invalid options in vote: {vote}")

            if valid_rankings:  # Only process votes with valid rankings
                all_options_set.update(valid_rankings)
                # Store processed rankings in the valid_votes list if needed later, or just the vote_data
                # Let's store the original vote dict but ensure valid_rankings is used below
                # Update rankings in the dict for consistency
                vote['vote_data']['rankings'] = valid_rankings
                valid_votes.append(vote)

        all_options = list(all_options_set)
        if not valid_votes or not all_options:
            return {'mechanism': 'runoff', 'results': [], 'winner': None, 'rounds': 0, }


        # Initialize counts
        current_options = set(all_options)
        round_results = []
        winner = None

        # Process rounds until a winner is found or no options left
        while winner is None and len(current_options) > 1:
            # Count first choices for each vote for options still in play
            counts = {option: 0 for option in current_options}
            for vote in valid_votes:
                rankings = vote.get('vote_data', {}).get('rankings', [])
                # Find first choice in this vote that is still in current_options
                for option in rankings:
                    if option in current_options:
                        counts[option] += 1
                        break  # Count only the highest-ranked non-eliminated option

            # Convert to list and sort by vote count
            # Handle cases where options might have 0 votes this round
            round_counts_list = sorted(
                counts.items(), key=lambda x: x[1], reverse=True)

            # Record this round's results
            round_results.append({
                'round': len(round_results) + 1,
                # List of (option, count) tuples for this round
                'counts': round_counts_list
            })

            # Check for majority (more than 50% of votes counted in *this* round)
            # Sum of first-preference votes this round
            total_votes_in_round = sum(counts.values())
            threshold = total_votes_in_round / 2

            if round_counts_list and round_counts_list[0][1] > threshold:
                # Majority found
                winner = round_counts_list[0][0]
                break  # Exit while loop

            # No majority, check for ties for elimination
            if len(current_options) > 1:
                # Find the lowest count
                min_votes = min(counts.values())
                # Find all options with the lowest count
                to_eliminate = [option for option,
                    count in counts.items() if count == min_votes]

                # If multiple options tie for the lowest count, eliminate all of them
                # UNLESS they are the *only* options remaining. If there's a tie
                # among the last 2 options, there's no clear winner.
                if len(current_options) - len(to_eliminate) < 1:
                    # Eliminating these would leave 1 or 0 options, and no majority was found.
                    # This indicates no clear winner under IRV rules.
                    break  # Exit loop, winner remains None

                # Eliminate options
                current_options -= set(to_eliminate)

        # If winner is still None and only one option remains (or started with one), that's the winner
        if winner is None and len(current_options) == 1:
            winner = list(current_options)[0]

        return {
            'mechanism': 'runoff',
            'results': round_results,  # List of round data dictionaries
            'winner': winner,
            'rounds': len(round_results),
        }

    @staticmethod
    def get_description():
        return "Voters rank options. If no majority, the option with the fewest votes is eliminated, and votes are redistributed until a majority winner is found."

    @staticmethod
    def get_vote_instructions():
        # Instructions are now generated in voting.py's get_voting_instructions
        return "Instructions defined in voting.py"


class DHondtMethod:
    """D'Hondt method for proportional representation"""

    @staticmethod
    def count_votes(votes: List[Dict]):
        counts = {}
        for vote in votes:
            vote_data = vote.get('vote_data')
            if not isinstance(vote_data, dict):
                print(
                    f"WARNING: Expected dict for vote_data but got {type(vote_data)} in DHondtMethod.count_votes. Skipping vote: {vote}")
                continue

            option = vote_data.get('option')
            if option is None:  # Check explicitly for None
                print(
                    f"WARNING: 'option' key missing or None in vote_data for DHondtMethod. Skipping vote: {vote}")
                continue
            if not isinstance(option, str):  # Ensure option is a string
                print(
                    f"WARNING: Expected string for 'option' but got {type(option)} in DHondtMethod.count_votes. Skipping vote: {vote}")
                continue

            if option:
                counts[option] = counts.get(option, 0) + 1

        # Convert raw counts to sorted list
        raw_counts_list = sorted(
            counts.items(), key=lambda x: x[1], reverse=True)

        # Calculate quotients for D'Hondt
        # We need to decide how many 'seats' or winners there are.
        # For a simple proposal, this is usually 1 (the winner).
        # D'Hondt is typically for allocating multiple seats proportionally.
        # If used for a single winner, it reduces to Plurality.
        # Let's assume for governance proposals we allocate only 1 'seat' - the proposal winner.
        # In this case, the winner is simply the option with the highest vote count.
        # However, the description implies proportional representation.
        # Let's assume the *intent* is to show relative support for options using the D'Hondt calculation principle,
        # even if only one winner is formally declared (the one with the highest first quotient).
        # Or, perhaps the proposal itself might specify the number of 'seats' (outcomes) to be allocated?
        # Let's stick to the most common interpretation for a single winner proposal: it's effectively Plurality.
        # But we can still calculate and show the quotients as part of the "details".

        quotients_data = {}
        all_quotients = []
        # Calculate quotients for, say, up to 5 "seats" to show distribution principle
        max_seats_to_show = 5

        for option, votes_count in counts.items():
            quotients = []
            # Calculate quotients for potential seats 1, 2, 3...
            for i in range(max_seats_to_show):
                divisor = i + 1
                quotient = votes_count / divisor
                quotients.append((f"Seat {i+1}", quotient))
                all_quotients.append((option, quotient, i + 1))
            quotients_data[option] = quotients

        # Sort all quotients to find the highest ones
        all_quotients.sort(key=lambda x: x[1], reverse=True)

        # The "winner" (single winner) is the option with the highest first quotient, which is the same as Plurality winner.
        # Let's just report the top option from the raw counts as the winner for simplicity in a single-winner context.
        winner = raw_counts_list[0][0] if raw_counts_list else None

        # We can include the top quotients as 'details' or a separate item in the results.
        # Let's include the top few overall quotients to show the PR nature.
        top_quotients_display = [(opt, q, pos)
                                  for opt, q, pos in all_quotients[:max_seats_to_show]]

        return {
            'mechanism': 'dhondt',
            'results': raw_counts_list,  # List of (option, count) tuples
            'winner': winner,  # Single winner (highest count)
            'quotients_data': quotients_data,  # Optional: all quotients per option
            # Optional: list of top overall quotients
            'top_quotients': top_quotients_display
        }

    @staticmethod
    def get_description():
        return "Votes determine proportional support. Typically used for multi-winner elections. For a single winner, it behaves like Plurality, but quotients are calculated."

    @staticmethod
    def get_vote_instructions():
        # Instructions are now generated in voting.py's get_voting_instructions
        return "Instructions defined in voting.py"


def get_voting_mechanism(mechanism_name: str):
    """Returns the appropriate voting mechanism class based on name"""
    mechanisms = {
        "plurality": PluralityVoting,
        "borda": BordaCount,
        "approval": ApprovalVoting,
        "runoff": RunoffVoting,
        "dhondt": DHondtMethod
    }
    return mechanisms.get(mechanism_name.lower())


async def calculate_results(proposal_id: int) -> Optional[Dict]:
    """Calculate the results of a proposal based on its votes and voting mechanism.

    Fetches proposal details, votes, and hyperparameters from the database.
    Separates abstain votes and passes relevant data to the mechanism-specific counter.

    Args:
        proposal_id: The ID of the proposal.

    Returns:
        A dictionary containing the election results, including mechanism, raw results,
        winner, abstain count, and any mechanism-specific details, or None if an error occurs.
    """
    try:
        proposal = await db.get_proposal(proposal_id)
        if not proposal:
            print(f"ERROR: Proposal #{proposal_id} not found in calculate_results.")
            return None

        raw_votes_from_db = await db.get_proposal_votes(proposal_id)
        if raw_votes_from_db is None: # Check for None explicitly, as empty list is valid
            print(f"ERROR: Failed to fetch votes for proposal #{proposal_id}.")
            return None

        mechanism_name = proposal.get('voting_mechanism')
        hyperparameters = proposal.get('hyperparameters') # This will be a dict or None
        if not hyperparameters: # Ensure hyperparameters is a dict for consistent access
            hyperparameters = {}

        # Separate abstain votes
        actual_votes = []
        abstain_count = 0
        for vote_record in raw_votes_from_db:
            vote_data_str = vote_record.get('vote_data')
            try:
                vote_data_dict = json.loads(vote_data_str) if isinstance(vote_data_str, str) else vote_data_str
            except json.JSONDecodeError:
                print(f"WARNING: Could not decode vote_data JSON: {vote_data_str} for vote ID {vote_record.get('id')}")
                continue # Skip this vote

            if not isinstance(vote_data_dict, dict): # Ensure it's a dict after potential JSON load
                print(f"WARNING: vote_data is not a dict for vote ID {vote_record.get('id')}: {vote_data_dict}")
                continue

            if vote_data_dict.get('did_abstain') is True:
                abstain_count += 1
            else:
                # Add the full vote record, not just vote_data, if mechanisms expect it
                # Or, just pass the essential vote_data_dict if that's all they need
                # For now, let's assume mechanisms can parse from the full record if needed, or we adapt them.
                # Let's re-insert the parsed vote_data_dict back into the vote_record for consistency
                # (as existing counters might expect vote_data to be a dict already).
                vote_record['vote_data'] = vote_data_dict
                actual_votes.append(vote_record)

        # Get proposal options (needed by counters to know all possible choices)
        # This is crucial for reporting 0 votes for some options.
        options = await db.get_proposal_options(proposal_id)
        if not options:
            # Fallback to extracting from description if no dedicated options in DB (old proposals?)
            options = utils.extract_options_from_description(proposal.get('description', ''))
            if not options and mechanism_name in ['plurality', 'approval', 'borda', 'runoff', 'dhondt']:
                 # Default options if still none, but only for mechanisms that typically have explicit options.
                options = ["Yes", "No"]

        print(f"DEBUG: Proposal #{proposal_id}, Mechanism: {mechanism_name}, Hyperparams: {hyperparameters}, Options: {options}, ActualVotes: {len(actual_votes)}, Abstain: {abstain_count}")

        mechanism_counter = get_voting_mechanism(mechanism_name)
        if not mechanism_counter:
            print(f"ERROR: Unknown voting mechanism: {mechanism_name} for proposal #{proposal_id}")
            return None

        # Call the mechanism's count_votes method
        # Pass actual_votes, options, and hyperparameters
        # Ensure all counters are updated to accept these arguments
        results_data = mechanism_counter.count_votes(actual_votes, options, hyperparameters)

        if results_data:
            results_data['abstain_count'] = abstain_count
            results_data['total_raw_votes'] = len(raw_votes_from_db) # Total including abstains
            results_data['total_effective_votes'] = len(actual_votes) # Total excluding abstains
            return results_data
        else:
            print(f"ERROR: Counting votes failed for mechanism {mechanism_name} on proposal #{proposal_id}")
            # Return a basic structure if counting fails but we have abstain info
            return {
                'mechanism': mechanism_name,
                'results': [],
                'winner': None,
                'abstain_count': abstain_count,
                'total_raw_votes': len(raw_votes_from_db),
                'total_effective_votes': len(actual_votes),
                'error': 'Vote counting failed'
            }

    except Exception as e:
        print(f"CRITICAL ERROR in calculate_results for proposal #{proposal_id}: {e}")
        import traceback
        traceback.print_exc()
        return None


async def format_vote_results(results: Dict, proposal: Dict) -> discord.Embed:
    """Formats the election results into a Discord embed, including hyperparameter info."""
    mechanism = results.get('mechanism', proposal.get('voting_mechanism', 'Unknown')).title()
    winner = results.get('winner')
    result_details = results.get('results', []) # List of (option, score) tuples
    abstain_count = results.get('abstain_count', 0)
    total_effective_votes = results.get('total_effective_votes', sum(item[1] for item in result_details) if result_details else 0)
    # total_raw_votes includes abstains, total_effective_votes does not.
    total_raw_votes = results.get('total_raw_votes', total_effective_votes + abstain_count)

    # Get proposal details for the embed title and link
    proposal_id = proposal.get('proposal_id') or proposal.get('id')
    proposal_title = proposal.get('title', f"Proposal #{proposal_id}")
    # Construct proposal URL (assuming proposals are viewable via a command or web interface)
    # For now, just use proposal ID. Ideally, you'd have a URL.
    # proposal_url = f"https://yourserver.com/proposals/{proposal_id}" # Example

    # Embed color and status message
    if winner:
        embed_color = discord.Color.green()
        status_message = f"🎉 **Winner: {winner}**"
    elif results.get('error'):
        embed_color = discord.Color.red()
        status_message = f"❌ **Error calculating results:** {results.get('error')}"
    elif not result_details and abstain_count == 0:
        embed_color = discord.Color.orange()
        status_message = "⚠️ No votes were cast."
    else:
        embed_color = discord.Color.orange()
        status_message = " inconclusive (no winner determined based on criteria)."
        if total_effective_votes > 0 and not winner:
             status_message = "⚠️ Result was inconclusive (e.g., no option met the winning threshold or a perfect tie)."
        elif total_effective_votes == 0 and abstain_count > 0:
            status_message = f"ℹ️ Only abstain votes were cast ({abstain_count})."

    embed = discord.Embed(
        title=f"🗳️ Vote Results: {proposal_title}",
        # description=f"Proposal ID: {proposal_id}", # Can add URL here if available
        color=embed_color
    )

    embed.add_field(name="Voting Mechanism", value=mechanism, inline=True)
    embed.add_field(name="Status", value=status_message, inline=False)

    # Display individual option results
    if result_details:
        options_text = ""
        for option, score in result_details:
            percentage = (score / total_effective_votes * 100) if total_effective_votes > 0 else 0
            options_text += f"• **{option}**: {score} vote(s) ({percentage:.1f}%)\n"
            # Add progress bar for visual representation
            # options_text += f"{create_progress_bar(percentage)}\n"
        if not options_text:
            options_text = "No effective votes were cast for any option."
        embed.add_field(name="📊 Option Scores", value=options_text, inline=False)
    elif not results.get('error'): # Only show this if not an error and no details
        embed.add_field(name="📊 Option Scores", value="No effective votes recorded for options.", inline=False)

    # Voting statistics
    stats_text = f"• **Total Ballots Cast**: {total_raw_votes}\n"
    stats_text += f"• **Effective Votes (non-abstain)**: {total_effective_votes}\n"
    stats_text += f"• **Abstain Votes**: {abstain_count}\n"
    embed.add_field(name="📝 Voting Statistics", value=stats_text, inline=False)

    # Hyperparameter Information
    hyperparams = proposal.get('hyperparameters', {})
    if hyperparams: # Check if hyperparameters dict is not empty
        hyperparams_text = ""
        allow_abstain = hyperparams.get('allow_abstain', True) # Default to True if not specified
        hyperparams_text += f"• Allow Abstain Votes: {'Yes' if allow_abstain else 'No'}\n"

        custom_threshold = hyperparams.get('custom_winning_threshold_percentage')
        if custom_threshold is not None:
            try:
                # Validate that it's a number before displaying
                threshold_val = float(custom_threshold)
                hyperparams_text += f"• Custom Winning Threshold: {threshold_val:.1f}% of effective votes\n"
            except ValueError:
                hyperparams_text += f"• Custom Winning Threshold: Invalid value ('{custom_threshold}')\n"
        else:
            # Indicate if no custom threshold was set, implying default (e.g. simple majority)
            if mechanism.lower() == 'plurality': # Only for plurality, as it has a default of >50%
                 hyperparams_text += f"• Winning Condition: Simple Majority (>50% of effective votes)\n"

        # Add other relevant hyperparameters as they are implemented
        # e.g., for Borda: hyperparams.get('borda_variant', 'standard')

        if hyperparams_text: # Only add field if there's text
            embed.add_field(name="⚙️ Voting Configuration", value=hyperparams_text, inline=False)

    # Add round details for Runoff if present
    if 'rounds' in results and results['rounds']:
        rounds_text = ""
        for i, round_info in enumerate(results['rounds'], 1):
            rounds_text += f"**Round {i}:**\n"
            # Ensure scores is a list of tuples or dict before iterating
            scores = round_info.get('scores', [])
            if isinstance(scores, dict): # Convert dict to list of tuples if necessary
                scores = list(scores.items())

            for option, count in scores:
                rounds_text += f"  • {option}: {count} vote(s)\n"
            if round_info.get('eliminated'):
                rounds_text += f"  *Eliminated: {round_info['eliminated']}*\n"
            rounds_text += "---\n"
        if rounds_text:
            embed.add_field(name="🗳️ Runoff Rounds", value=rounds_text, inline=False)

    # Add top quotients for D'Hondt if present
    if 'top_quotients' in results and results['top_quotients']:
        dhondt_text = "**Seats Allocated (based on highest quotients):**\n"
        # Assuming top_quotients is a list of (option, quotient, seat_number) or similar
        # This part might need adjustment based on the actual structure of top_quotients
        for item in results['top_quotients']:
            if isinstance(item, tuple) and len(item) >= 2: # Basic check
                option, quotient = item[0], item[1]
                dhondt_text += f"• **{option}** (Quotient: {quotient:.2f})\n"
            # Add more detailed formatting if seat numbers are included
        embed.add_field(name="🏛️ D'Hondt Allocation Details", value=dhondt_text, inline=False)

    embed.set_footer(text=f"Proposal ID: {proposal_id} | Results calculated at {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}")
    return embed


async def check_expired_proposals() -> List[Dict]:
    """Check for proposals with expired deadlines, close them, and return the list of closed proposals."""
    try:
        # Get all active proposals with expired deadlines
        expired_proposals = await db.get_expired_proposals()

        closed_proposals = []
        for proposal in expired_proposals:
            try:
                print(
                    f"TASK: Closing expired proposal #{proposal['proposal_id']}: {proposal['title']}")

                # Close the proposal and calculate results
                # This function will update status and store results internally
                results = await close_proposal(proposal['proposal_id'])

                if results:
                    # Add the proposal (with updated status) to the list for announcement
                    updated_proposal = await db.get_proposal(proposal['proposal_id'])
                    if updated_proposal:
                        closed_proposals.append(updated_proposal)
                        print(
                            f"TASK: Successfully closed proposal #{proposal['proposal_id']} and added to announcement list.")
                    else:
                        print(f"WARNING: Proposal #{proposal['proposal_id']} closed but failed to refetch for announcement list.")

                else:
                    print(
                        f"WARNING: Failed to close proposal #{proposal['proposal_id']} or calculate results.")
            except Exception as e:
                print(
                    f"ERROR processing expired proposal {proposal['proposal_id']}: {str(e)}")
                import traceback
                traceback.print_exc()

        # Return list of closed proposals (full proposal dicts) that need announcement
        return closed_proposals

    except Exception as e:
        print(f"CRITICAL ERROR checking expired proposals: {str(e)}")
        import traceback
        traceback.print_exc()
        return []


async def close_proposal(proposal_id: int) -> Optional[Dict]:
    """
    Close a proposal, calculate results, update status, and store results.
    Returns the calculated results dictionary or None on failure.
    """
    try:
        # Get the proposal
        proposal = await db.get_proposal(proposal_id)
        if not proposal:
            print(
                f"ERROR: Proposal {proposal_id} not found during close_proposal.")
            return None

        # Only close if it's currently in 'Voting' status
        if proposal['status'] != 'Voting':
            print(f"WARNING: Attempted to close proposal {proposal_id} which is not in 'Voting' status (current status: {proposal['status']}). Skipping.")
             # Return existing results if already closed, otherwise None
            existing_results_json = await db.get_proposal_results_json(proposal_id)
            if existing_results_json:
                try:
                    return json.loads(existing_results_json)
                except: return None
            return None  # Cannot proceed if not voting

        # Get all votes for this proposal
        votes = await db.get_proposal_votes(proposal_id)

        # Get voting mechanism
        mechanism_name = proposal['voting_mechanism'].lower()
        mechanism = get_voting_mechanism(mechanism_name)
        if not mechanism:
            print(
                f"ERROR: Invalid voting mechanism '{mechanism_name}' for closing proposal {proposal_id}.")
            # Update status to reflect issue? Or just leave it? Let's set to failed.
            await db.update_proposal_status(proposal_id, "Failed")
            await db.add_proposal_note(proposal_id, "closure_error", f"Invalid mechanism: {mechanism_name}")
            return None

        # Calculate results using the main calculate_results function in this file
        results = await calculate_results(proposal_id)

        # Determine if proposal passed based on winner existence
        status = "Passed" if results.get('winner') is not None else "Failed"
        print(f"DEBUG: Calculated status for proposal {proposal_id}: {status}")

        # Update the proposal status in the database
        await db.update_proposal_status(proposal_id, status)
        print(f"DEBUG: Updated proposal {proposal_id} status to {status}")

        # Store results in the database
        # Use integer 1 instead of boolean True for SQLite compatibility
        await db.store_proposal_results(proposal_id, results)
        print(f"DEBUG: Stored results for proposal {proposal_id}")

        # Clear the tracking message ID so the periodic task doesn't try to update it
        # await db.update_proposal(proposal_id, {'tracking_message_id': None}) # Or set to 0? Let's use None.
        # Update: It seems better to leave the tracking message and update it once more showing "Voting Closed".
        # The update_voting_message (or similar logic in announce) could handle this.
        # Let's rely on the announce function to update the message.

        # Set flag for announcement pending
        # Use integer 1 for True in SQLite
        await db.update_proposal(proposal_id, {'results_pending_announcement': 1})
        print(
            f"DEBUG: Set results_pending_announcement=1 for proposal {proposal_id}")

        return results

    except Exception as e:
        print(f"CRITICAL ERROR in close_proposal {proposal_id}: {str(e)}")
        import traceback
        traceback.print_exc()
        # Attempt to set status to Failed on critical error
        try:
            await db.update_proposal_status(proposal_id, "Failed")
            await db.add_proposal_note(proposal_id, "closure_error", f"Critical error: {str(e)}")
            # Set flag for announcement pending error
            await db.update_proposal(proposal_id, {'results_pending_announcement': 1})
        except Exception as db_e:
            print(f"ERROR updating proposal {proposal_id} status after critical error: {db_e}")
        return None


async def close_and_announce_results(guild: discord.Guild, proposal: Dict, results: Dict) -> bool:
    """Announce the results of a closed proposal with enhanced visuals"""
    try:
        print(
            f"DEBUG: Starting close_and_announce_results for proposal #{proposal.get('proposal_id')}")

        if not results:
            print(f"DEBUG: No results provided for announcement of proposal #{proposal.get('proposal_id')}. Skipping announcement.")
            return False

        proposal_id = proposal.get('proposal_id')

        # Format results into an embed
        embed = await format_vote_results(results, proposal)
        print(f"DEBUG: Formatted results embed for proposal #{proposal_id}")

        # First try to get the dedicated vote-results channel
        vote_results_channel = discord.utils.get(
            guild.text_channels, name="vote-results")
        if not vote_results_channel:
            # Try to create the vote-results channel
            try:
                print(
                    f"DEBUG: Creating vote-results channel for guild {guild.name}")
                # Define permission overwrites: Everyone can read, but only the bot can send messages
                overwrites = {
                    guild.default_role: discord.PermissionOverwrite(read_messages=True, send_messages=False),
                    guild.me: discord.PermissionOverwrite(
                        read_messages=True, send_messages=True)
                }

                vote_results_channel = await guild.create_text_channel(name="vote-results", overwrites=overwrites)
                await vote_results_channel.send("📊 **Vote Results Channel**\nThis channel displays the results of completed votes.")
                print(f"DEBUG: Created vote-results channel in {guild.name}")
            except Exception as e:
                print(f"ERROR creating vote-results channel: {e}")
                import traceback
                traceback.print_exc()
                vote_results_channel = None

        # Also get the governance-results channel as fallback
        governance_results_channel = discord.utils.get(
            guild.text_channels, name="governance-results")
        if not governance_results_channel and not vote_results_channel:
            # Try to create the governance-results channel as fallback
            try:
                governance_results_channel = await guild.create_text_channel("governance-results")
                await governance_results_channel.send("📊 **Governance Results Channel**\nThis channel shows the results of completed governance votes.")
                print(
                    f"DEBUG: Created governance-results channel in {guild.name}")
            except Exception as e:
                print(f"ERROR creating governance-results channel: {e}")
                import traceback
                traceback.print_exc()
                governance_results_channel = None

        # Use vote-results channel if available, otherwise use governance-results
        results_channel = vote_results_channel or governance_results_channel

        # Send results to the results channel
        if results_channel:
            try:
                await results_channel.send(embed=embed)
                print(
                    f"✅ Results sent to {results_channel.name} channel for proposal #{proposal_id}")
            except Exception as e:
                print(f"ERROR sending results to {results_channel.name}: {e}")
                import traceback
                traceback.print_exc()
                # Try a simpler message as fallback
                await results_channel.send(f"📊 **Proposal #{proposal_id}: Voting has concluded.** Status: {proposal.get('status', 'Unknown').lower()}. Results could not be fully formatted.")

        # Always send the results to the voting-room channel as well
        voting_channel = discord.utils.get(
            guild.text_channels, name="voting-room")
        if voting_channel:
            try:
                # Create a special announcement message
                announcement = f"🔔 **VOTING COMPLETE: Proposal #{proposal_id}**\n\n"
                announcement += f"**Title:** {proposal.get('title', 'Untitled')}\n"
                announcement += f"**Status:** {proposal.get('status', 'Unknown')}\n"

                winner = results.get('winner')
                if winner is not None:  # Use is not None to catch empty strings or 0 if that were possible
                    announcement += f"**Outcome:** 🏆 {winner}\n"
                else:
                    announcement += "**Outcome:** No clear winner.\n"  # Or Failed message if status is Failed

                if results_channel and results_channel.id != voting_channel.id:
                    announcement += f"\nView detailed results in <#{results_channel.id}>"

                # Send the announcement and the embed
                await voting_channel.send(announcement, embed=embed)
                print(
                    f"✅ Results announcement sent to voting-room channel for proposal #{proposal_id}")
            except Exception as e:
                print(f"ERROR sending results to voting-room: {e}")
                import traceback
                traceback.print_exc()
                # Try a simpler message as fallback
                await voting_channel.send(f"🔔 **VOTING COMPLETE: Proposal #{proposal_id}: Voting has concluded.** Status: {proposal.get('status', 'Unknown').lower()}.")

        # Try to update the main voting post message in voting-room to show it's closed
        main_voting_message_id = proposal.get('voting_message_id')
        if main_voting_message_id and voting_channel:
            try:
                main_voting_message = await voting_channel.fetch_message(main_voting_message_id)
                # Update embed to show voting closed
                old_embed = main_voting_message.embeds[0] if main_voting_message.embeds else discord.Embed(
                )
                old_embed.title = f"🔒 CLOSED: {proposal.get('title', 'Untitled')}"  # Use proposal title in case old embed is empty
                old_embed.description = proposal.get('description', '')[:200] + "..."  # Use proposal description
                old_embed.color = discord.Color.dark_gray()
                footer_text = f"Voting closed • See results in #{results_channel.name if results_channel else 'results-channel'}"
                old_embed.set_footer(text=footer_text)

                # Disable any buttons by creating a new view with no items
                await main_voting_message.edit(embed=old_embed, view=None)
                print(
                    f"✅ Updated main voting message {main_voting_message_id} to closed status.")

            except discord.NotFound:
                print(f"WARNING: Main voting message {main_voting_message_id} not found in voting-room.")
            except Exception as e:
                print(
                    f"ERROR updating main voting message {main_voting_message_id}: {e}")
                import traceback
                traceback.print_exc()

        # Try to update the tracking message in voting-room to show final state (if it exists)
        tracking_message_id = proposal.get('tracking_message_id')
        if tracking_message_id and voting_channel:
            try:
                tracking_message = await voting_channel.fetch_message(tracking_message_id)
                # Re-run the update_vote_tracking function one last time
                # It will fetch latest vote data (already closed status), eligible voters, etc.
                # And update the embed with final progress and closed state.
                await update_vote_tracking(guild, proposal_id)  # This function sends OR edits

                # If update_vote_tracking failed to find/edit, it would have sent a new one.
                # If it succeeded in editing, the message is updated.

                print(
                    f"✅ Attempted final update of tracking message {tracking_message_id}.")

            except discord.NotFound:
                print(f"WARNING: Tracking message {tracking_message_id} not found in voting-room.")
            except Exception as e:
                print(
                    f"ERROR attempting final update of tracking message {tracking_message_id}: {e}")
                import traceback
                traceback.print_exc()

        # Attempt to notify the proposer via DM
        try:
            proposer = guild.get_member(proposal['proposer_id'])
            if proposer:
                await proposer.send(f"Your proposal **{proposal.get('title', 'Untitled')}** has concluded with the status: {proposal.get('status', 'Unknown').lower()}.", embed=embed)
                print(f"✅ DM sent to proposer for proposal #{proposal_id}")
            else:
                print(
                    f"DEBUG: Could not find proposer with ID {proposal['proposer_id']} in guild {guild.name}")
        except Exception as e:
            print(f"ERROR sending DM to proposer: {e}")
            import traceback
            traceback.print_exc()

        # Mark the announcement as complete in the database
        # Use integer 0 for False in SQLite
        await db.update_proposal(proposal_id, {'results_pending_announcement': 0})
        print(
            f"DEBUG: Cleared results_pending_announcement=0 for proposal {proposal_id}")

        print(
            f"✅ Results for proposal #{proposal_id} have been announced successfully")
        return True
    except Exception as e:
        print(
            f"CRITICAL ERROR in close_and_announce_results for proposal #{proposal.get('proposal_id')}: {e}")
        import traceback
        traceback.print_exc()  # Print full stack trace for debugging
        # Even on failure, try to clear the pending announcement flag to avoid spamming errors
        if proposal.get('proposal_id'):
            try:
                 await db.update_proposal(proposal['proposal_id'], {'results_pending_announcement': 0})
                 print(
                     f"DEBUG: Attempted to clear pending announcement flag for proposal {proposal.get('proposal_id')} after error.")
            except Exception as db_e:
                print(f"ERROR clearing pending announcement flag after error: {db_e}")
        return False


async def create_vote_post(guild, proposal):
    """Create a voting post with visual indicators"""
    # Get the voting channel
    voting_channel = discord.utils.get(guild.text_channels, name="voting-room")
    if not voting_channel:
        print("ERROR: Voting channel 'voting-room' not found in guild", guild.id)
        return None

    # Extract options from database
    proposal_id = proposal['proposal_id']
    options = await db.get_proposal_options(proposal_id)
    if not options:
        # Fallback if options weren't stored - extract from description
        options = utils.extract_options_from_description(
            proposal.get('description', ''))
        if not options:
            options = ["Yes", "No"]  # Default options
            print(
                f"WARNING: No options found/extracted for proposal {proposal_id}. Using default Yes/No.")

    # Create an embed for the voting post
    embed = create_voting_embed(proposal, options)

    # Add early termination button if applicable
    # Import needed views/functions from proposals
    from proposals import EarlyTerminationView
    view = EarlyTerminationView(proposal_id)

    # Send the voting post
    message = await voting_channel.send(embed=embed, view=view)

    # Store message ID in database for updating
    await db.update_proposal(proposal_id, {'voting_message_id': message.id})

    # Send initial vote tracking message
    await update_vote_tracking(guild, proposal_id)

    # Return the message
    return message


# The functions related to creating/updating vote posts (like create_vote_post, create_voting_embed, get_voting_instructions, format_deadline, update_voting_message) are also related to the *display* of voting, not the *calculation* of results, so they could potentially stay here or move to a `voting_display.py` module if it gets complex. Let's keep them here for now.


def create_voting_embed(proposal: Dict, options: List[str]) -> discord.Embed:
    """Create an embed for voting with progress visualization"""
    # Create base embed
    embed = discord.Embed(
        title=f"🗳️ Vote on Proposal #{proposal.get('proposal_id') or proposal.get('id')}: {proposal.get('title', 'Untitled')}",
        description=proposal.get('description', ''),
        color=discord.Color.blue()
    )

    # Add metadata
    embed.add_field(name="Status", value=proposal.get(
        'status', 'Unknown'), inline=True)
    embed.add_field(name="Proposer",
                    value=f"<@{proposal.get('proposer_id')}>", inline=True)
    embed.add_field(name="Voting Mechanism", value=proposal.get(
        'voting_mechanism', 'Unknown').title(), inline=True)

    # Format deadline and calculate time remaining - Use the robust format_deadline helper from utils
    deadline_data = proposal.get('deadline')
    deadline_str_formatted = utils.format_deadline(
        deadline_data)  # Use helper from utils

    # Calculate time remaining string robustly
    time_remaining_str = "Unknown"
    if isinstance(deadline_data, (str, datetime)):  # Check if data is string or datetime
        try:
            # Attempt to parse the deadline data if it's not already a datetime object
            if isinstance(deadline_data, str):
                deadline_dt = datetime.fromisoformat(
                    deadline_data.replace('Z', '+00:00'))
            else:  # It's already a datetime object
                deadline_dt = deadline_data

            if deadline_dt:
                time_remaining = deadline_dt - datetime.now()
                time_remaining_str = utils.format_time_remaining(
                    time_remaining)  # Use helper from utils

        except ValueError:
            print(
                f"ERROR: Could not parse deadline data for time remaining in create_voting_embed: '{deadline_data}'")
            time_remaining_str = "Error Calculating"
        except Exception as e:
            print(
                f"Unexpected error calculating time remaining in create_voting_embed: {e}")
            time_remaining_str = "Error"
    else:
        print(
            f"WARNING: Unexpected type for deadline data in create_voting_embed: {type(deadline_data)}")
        time_remaining_str = "Unknown"

    # Add time remaining field using the calculated string
    embed.add_field(name="Time Remaining",
                    value=time_remaining_str, inline=True)

    # Add voting instructions based on the mechanism
    mechanism_name = proposal.get('voting_mechanism', 'Unknown').lower()
    # This function is likely in voting_utils or voting
    instructions = get_voting_instructions(mechanism_name, options)
    embed.add_field(name="How to Vote", value=instructions, inline=False)

    # Add options
    if options:
        options_text = "\n".join([f"• {option}" for option in options])
        if options_text:  # Only add if there are options
            embed.add_field(name="Options", value=options_text, inline=False)

    # Add footer
    # Use the formatted deadline string from utils.format_deadline
    embed.set_footer(
        text=f"Vote via DM to the bot • Voting ends at {deadline_str_formatted}")

    return embed


def get_voting_instructions(mechanism, options):
    """Get voting instructions based on the mechanism"""
    mechanism = mechanism.lower()

    # Common instruction
    instructions = "**Send a DM to the bot using the `!vote` command.**\n"
    options_text = ", ".join(
        [f"`{opt}`" for opt in options]) if options else "No options defined."

    if mechanism in ["plurality", "dhondt"]:
        instructions += f"Format: `!vote <proposal_id> <option>`\nChoose *one* option.\nAvailable options: {options_text}"

    elif mechanism in ["borda", "runoff"]:
        instructions += f"Format: `!vote <proposal_id> rank option1,option2,...`\nRank the options in order of preference, separated by commas.\nAvailable options: {options_text}"

    elif mechanism == "approval":
        instructions += f"Format: `!vote <proposal_id> approve option1,option2,...`\nApprove *all* options you support, separated by commas.\nAvailable options: {options_text}"

    else:
        instructions += "Format: `!vote <proposal_id> ...`\nInstructions for this mechanism are not fully implemented. Please contact an admin."

    instructions += f"\n\nTo **Abstain**, send: `!vote <proposal_id> abstain`"

    return instructions



async def get_eligible_voters(guild: discord.Guild, proposal: Dict) -> List[discord.Member]:
    """Get all members eligible to vote on a proposal"""
    # Get constitutional variables
    # Import db only when needed to help with potential circular imports
    # import db # db is imported at the top
    const_vars = await db.get_constitutional_variables(guild.id)
    eligible_voters_role_name = const_vars.get(
        "eligible_voters_role", {"value": "everyone"})["value"]

    eligible_members = []
    all_members = guild.members # Fetching all members is needed to check roles/bots

    if eligible_voters_role_name.lower() == "everyone":
        # Everyone can vote (except bots)
        eligible_members = [member for member in all_members if not member.bot]
    else:
        # Only members with the specified role can vote
        role = discord.utils.get(guild.roles, name=eligible_voters_role_name)
        if role:
            eligible_members = [
                member for member in all_members if role in member.roles and not member.bot]
        else:
            # Role not found, default to everyone (excluding bots)
            print(
                f"WARNING: Eligible voters role '{eligible_voters_role_name}' not found in guild {guild.id}. Defaulting to everyone (excluding bots).")
            eligible_members = [
                member for member in all_members if not member.bot]

    # Exclude the bot itself if it somehow appears in members list (unlikely but safe)
    # Your bot's user ID
    bot_user_id = guild.me.id if guild.me else None
    if bot_user_id:
        eligible_members = [m for m in eligible_members if m.id != bot_user_id]

    # You might have other bots to exclude here if needed
    # VERDICT_BOT_ID = 1337818333239574598 # Example ID, replace with actual if known
    # if VERDICT_BOT_ID:
    #     eligible_members = [m for m in eligible_members if m.id != VERDICT_BOT_ID]


    print(f"DEBUG: Found {len(eligible_members)} eligible voters (excluding bots) in guild {guild.id}")
    return eligible_members


async def update_vote_tracking(guild: discord.Guild, proposal_id: int, final_proposal_state: Optional[Dict] = None):
    """
    Update vote tracking for a proposal.
    If final_proposal_state is provided (a proposal dict for a non-Voting state),
    formats the embed to show the final results state instead of progress.
    """
    # Get proposal (use provided final state if available)
    proposal = final_proposal_state or await db.get_proposal(proposal_id)
    if not proposal:
        print(
            f"WARNING: Proposal {proposal_id} not found for tracking update.")
        return

    # --- ADD THIS LOG ---
    print(
        f"DEBUG: update_vote_tracking called for proposal {proposal_id}. Status: {proposal.get('status')}")

    is_voting_active = proposal.get('status') == "Voting"

    # Get votes (needed for count even if not active)
    # Fetch votes directly from DB using proposal_id
    votes = await db.get_proposal_votes(proposal_id)

    # Count abstain votes
    abstain_count = sum(1 for vote in votes if vote.get(
        'vote_data', {}).get('did_abstain', False))
    non_abstain_count = len(votes) - abstain_count
    # Should be non_abstain_count + abstain_count
    total_votes_cast = len(votes)

    # Get eligible voters (needed for counts)
    # Needs guild member data, so fetch from guild object
    # This function handles fetching eligible members and filtering bots
    eligible_voters = await get_eligible_voters(guild, proposal)
    # This count should only include non-bots
    eligible_count = len(eligible_voters)

    # If eligible voters count is 0 (e.g., no non-bots), use total members or handle appropriately
    if eligible_count == 0 and total_votes_cast > 0:
        # Fallback to total votes cast if we have votes but no eligible voters found
        eligible_count = total_votes_cast
        print(
            f"WARNING: No non-bot eligible voters found for guild {guild.id} but votes exist. Using total votes cast ({eligible_count}) as fallback for tracking base.")
    elif eligible_count == 0:  # Still 0 and no votes cast
        eligible_count = 1  # Avoid div by zero

    # Get invited voters (more accurate base for percentage if available)
    # Fetch invited voter IDs from DB
    invited_voter_ids = await db.get_invited_voters_ids(proposal_id)
    # Use invited count if invites were tracked and found, otherwise default to eligible_count
    invited_count = len(
        invited_voter_ids) if invited_voter_ids is not None else eligible_count
    # Ensure invited_count is at least 1 if total votes > 0, to avoid div by zero
    if invited_count == 0 and total_votes_cast > 0:
        # Use number of votes cast as base if 0 invited somehow
        invited_count = max(total_votes_cast, 1)

    # Get options from database (needed for display)
    options = await db.get_proposal_options(proposal_id)
    if not options:
        options = utils.extract_options_from_description(
            proposal.get('description', '')) or ["Yes", "No"]  # Fallback

    # Create embed
    embed = discord.Embed(
        title=f"📊 Voting Progress: Proposal #{proposal_id}",
        description=f"**{proposal.get('title', 'Untitled')}**\n\n{proposal.get('description', '')[:150]}...",
        # Grey if not active
        color=discord.Color.blue() if is_voting_active else discord.Color.dark_gray()
    )

    # Add voting mechanism info
    embed.add_field(
        name="Voting Mechanism",
        value=proposal.get('voting_mechanism', 'Unknown').title(),
        inline=True
    )

    # Add options
    options_text = "\n".join([f"• {option}" for option in options])
    if options_text:
        embed.add_field(
            name="Options",
            value=options_text,
            inline=True
        )

    # Add vote count and progress if voting is active
    if is_voting_active:
        # Calculate progress based on total votes cast vs invited voters
        # Ensure invited_count is not zero if votes were cast
        progress_percentage = (total_votes_cast / invited_count) * \
            100 if invited_count > 0 else 0
        progress_bar = utils.create_progress_bar(
            progress_percentage)  # Use utils helper

        embed.add_field(
            name="Votes Cast",
            # Show total cast and how many are abstain vs non-abstain
            value=f"Total Cast: {total_votes_cast} ({non_abstain_count} non-abstain, {abstain_count} abstain)\n"
                  # Show both eligible and invited
                  f"Eligible: {eligible_count} ({invited_count} invited)\n"
                  f"Progress: {progress_percentage:.1f}%\n{progress_bar}",
            inline=False
        )

        # Add who has voted (without revealing choices)
        voted_member_ids = {vote['voter_id']
                            for vote in votes}  # Use set for efficiency
        # Filter eligible voters to find who has/hasn't voted
        # Ensure filtering is done against the list of eligible_voters fetched above
        voted_members = [
            m for m in eligible_voters if m.id in voted_member_ids]
        not_voted_members = [
            m for m in eligible_voters if m.id not in voted_member_ids]

        # Create a more detailed breakdown of who has voted
        if voted_members:
            # Show first few voters directly (mentions)
            display_limit = 15
            voted_display_mentions = [
                m.mention for m in voted_members[:display_limit]]
            voted_display = ", ".join(voted_display_mentions)

            # If there are more voters, add a count
            if len(voted_members) > display_limit:
                voted_display += f", +{len(voted_members) - display_limit} more"
            # Ensure it's not an empty string if there are voters but none to display (e.g., limit=0)
            voted_display = voted_display or f"{len(voted_members)} voted"

            embed.add_field(
                name=f"✅ Voted ({len(voted_members)})",
                value=voted_display,
                inline=False
            )
        else:
            embed.add_field(
                name=f"✅ Voted (0)",
                value="None yet",
                inline=False
            )

        if not_voted_members:
            # Show first few non-voters directly (mentions)
            display_limit = 15
            not_voted_display_mentions = [
                m.mention for m in not_voted_members[:display_limit]]
            not_voted_display = ", ".join(not_voted_display_mentions)

            # If there are more non-voters, add a count
            if len(not_voted_members) > display_limit:
                not_voted_display += f", +{len(not_voted_members) - display_limit} more"
            # Ensure it's not an empty string if there are non-voters but none to display
            not_voted_display = not_voted_display or f"{len(not_voted_members)} remaining"

            embed.add_field(
                name=f"⏳ Not Voted Yet ({len(not_voted_members)})",
                value=not_voted_display,
                inline=False
            )
        else:
            embed.add_field(
                name=f"⏳ Not Voted Yet (0)",
                value="None left!",
                inline=False
            )

        # Add deadline and time remaining
        # Ensure proposal['deadline'] is a datetime object or parse it
        deadline_data = proposal.get('deadline')
        time_remaining_str = "Unknown"
        if isinstance(deadline_data, (str, datetime)):  # Check if data is string or datetime
            try:
                if isinstance(deadline_data, str):
                    deadline_dt = datetime.fromisoformat(
                        deadline_data.replace('Z', '+00:00'))
                else:
                    deadline_dt = deadline_data  # It's already a datetime object

                if deadline_dt:
                    time_remaining = deadline_dt - datetime.now()
                    time_remaining_str = utils.format_time_remaining(
                        time_remaining)  # Use utils helper

            except ValueError:
                print(
                    f"ERROR: Could not parse deadline string for tracking time remaining: '{deadline_data}'")
                time_remaining_str = "Error Calculating"
            except Exception as e:
                print(
                    f"Unexpected error calculating time remaining in update_vote_tracking: {e}")
                time_remaining_str = "Error"
        else:
            print(
                f"WARNING: No deadline data or unexpected type for proposal {proposal_id} in tracking update.")
            time_remaining_str = "Unknown"

        embed.add_field(
            name="Time Remaining",
            value=time_remaining_str,
            inline=False
        )

    # Proposal is not active/voting (i.e., closed, passed, failed, rejected)
    else:
        # Update title
        embed.title = f"📊 Voting Concluded: Proposal #{proposal_id}"
        embed.add_field(
            name="Status", value=f"Voting is {proposal.get('status', 'Unknown').lower()}.", inline=False)
        embed.add_field(
            name="Votes Cast", value=f"Total: {total_votes_cast} ({non_abstain_count} non-abstain, {abstain_count} abstain)", inline=False)

        # Get results if available to link to them
        # Need guild object to find channel
        results_channel = discord.utils.get(
            guild.text_channels, name="vote-results") or discord.utils.get(guild.text_channels, name="governance-results")
        if results_channel:
            embed.add_field(
                name="Results", value=f"View detailed results in <#{results_channel.id}>", inline=False)
        else:
            embed.add_field(
                name="Results", value="Results calculated, but results channel not found.", inline=False)

    # Add footer with timestamp
    embed.set_footer(
        text=f"Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}")  # Use UTC for consistency

    # Get voting channel
    # Using utils.get_or_create_channel and passing bot ID
    bot_user_id = guild.me.id if guild.me else None
    voting_channel = await utils.get_or_create_channel(guild, "voting-room", bot_user_id)
    if not voting_channel:
        print("ERROR: Voting channel not found or could not be created for guild",
              guild.id, "in tracking update.")
        return

    # Find the existing tracking message if possible and update it
    tracking_message_id = proposal.get('tracking_message_id')
    tracking_message_updated = False

    if tracking_message_id:
        max_retries = 3
        retry_delay_seconds = 1
        for attempt in range(max_retries):
            try:
                tracking_message = await voting_channel.fetch_message(tracking_message_id)
                # Always remove view on tracking message
                await tracking_message.edit(embed=embed, view=None)
                print(
                    f"Successfully updated tracking message {tracking_message_id} for proposal {proposal_id} on attempt {attempt+1}.")
                tracking_message_updated = True
                break  # Success, exit retry loop
            except discord.NotFound:
                print(
                    f"Tracking message {tracking_message_id} for proposal {proposal_id} not found on attempt {attempt+1}.")
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delay_seconds)
                else:
                    print(
                        f"Failed to find tracking message {tracking_message_id} after {max_retries} attempts.")
                    break  # Failed after retries, exit retry loop
            except Exception as e:
                print(
                    f"Error updating tracking message {tracking_message_id} for proposal {proposal_id} on attempt {attempt+1}: {e}")
                import traceback
                traceback.print_exc()
                # Assuming unexpected errors are not temporary NotFound issues, don't retry fetching.
                break  # Exit retry loop on other errors

    # If the message wasn't found or couldn't be updated AND voting is still active, send a new one.
    if not tracking_message_updated and is_voting_active:
        try:
            print(
                f"Sending a new tracking message for proposal {proposal_id}.")
            new_message = await voting_channel.send(embed=embed)
            # Store the new message ID in the database for this proposal
            await db.update_proposal(proposal_id, {'tracking_message_id': new_message.id})
            print(
                f"Sent new tracking message {new_message.id} for proposal {proposal_id} and saved its ID.")
        except discord.Forbidden:
            print(
                f"ERROR: Bot lacks permissions to send new tracking message in voting-room in guild {guild.name}.")
        except Exception as e:
            print(
                f"ERROR sending new tracking message for proposal {proposal_id}: {e}")
            import traceback
            traceback.print_exc()
    elif not is_voting_active:
        print(
            f"DEBUG: Not sending a new tracking message for proposal {proposal_id} as voting is not active.")


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
