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
from utils import create_progress_bar, format_time_remaining, extract_options_from_description, get_ordinal_suffix

# ========================
# 🔹 VOTING MECHANISMS (COUNTING LOGIC)
# ========================
# Keep these here, they implement the counting logic for *non-abstain* votes.


class PluralityVoting:
    @staticmethod
    def count_votes(votes: List[Dict], options: List[str], hyperparameters: Optional[Dict[str, Any]] = None):
        """Counts votes for Plurality voting, considering token investments and hyperparameters."""
        if hyperparameters is None: hyperparameters = {}

        results = {option: {'raw_votes': 0, 'weighted_votes': 0} for option in options}
        total_raw_votes = 0
        total_weighted_votes = 0

        for vote_record in votes: # vote_record is a dict from db.get_proposal_votes
            vote_data = vote_record.get('vote_data') # This is the JSON string from DB
            if isinstance(vote_data, str):
                try:
                    vote_data = json.loads(vote_data)
                except json.JSONDecodeError:
                    print(f"WARNING: Could not decode vote_data JSON: {vote_data}. Skipping vote.")
                    continue

            if not isinstance(vote_data, dict):
                print(f"WARNING: Expected dict for parsed vote_data. Got {type(vote_data)}. Vote: {vote_record}")
                continue

            chosen_option = vote_data.get('option')
            if chosen_option is None or not isinstance(chosen_option, str) or chosen_option not in results:
                print(f"WARNING: Invalid or missing option in vote_data: {chosen_option}. Valid: {options}. Vote: {vote_record}")
                continue

            # Get tokens_invested from the main vote_record (it's a direct column now)
            tokens = vote_record.get('tokens_invested')
            vote_weight = tokens if tokens is not None and tokens > 0 else 1

            results[chosen_option]['raw_votes'] += 1
            results[chosen_option]['weighted_votes'] += vote_weight
            total_raw_votes += 1
            total_weighted_votes += vote_weight

        # Sort by weighted_votes for winner determination
        # Results structure: {option: {'raw_votes': X, 'weighted_votes': Y}}
        # Convert to list of tuples for sorting: (option, {'raw_votes': X, 'weighted_votes': Y})
        sorted_results_detailed = sorted(results.items(), key=lambda item: item[1]['weighted_votes'], reverse=True)

        # For external presentation, often a simpler list of (option, weighted_vote_count) is useful.
        # The main `calculate_results` can decide the final output format.
        # Here, we'll determine winner based on weighted_votes.

        winner = None
        reason_for_no_winner = None

        if total_weighted_votes > 0 and sorted_results_detailed:
            top_option_name, top_option_details = sorted_results_detailed[0]
            top_option_weighted_votes = top_option_details['weighted_votes']

            winning_threshold_config = hyperparameters.get('winning_threshold_percentage')
            if winning_threshold_config is not None:
                try:
                    threshold_percentage_value = float(winning_threshold_config)
                    if not (0 <= threshold_percentage_value <= 100):
                        reason_for_no_winner = "Invalid threshold configuration (0-100 required)."
                        # Fallback to simple majority on total_weighted_votes
                        if top_option_weighted_votes > total_weighted_votes / 2:
                            winner = top_option_name
                        elif len(sorted_results_detailed) > 1 and sorted_results_detailed[1][1]['weighted_votes'] == top_option_weighted_votes:
                            reason_for_no_winner = "Tie (simple majority fallback)."
                        else:
                            reason_for_no_winner = "No majority (simple majority fallback)."
                    else:
                        required_weighted_votes = (threshold_percentage_value / 100.0) * total_weighted_votes
                        if top_option_weighted_votes >= required_weighted_votes: # Use >= for threshold
                            # Check for ties at this threshold
                            tied_winners = [name for name, details in sorted_results_detailed if details['weighted_votes'] == top_option_weighted_votes and details['weighted_votes'] >= required_weighted_votes]
                            if len(tied_winners) == 1:
                                winner = top_option_name
                            else:
                                winner = None # Explicitly no single winner if tie at threshold
                                reason_for_no_winner = f"Tie between {len(tied_winners)} options at threshold."
                        else:
                            reason_for_no_winner = f"Threshold of {threshold_percentage_value}% not met. Top option: { (top_option_weighted_votes / total_weighted_votes * 100) if total_weighted_votes > 0 else 0 :.2f}%."
                            if len(sorted_results_detailed) > 1 and sorted_results_detailed[1][1]['weighted_votes'] == top_option_weighted_votes:
                                reason_for_no_winner += " (Tie for highest votes)"
                except ValueError:
                    reason_for_no_winner = "Invalid threshold format."
                    # Fallback logic as above
                    if top_option_weighted_votes > total_weighted_votes / 2:
                        winner = top_option_name
                    elif len(sorted_results_detailed) > 1 and sorted_results_detailed[1][1]['weighted_votes'] == top_option_weighted_votes:
                        reason_for_no_winner = "Tie (simple majority fallback)."
                    else:
                        reason_for_no_winner = "No majority (simple majority fallback)."
            else: # Simple majority based on weighted votes
                if top_option_weighted_votes > total_weighted_votes / 2:
                    winner = top_option_name
                elif len(sorted_results_detailed) > 1 and sorted_results_detailed[1][1]['weighted_votes'] == top_option_weighted_votes: # Tie for top
                    reason_for_no_winner = "Tie (simple majority)."
                else:
                    reason_for_no_winner = "No majority (simple majority)."
        elif not sorted_results_detailed or total_weighted_votes == 0:
            reason_for_no_winner = "No effective votes cast."

        return {
            'mechanism': 'plurality',
            'results_detailed': sorted_results_detailed, # [(option, {'raw_votes': X, 'weighted_votes': Y}), ...]
            'winner': winner,
            'total_raw_votes': total_raw_votes,
            'total_weighted_votes': total_weighted_votes,
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
    def count_votes(votes: List[Dict], options: List[str], hyperparameters: Optional[Dict[str, Any]] = None):
        """Counts Borda votes, applying token weighting."""
        # options provided by calculate_results are the definitive list of valid options for the proposal
        points = {option: {'raw_score': 0, 'weighted_score': 0} for option in options}
        all_options_actually_ranked = set() # To track options that received any ranking
        total_raw_ranking_sets = 0 # Number of voters who submitted valid rankings
        total_weighted_ranking_power = 0 # Sum of tokens from voters who submitted valid rankings

        for vote_record in votes:
            vote_data_str = vote_record.get('vote_data')
            try:
                vote_data = json.loads(vote_data_str) if isinstance(vote_data_str, str) else vote_data_str
            except json.JSONDecodeError:
                print(f"WARNING: Borda: Could not decode vote_data JSON: {vote_data_str}. Vote: {vote_record}")
                continue

            if not isinstance(vote_data, dict) or not isinstance(vote_data.get('rankings'), list):
                print(f"WARNING: Borda: Invalid vote_data structure or missing rankings. Vote: {vote_record}")
                continue

            rankings = [r for r in vote_data['rankings'] if isinstance(r, str) and r in options]
            if not rankings: # Skip if no valid rankings provided for known options
                continue

            vote_weight = vote_record.get('tokens_invested', 1) # Default to 1 if no tokens
            if vote_weight is None or vote_weight < 0: vote_weight = 1 # Ensure positive weight

            total_raw_ranking_sets += 1
            total_weighted_ranking_power += vote_weight

            num_ranked_options_by_voter = len(rankings)
            for i, ranked_option in enumerate(rankings):
                # Standard Borda: k points for 1st, k-1 for 2nd, ..., 1 for kth (if k options ranked by voter)
                # Or, N-1 for 1st, N-2 for 2nd, ..., 0 for Nth (if N total options, voter ranks all)
                # We use: (num_ranked_options_by_voter - 1) - i points for the option at index i
                # This means the first choice gets (num_ranked_options_by_voter - 1) points, last gets 0.
                # This handles partial rankings correctly.
                score_for_this_rank = (num_ranked_options_by_voter - 1) - i

                points[ranked_option]['raw_score'] += score_for_this_rank
                points[ranked_option]['weighted_score'] += score_for_this_rank * vote_weight
                all_options_actually_ranked.add(ranked_option)

        # Ensure all official options are in the results, even if they got 0 score.
        # The initial `points` dict already does this.

        sorted_results_detailed = sorted(points.items(), key=lambda item: item[1]['weighted_score'], reverse=True)

        winner = None
        reason_for_no_winner = None
        if total_weighted_ranking_power > 0 and sorted_results_detailed:
            top_option_name, top_option_details = sorted_results_detailed[0]
            if top_option_details['weighted_score'] > 0: # Must have some positive score
                # Check for ties for the top score
                tied_winners = [name for name, details in sorted_results_detailed if details['weighted_score'] == top_option_details['weighted_score']]
                if len(tied_winners) == 1:
                    winner = top_option_name
                else:
                    reason_for_no_winner = f"Tie for highest score among {len(tied_winners)} options."
            else:
                reason_for_no_winner = "No option received a positive weighted score."
        else:
            reason_for_no_winner = "No effective votes or rankings cast."

        return {
            'mechanism': 'borda',
            'results_detailed': sorted_results_detailed, # (option, {'raw_score': X, 'weighted_score': Y})
            'winner': winner,
            'total_raw_vote_sets': total_raw_ranking_sets, # Renamed for clarity
            'total_weighted_vote_power': total_weighted_ranking_power, # Renamed for clarity
            'reason_for_no_winner': reason_for_no_winner if winner is None else None,
            'options_ranked': list(all_options_actually_ranked) # List options that got any rank
        }

    @staticmethod
    def get_description():
        return "Voters rank options. Points are assigned based on rank (more for higher ranks). Option with most points wins."

    @staticmethod
    def get_vote_instructions():
        # Instructions are now generated in voting.py's get_voting_instructions
        return "Instructions defined in voting.py"


class ApprovalVoting:
    """Approval voting system"""

    @staticmethod
    def count_votes(votes: List[Dict], options: List[str], hyperparameters: Optional[Dict[str, Any]] = None):
        """Counts approval votes, applying token weighting."""
        results = {option: {'raw_approvals': 0, 'weighted_approvals': 0} for option in options}
        total_raw_voters = 0 # Number of unique voters who cast effective (approval) votes
        total_weighted_voting_power = 0 # Sum of tokens from these voters

        for vote_record in votes:
            vote_data_str = vote_record.get('vote_data')
            try:
                vote_data = json.loads(vote_data_str) if isinstance(vote_data_str, str) else vote_data_str
            except json.JSONDecodeError:
                print(f"WARNING: Approval: Could not decode vote_data JSON: {vote_data_str}. Vote: {vote_record}")
                continue

            if not isinstance(vote_data, dict) or not isinstance(vote_data.get('approved'), list):
                print(f"WARNING: Approval: Invalid vote_data structure or missing approved list. Vote: {vote_record}")
                continue

            approved_options_by_voter = [opt for opt in vote_data['approved'] if isinstance(opt, str) and opt in options]
            if not approved_options_by_voter: # Skip if voter approved no valid options
                continue

            vote_weight = vote_record.get('tokens_invested', 1)
            if vote_weight is None or vote_weight < 0: vote_weight = 1

            total_raw_voters += 1
            total_weighted_voting_power += vote_weight

            for approved_option in approved_options_by_voter:
                results[approved_option]['raw_approvals'] += 1
                results[approved_option]['weighted_approvals'] += vote_weight

        sorted_results_detailed = sorted(results.items(), key=lambda item: item[1]['weighted_approvals'], reverse=True)

        winner = None # Approval voting often doesn't declare a single winner unless specific rules (e.g. fixed number of winners)
        reason_for_no_winner = "Approval voting typically highlights all approved options above a threshold, not a single winner unless specified by other rules."
        # For simplicity, we can declare the one with most weighted approvals as a primary 'winner' if desired.
        # Or, list all options that meet a certain approval percentage if a hyperparameter for that exists.

        # Let's find the option(s) with the most weighted approvals.
        # If there are any votes at all.
        if total_weighted_voting_power > 0 and sorted_results_detailed and sorted_results_detailed[0][1]['weighted_approvals'] > 0:
            top_option_name, top_option_details = sorted_results_detailed[0]
            max_weighted_approvals = top_option_details['weighted_approvals']

            potential_winners = [name for name, details in sorted_results_detailed if details['weighted_approvals'] == max_weighted_approvals]

            if len(potential_winners) == 1:
                winner = potential_winners[0]
                reason_for_no_winner = None # Clear reason if single winner
            else:
                winner = None # Multiple tied for top
                reason_for_no_winner = f"Tie for most approvals among {len(potential_winners)} options."
        elif total_weighted_voting_power == 0:
            reason_for_no_winner = "No effective votes cast."
        else: # votes cast, but top option has 0 weighted approvals (shouldn't happen if validation is correct)
             reason_for_no_winner = "No option received any approvals."

        return {
            'mechanism': 'approval',
            'results_detailed': sorted_results_detailed, # (option, {'raw_approvals': X, 'weighted_approvals': Y})
            'winner': winner, # Can be None if tied or no approvals
            'total_raw_voters': total_raw_voters,
            'total_weighted_voting_power': total_weighted_voting_power,
            'reason_for_no_winner': reason_for_no_winner
        }

    @staticmethod
    def get_description():
        return "Voters can approve of (vote for) as many options as they like. The option(s) with the most approval wins."

    @staticmethod
    def get_vote_instructions():
        # Instructions are now generated in voting.py's get_voting_instructions
        return "Instructions defined in voting.py"


class RunoffVoting:
    """Instant runoff voting system"""

    @staticmethod
    def count_votes(votes: List[Dict], options: List[str], hyperparameters: Optional[Dict[str, Any]] = None):
        """Counts Instant Runoff Voting (IRV) votes, applying token weighting."""

        processed_ballots = []
        for vote_record in votes:
            vote_data_str = vote_record.get('vote_data')
            try:
                vote_data = json.loads(vote_data_str) if isinstance(vote_data_str, str) else vote_data_str
            except json.JSONDecodeError:
                print(f"WARNING: Runoff: Could not decode vote_data JSON: {vote_data_str}. Vote: {vote_record}")
                continue

            if not isinstance(vote_data, dict) or not isinstance(vote_data.get('rankings'), list):
                print(f"WARNING: Runoff: Invalid vote_data or missing rankings. Vote: {vote_record}")
                continue

            # Filter rankings to valid, known options & maintain order
            current_rankings = [opt for opt in vote_data['rankings'] if isinstance(opt, str) and opt in options]
            if not current_rankings:
                continue # Voter ranked no valid options

            vote_weight = vote_record.get('tokens_invested', 1)
            if vote_weight is None or vote_weight < 0: vote_weight = 1

            processed_ballots.append({'original_rankings': list(current_rankings), 'current_rankings': list(current_rankings), 'weight': vote_weight, 'exhausted': False})

        if not processed_ballots:
            return {'mechanism': 'runoff', 'winner': None, 'reason_for_no_winner': 'No valid ballots cast.', 'rounds_detailed': [], 'total_raw_ballots': 0, 'total_weighted_ballot_power': 0}

        active_options = set(options)
        round_details_history = []
        total_raw_ballots_submitted = len(processed_ballots)
        total_weighted_ballot_power_submitted = sum(b['weight'] for b in processed_ballots)

        for round_num in range(1, len(options) + 1): # Max rounds = number of options
            current_round_weighted_votes = {opt: 0 for opt in active_options}
            current_round_raw_ballots = {opt: 0 for opt in active_options}
            active_ballots_in_round = 0
            weighted_ballot_power_in_round = 0

            for ballot in processed_ballots:
                if ballot['exhausted']:
                    continue

                found_preference_in_round = False
                for pref_opt in ballot['current_rankings']:
                    if pref_opt in active_options:
                        current_round_weighted_votes[pref_opt] += ballot['weight']
                        current_round_raw_ballots[pref_opt] += 1
                        active_ballots_in_round +=1
                        weighted_ballot_power_in_round += ballot['weight']
                        found_preference_in_round = True
                        break # Count only the highest active preference
                if not found_preference_in_round:
                    ballot['exhausted'] = True # Ballot has no more active options to transfer to

            round_summary = {
                'round_number': round_num,
                'weighted_votes_per_option': dict(current_round_weighted_votes),
                'raw_ballots_per_option': dict(current_round_raw_ballots),
                'active_options_in_round': list(active_options),
                'exhausted_ballots_this_round': sum(1 for b in processed_ballots if b['exhausted'] and not any(rd['exhausted_ballots_this_round'] == sum(1 for b_prev in processed_ballots if b_prev['exhausted']) for rd in round_details_history)) # Crude check for new exhaustions
            }
            round_details_history.append(round_summary)

            if not active_options or weighted_ballot_power_in_round == 0: # No more votes to count or options left
                break

            # Check for winner (majority of weighted votes in this round)
            # Majority threshold is based on non-exhausted, weighted ballot power in this round
            majority_threshold = weighted_ballot_power_in_round / 2.0
            sorted_candidates_this_round = sorted(current_round_weighted_votes.items(), key=lambda x: x[1], reverse=True)

            if sorted_candidates_this_round[0][1] > majority_threshold:
                winner = sorted_candidates_this_round[0][0]
                return {
                    'mechanism': 'runoff',
                    'winner': winner,
                    'reason_for_no_winner': None,
                    'rounds_detailed': round_details_history,
                    'total_raw_ballots': total_raw_ballots_submitted,
                    'total_weighted_ballot_power': total_weighted_ballot_power_submitted
                }

            if len(active_options) <= 1: # Should have been caught by majority or no votes
                break # Should not happen if logic is correct, but a safeguard

            # Elimination: find candidate(s) with fewest weighted_votes in this round
            if not sorted_candidates_this_round: break # No candidates somehow
            min_votes_this_round = sorted_candidates_this_round[-1][1]
            to_eliminate_this_round = {opt for opt, count in current_round_weighted_votes.items() if count == min_votes_this_round}

            # If all remaining candidates are tied, it's a tie (no single winner)
            if len(to_eliminate_this_round) == len(active_options):
                return {
                    'mechanism': 'runoff',
                    'winner': None,
                    'reason_for_no_winner': f"Tie among all {len(active_options)} remaining options.",
                    'rounds_detailed': round_details_history,
                    'total_raw_ballots': total_raw_ballots_submitted,
                    'total_weighted_ballot_power': total_weighted_ballot_power_submitted
                }

            for opt_to_eliminate in to_eliminate_this_round:
                if opt_to_eliminate in active_options: # Ensure it hasn't been removed by mistake
                    active_options.remove(opt_to_eliminate)
                    # For ballots whose top current preference was eliminated, their rankings need re-evaluation in next round
                    # No need to explicitly modify ballot['current_rankings'] here; the loop for pref_opt will naturally skip eliminated ones.

        # If loop finishes without a majority winner (e.g. all remaining options eliminated due to tie for last)
        # Or if down to one active option but it didn't cross majority (e.g. due to exhausted ballots)
        if len(active_options) == 1:
             # If only one option remains, it's the winner by default of IRV process, even if not majority of initial total power.
            winner = list(active_options)[0]
            return {
                    'mechanism': 'runoff',
                    'winner': winner,
                    'reason_for_no_winner': None,
                    'rounds_detailed': round_details_history,
                    'total_raw_ballots': total_raw_ballots_submitted,
                    'total_weighted_ballot_power': total_weighted_ballot_power_submitted
                }

        return {
            'mechanism': 'runoff',
            'winner': None,
            'reason_for_no_winner': 'Could not determine a winner after all rounds (e.g., unbreakable tie or all ballots exhausted before majority).',
            'rounds_detailed': round_details_history,
            'total_raw_ballots': total_raw_ballots_submitted,
            'total_weighted_ballot_power': total_weighted_ballot_power_submitted
        }

    @staticmethod
    def get_description():
        return "Voters rank options. If no majority, lowest-ranked option is eliminated & votes transfer until one has a majority."

    @staticmethod
    def get_vote_instructions():
        # Instructions are now generated in voting.py's get_voting_instructions
        return "Instructions defined in voting.py"


class DHondtMethod:
    """D'Hondt method for proportional representation"""

    @staticmethod
    def count_votes(votes: List[Dict], options: List[str], hyperparameters: Optional[Dict[str, Any]] = None):
        """Counts D'Hondt votes, applying token weighting, typically for seat allocation."""
        if hyperparameters is None: hyperparameters = {}
        num_seats_to_allocate = hyperparameters.get('num_seats', 1) # Default to 1 seat (like plurality winner)
        try:
            num_seats_to_allocate = int(num_seats_to_allocate)
            if num_seats_to_allocate <= 0:
                num_seats_to_allocate = 1 # Fallback if invalid
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
    """Calculates the results for a given proposal, handling token weighting for campaigns."""
    try:
        proposal = await db.get_proposal(proposal_id)
        if not proposal:
            print(f"ERROR: Proposal {proposal_id} not found for calculating results.")
            return None

        all_db_votes = await db.get_proposal_votes(proposal_id) # Assumed to fetch tokens_invested
        if all_db_votes is None: # Check if fetch failed or returned None
            print(f"ERROR: Failed to fetch votes for proposal {proposal_id}.")
            return None # Or handle as empty list if appropriate

        # Separate abstain votes. Abstain votes have vote_record.is_abstain = True (or 1)
        # The `vote_data` (JSON) for abstain might be empty or indicate abstain.
        # The crucial part is the `is_abstain` column from the `votes` table.
        abstain_votes_records = [v for v in all_db_votes if v.get('is_abstain')]
        effective_vote_records = [v for v in all_db_votes if not v.get('is_abstain')]

        num_abstain_votes = len(abstain_votes_records)
        # Tokens invested in abstain votes might be relevant for auditing, but not for winner calculation.
        tokens_in_abstain = sum(v.get('tokens_invested', 0) for v in abstain_votes_records if v.get('tokens_invested'))

        options_from_db = await db.get_proposal_options(proposal_id)
        if not options_from_db:
            # Fallback: Try to extract from description - this might be less reliable
            options_from_db = extract_options_from_description(proposal.get('description', ''))
            if not options_from_db: # Ultimate fallback if still no options
                options_from_db = ["Yes", "No"]
                print(f"WARNING: P#{proposal_id} No options in DB or description. Defaulting to Yes/No.")

        options = options_from_db # Use the determined options list

        mechanism_name = proposal.get('voting_mechanism', 'plurality').lower()
        hyperparameters = proposal.get('hyperparameters') # This should be a dict
        if isinstance(hyperparameters, str): # Guard against stored as string
            try: hyperparameters = json.loads(hyperparameters)
            except json.JSONDecodeError: hyperparameters = {}
        elif hyperparameters is None: hyperparameters = {}

        results_summary = None
        mechanism_module = get_voting_mechanism(mechanism_name)

        if mechanism_module:
            # Pass effective_vote_records (which include tokens_invested directly)
            # The count_votes method of the mechanism will handle the weighting.
            results_summary = mechanism_module.count_votes(effective_vote_records, options, hyperparameters)
        else:
            print(f"ERROR: Unknown voting mechanism: {mechanism_name} for P#{proposal_id}")
            return None

        if results_summary:
            results_summary['proposal_id'] = proposal_id
            results_summary['num_abstain_votes'] = num_abstain_votes
            results_summary['tokens_in_abstain_votes'] = tokens_in_abstain # Add for info
            results_summary['options_used_for_tally'] = options # Record what options were used

            # Determine final status based on winner/reason
            final_status = "Unknown"
            if results_summary.get('winner'):
                final_status = "Passed" # Or more specific like "Winner: [Name]"
            elif results_summary.get('reason_for_no_winner') == "Tie": # Needs exact match for "Tie" reason
                final_status = "Tied"
            elif results_summary.get('reason_for_no_winner'):
                final_status = "Failed" # Generic fail if no winner and not a specific tie
            elif results_summary.get('total_weighted_votes', 0) == 0 and num_abstain_votes == 0:
                final_status = "No Votes"
            elif results_summary.get('total_weighted_votes', 0) == 0 and num_abstain_votes > 0:
                final_status = "Abstained"

            results_summary['final_status_derived'] = final_status

        return results_summary

    except Exception as e:
        print(f"CRITICAL ERROR calculating results for P#{proposal_id}: {e}")
        import traceback
        traceback.print_exc()
        return None


async def format_vote_results(results: Dict, proposal: Dict) -> discord.Embed:
    """Format the results of a vote into a Discord embed"""
    # Basic proposal info
    proposal_id = proposal.get('proposal_id') or proposal.get('id')
    title = proposal.get('title', 'Untitled Proposal')
    mechanism = results.get('mechanism', 'unknown')
    hyperparameters = proposal.get('hyperparameters')  # Get hyperparameters from the proposal dict
    if isinstance(hyperparameters, str):
        try:
            hyperparameters = json.loads(hyperparameters)
        except json.JSONDecodeError:
            print(f"Warning: Could not parse hyperparameters JSON for proposal {proposal_id}: {hyperparameters}")
            hyperparameters = {}
    elif hyperparameters is None:
        hyperparameters = {}


    # Determine color and status message
    winner = results.get('winner')
    status_message = ""
    if winner:
        color = discord.Color.green()
        status_message = f"🏆 Winner: {winner}"
    elif results.get('reason_for_no_winner'):
        color = discord.Color.orange() # Orange for no winner due to specific reason
        status_message = f"⚠️ No Winner: {results.get('reason_for_no_winner')}"
    elif mechanism == 'plurality' and not results.get('results'): # No votes cast for plurality
        color = discord.Color.greyple()
        status_message = "No votes were cast."
    else: # Generic tie or other no-winner scenario not yet explicitly handled by reason_for_no_winner
        color = discord.Color.gold()
        status_message = "Result: Tie or no decisive winner."
        if not results.get('results'): # If results list is empty
            status_message = "No votes were cast or no options eligible."


    embed = discord.Embed(
        title=f"Results for Proposal #{proposal_id}: {title}",
        description=status_message,
        color=color
    )

    # Add specific mechanism results
    if mechanism == 'plurality':
        total_votes = results.get('total_weighted_votes', 0)
        embed.add_field(name="Voting Method", value="Plurality Voting", inline=True)
        embed.add_field(name="Total Votes Cast", value=str(total_votes), inline=True)

        # Display winning threshold if it was set for this proposal
        if hyperparameters and 'winning_threshold_percentage' in hyperparameters and hyperparameters['winning_threshold_percentage'] is not None:
            threshold = hyperparameters['winning_threshold_percentage']
            embed.add_field(name="Winning Threshold", value=f"{threshold}% of total votes", inline=True)
        else:
            embed.add_field(name="Winning Threshold", value="Simple Majority (>50%)", inline=True)

        if results.get('results_detailed'):
            results_text = ""
            for option, details in results.get('results_detailed'):
                percentage = (details['weighted_votes'] / total_votes) * 100 if total_votes > 0 else 0
                results_text += f"• {option}: {details['weighted_votes']} votes ({percentage:.2f}%)\n"
            if not results_text: # Should not happen if results.get('results_detailed') is true
                 results_text = "No votes recorded for options."
            embed.add_field(name="Vote Counts", value=results_text, inline=False)
        else:
            embed.add_field(name="Vote Counts", value="No votes recorded.", inline=False)

    # Add general results
    if mechanism != 'plurality':
        if results.get('results'):
            results_text = ""
            for option, count in results.get('results'):
                results_text += f"• {option}: {count} votes\n"
            if not results_text: # Should not happen if results.get('results') is true
                 results_text = "No votes recorded for options."
            embed.add_field(name="Vote Counts", value=results_text, inline=False)
        else:
            embed.add_field(name="Vote Counts", value="No votes recorded.", inline=False)

    # Add abstain count
    abstain_count = results.get('num_abstain_votes', 0)
    embed.add_field(name="Abstain Votes", value=str(abstain_count), inline=True)

    # Add total raw votes
    total_raw_votes = results.get('total_raw_votes', results.get('total_weighted_votes', 0) + abstain_count)
    embed.add_field(name="Total Ballots Cast", value=str(total_raw_votes), inline=True)

    # Add total effective votes
    total_effective_votes = results.get('total_weighted_votes', 0)
    embed.add_field(name="Effective Votes (non-abstain)", value=str(total_effective_votes), inline=True)

    # Add mechanism description
    embed.add_field(name="Voting Mechanism", value=mechanism.title(), inline=True)

    # Add proposal title and ID
    embed.add_field(name="Proposal Title", value=title, inline=True)
    embed.add_field(name="Proposal ID", value=str(proposal_id), inline=True)

    # Add winner if available
    if winner:
        embed.add_field(name="Winner", value=winner, inline=True)

    # Add reason for no winner if available
    if results.get('reason_for_no_winner'):
        embed.add_field(name="Reason for No Winner", value=results.get('reason_for_no_winner'), inline=False)

    # Add hyperlink to proposal
    proposal_url = f"https://yourserver.com/proposals/{proposal_id}" # Replace with actual URL
    embed.add_field(name="View Proposal", value=f"[View Proposal]({proposal_url})", inline=False)

    # Add footer with timestamp
    embed.set_footer(text=f"Results calculated at {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}")

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
