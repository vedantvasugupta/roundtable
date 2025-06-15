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
from proposals import extract_options_from_description, get_proposal_options_from_db # Added get_proposal_options_from_db
# Used for formatting output
from utils import create_progress_bar, format_time_remaining

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
            hyperparameters: An optional dictionary of hyperparameters, e.g., {'winning_threshold': 0.6}.
        """
        if hyperparameters is None:
            hyperparameters = {}

        # Initialize results for all possible options to ensure they are reported, even with 0 votes.
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

        # Convert to sorted list of tuples (option, count)
        sorted_results = sorted(results.items(), key=lambda x: x[1], reverse=True)

        winner = None
        if total_effective_votes > 0 and sorted_results:
            # Determine winner based on highest count and potential threshold
            # Default winning threshold (simple majority of effective votes)
            # If a specific threshold is provided, it overrides the default.
            # Example: 0.5 means > 50% of votes.
            winning_threshold_config = hyperparameters.get('custom_winning_threshold_percentage')
            # Assume threshold is a percentage like 50 for 50%, 60 for 60%

            # The top option is the one with the most votes.
            top_option_name, top_option_votes = sorted_results[0]

            if winning_threshold_config is not None:
                try:
                    threshold_percentage = float(winning_threshold_config) / 100.0
                    required_votes = threshold_percentage * total_effective_votes
                    # Winner must have strictly more votes than threshold requires for > X%
                    # or equal for >= X% (let's use strictly more for now, meaning > X%)
                    # Example: threshold 0.5, 10 votes. Needs > 5 votes (i.e., 6 or more)
                    if top_option_votes > required_votes:
                        winner = top_option_name
                    else:
                        # Did not meet custom threshold
                        winner = None
                        print(f"INFO: Plurality top option {top_option_name} ({top_option_votes}/{total_effective_votes}) did not meet custom threshold {threshold_percentage*100}% ({required_votes} required).")
                except ValueError:
                    print(f"WARNING: Invalid custom_winning_threshold_percentage '{winning_threshold_config}'. Using simple majority.")
                    # Fallback to simple majority if threshold is invalid
                    if top_option_votes > total_effective_votes / 2:
                        winner = top_option_name
                    # If exactly 50% in a two-way tie, or less, no winner by simple majority either.
            else:
                # Default: simple majority (more than 50% of votes)
                if top_option_votes > total_effective_votes / 2:
                    winner = top_option_name
                # Handle cases like a perfect tie in a 2-option scenario (e.g., 5 votes for A, 5 for B out of 10)
                # In such a case, top_option_votes (5) is not > total_effective_votes / 2 (5), so winner remains None. This is correct.

        # If there are multiple options with the same highest number of votes, and they meet the threshold,
        # current logic picks the first one from sorted_results. This is a simple tie-breaking rule.
        # More complex tie-breaking could be added if needed.

        return {
            'mechanism': 'plurality',
            'results': sorted_results,  # List of (option, count) tuples
            'winner': winner,
            'total_effective_votes': total_effective_votes # Pass this back for format_vote_results
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
    def count_votes(votes: List[Dict]):
        """Count approval votes (only non-abstain votes are passed here)"""
        results = {}
        for vote in votes:
            # Add a robustness check: ensure vote_data is actually a dict
            vote_data = vote.get('vote_data')
            if not isinstance(vote_data, dict):
                print(
                    f"WARNING: Expected dict for vote_data but got {type(vote_data)} in ApprovalVoting.count_votes. Skipping vote: {vote}")
                continue  # Skip this vote if data is not a dict

            # Now safely call .get() on the dict
            approved = vote_data.get('approved', [])
            # Add a robustness check: ensure approved is a list
            if not isinstance(approved, list):
                print(
                    f"WARNING: Expected list for 'approved' in vote_data but got {type(approved)}. Skipping vote: {vote}")
                continue  # Skip this vote if 'approved' is not a list

            for option in approved:
                # Add a robustness check: ensure option is a string
                if not isinstance(option, str):
                    print(
                        f"WARNING: Expected string for option in approved list but got {type(option)}. Skipping option: {option}")
                    continue  # Skip this option

                results[option] = results.get(option, 0) + 1


        # Convert to sorted list of tuples
        sorted_results = sorted(
            results.items(), key=lambda x: x[1], reverse=True)

        # Determine winner (highest approvals)
        winner = sorted_results[0][0] if sorted_results and sorted_results[0][1] > 0 else None

        return {
            'mechanism': 'approval',
            'results': sorted_results,  # List of (option, count) tuples
            'winner': winner,
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

        raw_votes_from_db = await db.get_votes_for_proposal(proposal_id)
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
        options = await get_proposal_options_from_db(proposal_id)
        if not options:
            # Fallback to extracting from description if no dedicated options in DB (old proposals?)
            options = extract_options_from_description(proposal.get('description', ''))
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
