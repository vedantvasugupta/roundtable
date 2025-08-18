import asyncio
import datetime
import json
import traceback
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING, Union

import discord
from discord.ext import commands

import db  # Assuming db can be imported here
import utils

# Import CHANNELS from utils
from utils import CHANNELS

# Import functions/classes from voting and proposals only if strictly necessary and non-circular
# Avoid importing the whole module if only specific items are needed.
# For example, formatting functions might need proposal data structures.
# extract_options_from_description is needed here for fallback, so import it.
# create_progress_bar is needed for formatting results, so import it from utils.
# Used as fallback for options
from db import get_proposal_options
# Used for formatting output
from utils import (
    create_progress_bar,
    extract_options_from_description,
    format_time_remaining,
    get_ordinal_suffix,
)

if TYPE_CHECKING:
    from proposals import CampaignControlView

# ========================
# 🔹 VOTING MECHANISMS (COUNTING LOGIC)
# ========================
# Keep these here, they implement the counting logic for *non-abstain* votes.


class PluralityVoting:
    """Plurality voting where a single option is selected.

    Vote weights follow these rules:
    - ``tokens`` > 0: weight equals ``tokens``
    - ``tokens`` == 0: weight is 0 (vote has no effect)
    - ``tokens`` is ``None``: weight defaults to 1 (non-campaign context)
    """

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

            # Determine vote weight based on tokens_invested
            tokens = vote_record.get('tokens_invested')
            if tokens is None:
                vote_weight = 1
            elif tokens > 0:
                vote_weight = tokens
            else:
                vote_weight = 0

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
        winning_threshold_percentage = None

        if total_weighted_votes > 0 and sorted_results_detailed:
            top_option_name, top_option_details = sorted_results_detailed[0]
            top_option_weighted_votes = top_option_details['weighted_votes']

            winning_threshold_config = hyperparameters.get('winning_threshold_percentage')
            if winning_threshold_config is not None:
                try:
                    threshold_percentage_value = float(winning_threshold_config)
                    winning_threshold_percentage = threshold_percentage_value
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
            'winning_threshold_percentage': winning_threshold_percentage,
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
    """Borda count voting system assigning points based on rankings.

    Vote weights follow these rules:
    - ``tokens`` > 0: weight equals ``tokens``
    - ``tokens`` == 0: weight is 0 (vote has no effect)
    - ``tokens`` is ``None``: weight defaults to 1 (non-campaign context)
    """

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

            tokens = vote_record.get('tokens_invested')
            if tokens is None:
                vote_weight = 1
            elif tokens > 0:
                vote_weight = tokens
            else:
                vote_weight = 0

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
    """Approval voting where voters may back multiple options.

    Vote weights follow these rules:
    - ``tokens`` > 0: weight equals ``tokens``
    - ``tokens`` == 0: weight is 0 (vote has no effect)
    - ``tokens`` is ``None``: weight defaults to 1 (non-campaign context)
    """

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

            tokens = vote_record.get('tokens_invested')
            if tokens is None:
                vote_weight = 1
            elif tokens > 0:
                vote_weight = tokens
            else:
                vote_weight = 0

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
    """Instant runoff voting where ranked choices are evaluated in rounds.

    Vote weights follow these rules:
    - ``tokens`` > 0: weight equals ``tokens``
    - ``tokens`` == 0: weight is 0 (vote has no effect)
    - ``tokens`` is ``None``: weight defaults to 1 (non-campaign context)
    """

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

            tokens = vote_record.get('tokens_invested')
            if tokens is None:
                vote_weight = 1
            elif tokens > 0:
                vote_weight = tokens
            else:
                vote_weight = 0

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



class CondorcetMethod:
    """Condorcet method based on pairwise comparisons"""

    @staticmethod
    def count_votes(votes: List[Dict], options: List[str], hyperparameters: Optional[Dict[str, Any]] = None):
        """Counts Condorcet votes using weighted pairwise comparisons."""
        if hyperparameters is None:
            hyperparameters = {}

        pairwise_matrix = {a: {b: 0 for b in options if b != a} for a in options}
        total_raw_ballots = 0
        total_weighted_ballot_power = 0

        for vote_record in votes:
            vote_data_str = vote_record.get('vote_data')
            try:
                vote_data = json.loads(vote_data_str) if isinstance(vote_data_str, str) else vote_data_str
            except json.JSONDecodeError:
                print(f"WARNING: Condorcet: Could not decode vote_data JSON: {vote_data_str}. Vote: {vote_record}")
                continue

            if not isinstance(vote_data, dict) or not isinstance(vote_data.get('rankings'), list):
                print(f"WARNING: Condorcet: Invalid vote_data structure or missing rankings. Vote: {vote_record}")
                continue

            rankings = [r for r in vote_data['rankings'] if isinstance(r, str) and r in options]
            if not rankings:
                continue

            weight = vote_record.get('tokens_invested', 1)
            if weight is None or weight < 0:
                weight = 1

            total_raw_ballots += 1
            total_weighted_ballot_power += weight

            rank_order = {opt: idx for idx, opt in enumerate(rankings)}
            default_rank = len(options)

            for i in range(len(options)):
                for j in range(i + 1, len(options)):
                    a = options[i]
                    b = options[j]
                    rank_a = rank_order.get(a, default_rank)
                    rank_b = rank_order.get(b, default_rank)
                    if rank_a < rank_b:
                        pairwise_matrix[a][b] += weight
                    elif rank_b < rank_a:
                        pairwise_matrix[b][a] += weight
                    # Ties contribute nothing

        winner = None
        for option in options:
            beats_all = True
            for opponent in options:
                if option == opponent:
                    continue
                if pairwise_matrix[option].get(opponent, 0) <= pairwise_matrix[opponent].get(option, 0):
                    beats_all = False
                    break
            if beats_all:
                winner = option
                break

        reason_for_no_winner = None if winner else "No Condorcet winner (cycle or tie)."

        return {
            'mechanism': 'condorcet',
            'pairwise_matrix': pairwise_matrix,
            'winner': winner,
            'reason_for_no_winner': reason_for_no_winner if not winner else None,
            'total_raw_ballots': total_raw_ballots,
            'total_weighted_ballot_power': total_weighted_ballot_power
        }

    @staticmethod
    def get_description():
        return "Compares options head-to-head; an option that beats every other wins."

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
        "condorcet": CondorcetMethod
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
                final_status = "Passed"  # Or more specific like "Winner: [Name]"
            elif results_summary.get('reason_for_no_winner') == "Tie":  # Needs exact match for "Tie" reason
                final_status = "Tied"
            elif results_summary.get('reason_for_no_winner'):
                final_status = "Failed"  # Generic fail if no winner and not a specific tie
            else:
                # Map each mechanism to its corresponding total-weight field
                total_weight_field_map = {
                    'plurality': 'total_weighted_votes',
                    'borda': 'total_weighted_vote_power',
                    "dhondt": 'total_weighted_vote_power',
                    "d'hondt": 'total_weighted_vote_power',
                    "d’hondt": 'total_weighted_vote_power',
                    'approval': 'total_weighted_voting_power',
                    'runoff': 'total_weighted_ballot_power',
                }
                mechanism = results_summary.get('mechanism', '').lower()
                total_field = total_weight_field_map.get(mechanism, 'total_weighted_votes')
                total_weighted = results_summary.get(total_field, 0)

                if total_weighted == 0 and num_abstain_votes == 0:
                    final_status = "No Votes"
                elif total_weighted == 0 and num_abstain_votes > 0:
                    final_status = "Abstained"

            results_summary['final_status_derived'] = final_status

        return results_summary

    except Exception as e:
        print(f"CRITICAL ERROR calculating results for P#{proposal_id}: {e}")
        import traceback
        traceback.print_exc()
        return None


async def format_vote_results(results: Dict, proposal: Dict) -> discord.Embed:
    """Format vote results into a Discord embed with enhanced visuals"""
    proposal_id = proposal.get('proposal_id') or proposal.get(
        'id')  # Use .get() for safety
    print(
        f"DEBUG: Formatting results for proposal #{proposal_id}")
    mechanism = results.get('mechanism', 'Unknown').lower()
    color = discord.Color.light_grey()  # Default to grey

    embed = discord.Embed(
        title=f"📊 Results for Proposal #{proposal_id}",
        description=f"**{proposal.get('title', 'Untitled')}**\n\n{proposal.get('description', '')[:200]}...",
        color=color
    )

    # Add proposal metadata
    embed.add_field(name="Voting Mechanism", value=mechanism.title(), inline=True)
    embed.add_field(name="Abstain Votes", value=str(results.get('num_abstain_votes', 0)), inline=True)
    embed.add_field(name="Tokens in Abstain", value=str(results.get('tokens_in_abstain_votes', 0)), inline=True)
    embed.add_field(name="Options Used", value=", ".join(results.get('options_used_for_tally', [])), inline=False)

    # Add results
    if results.get('winner'):
        embed.add_field(name="Winner", value=results.get('winner'), inline=True)
    else:
        embed.add_field(name="No Winner", value="No option met criteria", inline=True)
    if results.get('reason_for_no_winner'):
        embed.add_field(name="Reason", value=results.get('reason_for_no_winner'), inline=True)

    # Add mechanism-specific details
    if mechanism == 'plurality':
        embed.add_field(name="Total Raw Votes", value=str(results.get('total_raw_votes', 0)), inline=True)
        embed.add_field(name="Winning Threshold", value=f"{results.get('winning_threshold_percentage', 'N/A')}% of weighted votes", inline=True)
        embed.add_field(name="Total Weighted Votes", value=str(results.get('total_weighted_votes', 0)), inline=True)
        embed.add_field(name="Vote Counts (Weighted)", value="\n".join([f"• {option}: {details['weighted_votes']:.2f} ({details['raw_votes']} raw)" for option, details in results['results_detailed']]), inline=False)
    elif mechanism == 'borda':
        embed.add_field(name="Total Raw Vote Sets", value=str(results.get('total_raw_vote_sets', 0)), inline=True)
        embed.add_field(name="Total Weighted Vote Power", value=str(results.get('total_weighted_vote_power', 0)), inline=True)
        embed.add_field(name="Borda Scores (Weighted)", value="\n".join([f"• {option}: {details['weighted_score']:.2f} ({details['raw_score']} raw)" for option, details in results['results_detailed']]), inline=False)
    elif mechanism == 'approval':
        embed.add_field(name="Total Raw Voters", value=str(results.get('total_raw_voters', 0)), inline=True)
        embed.add_field(name="Total Weighted Voting Power", value=str(results.get('total_weighted_voting_power', 0)), inline=True)
        embed.add_field(name="Approval Counts (Weighted)", value="\n".join([f"• {option}: {details['weighted_approvals']:.2f} ({details['raw_approvals']} raw)" for option, details in results['results_detailed']]), inline=False)
    elif mechanism == 'runoff':
        embed.add_field(name="Rounds Conducted", value=str(len(results.get('rounds_detailed', []))), inline=True)
        embed.add_field(name="Total Raw Ballots", value=str(results.get('total_raw_ballots', 0)), inline=True)
        embed.add_field(name="Total Weighted Ballot Power", value=str(results.get('total_weighted_ballot_power', 0)), inline=True)
        human_readable_rounds = []
        for round_index, round_info in enumerate(results.get('rounds_detailed', []), 1):
            option_lines = []
            for opt in round_info.get('active_options_in_round', []):
                w = round_info.get('weighted_votes_per_option', {}).get(opt, 0)
                r = round_info.get('raw_ballots_per_option', {}).get(opt, 0)
                option_lines.append(f"{opt}: {w:.2f} ({r} raw)")
            exhausted = round_info.get('exhausted_ballots_this_round', 0)
            round_text = "\n".join(option_lines)
            if exhausted:
                round_text += f"\nExhausted Ballots: {exhausted}"
            human_readable_rounds.append(f"**Round {round_index}**\n{round_text}")
        embed.add_field(name="Round Details", value="\n\n".join(human_readable_rounds) if human_readable_rounds else "No rounds", inline=False)
    elif mechanism == 'dhondt' or mechanism == "d'hondt":
        embed.add_field(name="Total Raw Ballots", value=str(results.get('total_raw_ballots', 0)), inline=True)
        embed.add_field(name="Total Weighted Ballot Power", value=str(results.get('total_weighted_ballot_power', 0)), inline=True)
        weighted = results.get('party_totals_weighted', {}) or {}
        raw = results.get('party_totals_raw', {}) or {}
        seats = results.get('seats_allocated', {}) or {}
        party_lines = []
        for party in sorted(set(list(weighted.keys()) + list(raw.keys()))):
            w = weighted.get(party, 0)
            r = raw.get(party, 0)
            s = seats.get(party)
            line = f"• {party}: {w:.2f} weighted ({r} raw)"
            if s is not None:
                line += f", {s} seats"
            party_lines.append(line)
        embed.add_field(name="Party Totals", value="\n".join(party_lines) if party_lines else "No data", inline=False)
    elif mechanism == 'condorcet':
        pairwise_matrix = results.get('pairwise_results', {}) or results.get('pairwise_matrix', {})
        pairwise_lines = []
        for option, opponents in pairwise_matrix.items():
            matchups = ", ".join([f"{opp}:{res}" for opp, res in opponents.items()])
            pairwise_lines.append(f"{option} -> {matchups}")
        pairwise_text = "\n".join(pairwise_lines) if pairwise_lines else "No pairwise data available"
        embed.add_field(name="Pairwise Matchups", value=pairwise_text, inline=False)
        if results.get('condorcet_winner'):
            embed.add_field(name="Condorcet Verdict", value=f"Winner: {results['condorcet_winner']}", inline=True)
        else:
            embed.add_field(name="Condorcet Verdict", value="No Condorcet winner (cycle detected)", inline=True)


    # Add footer
    embed.set_footer(text=f"Results for Proposal #{proposal_id} | Calculated at {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}")
    return embed


async def check_expired_proposals() -> List[Tuple[Dict, Dict]]:
    """Check for proposals with expired deadlines, close them, and return the list of (proposal, results) pairs."""
    try:
        # Get all active proposals with expired deadlines
        expired_proposals = await db.get_expired_proposals()

        closed_proposals = []
        for proposal in expired_proposals:
            try:
                print(
                    f"TASK: Closing expired proposal #{proposal['proposal_id']}: {proposal['title']}")

                # Close the proposal and calculate results
                guild_obj = None
                try:
                    import main
                    bot_instance = getattr(main, 'bot', None)
                    if bot_instance:
                        guild_obj = bot_instance.get_guild(proposal['server_id'])
                except Exception as e:
                    print(f"DEBUG: Could not fetch guild for proposal {proposal['proposal_id']}: {e}")
                results = await close_proposal(proposal['proposal_id'], guild_obj)

                if results:
                    # Add the proposal (with updated status) to the list for announcement
                    updated_proposal = await db.get_proposal(proposal['proposal_id'])
                    if updated_proposal:
                        closed_proposals.append((updated_proposal, results))
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

        # Return list of closed proposals with their results
        return closed_proposals

    except Exception as e:
        print(f"CRITICAL ERROR checking expired proposals: {str(e)}")
        import traceback
        traceback.print_exc()
        return []


async def close_proposal(proposal_id: int, guild: Optional[discord.Guild] = None) -> Optional[Dict]:
    """
    Close a proposal, calculate results, update status, and store results.
    Optionally provide the ``guild`` for campaign auto-progression.
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
            # Map "Failed" to "Closed" for DB storage
            await db.update_proposal_status(proposal_id, "Closed") # Changed "Failed" to "Closed"
            await db.add_proposal_note(proposal_id, "closure_error", f"Invalid mechanism: {mechanism_name}")
            return None

        # Calculate results using the main calculate_results function in this file
        results = await calculate_results(proposal_id)

        # Determine if proposal passed based on winner existence
        status_determined = "Passed" if results and results.get('winner') is not None else "Failed"
        print(f"DEBUG: Calculated status for proposal {proposal_id}: {status_determined}")

        # Map computed status to an allowed DB value
        db_status_to_set = "Closed"
        if status_determined == "Passed":
            db_status_to_set = "Closed"
        elif status_determined == "Failed":
            db_status_to_set = "Closed"
        else:
            db_status_to_set = status_determined

        allowed_statuses = [
            "Pending",
            "Voting",
            "Closed",
            "Cancelled",
            "Pending Approval",
            "Rejected",
            "ApprovedScenario",
        ]
        if db_status_to_set not in allowed_statuses:
            print(f"WARNING: Invalid status '{db_status_to_set}' derived from '{status_determined}', defaulting to 'Closed'")
            db_status_to_set = "Closed"

        # Update the proposal status in the database
        await db.update_proposal_status(proposal_id, db_status_to_set)
        print(f"DEBUG: Updated proposal {proposal_id} status to {db_status_to_set}")

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

        # NEW: Auto-progression logic for campaign scenarios
        if proposal.get('campaign_id'):
            campaign_id = proposal['campaign_id']
            guild_id = proposal.get('server_id')
            print(f"DEBUG: Checking for auto-progression in campaign C#{campaign_id} after P#{proposal_id} completed")

            guild_obj = guild
            bot_instance = None
            if guild_obj is None and guild_id:
                try:
                    import main
                    bot_instance = getattr(main, 'bot', None)
                    if bot_instance:
                        guild_obj = bot_instance.get_guild(guild_id)
                except Exception as e_fetch:
                    print(f"DEBUG: Unable to fetch guild {guild_id} for campaign {campaign_id}: {e_fetch}")
            if guild_obj:
                try:
                    campaign_proposals = await db.get_proposals_by_campaign_id(campaign_id, guild_id=guild_obj.id)
                    queued = [p['proposal_id'] for p in campaign_proposals if p['status'] == 'ApprovedScenario']
                    if queued and bot_instance:
                        try:
                            success_auto_start, msg = await initiate_campaign_stage_voting(
                                guild=guild_obj,
                                campaign_id=campaign_id,
                                scenario_proposal_ids=queued,
                                bot_instance=bot_instance
                            )
                            if success_auto_start:
                                print(f"DEBUG: Successfully auto-started queued scenarios in C#{campaign_id}: {msg}")
                            else:
                                print(f"DEBUG: Failed to auto-start queued scenarios in C#{campaign_id}: {msg}")
                        except Exception as e_auto:
                            print(f"ERROR: Failed to auto-start queued scenarios in C#{campaign_id}: {e_auto}")
                            import traceback; traceback.print_exc()
                except Exception as e_progression:
                    print(f"ERROR: Exception during auto-progression check for C#{campaign_id}: {e_progression}")
                    import traceback; traceback.print_exc()
            else:
                print(f"DEBUG: No guild context for auto-progression in campaign C#{campaign_id}; skipping.")

        return results

    except Exception as e:
        print(f"CRITICAL ERROR in close_proposal {proposal_id}: {str(e)}")
        import traceback
        traceback.print_exc()
        # Attempt to set status to Failed on critical error
        try:
            await db.update_proposal_status(proposal_id, "Closed") # Ensure it's a valid status
            await db.add_proposal_note(proposal_id, "closure_error", f"Critical error: {str(e)[:200]}")
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

    if mechanism in ["plurality"]:

        instructions += f"Format: `!vote <proposal_id> <option>`\nChoose *one* option.\nAvailable options: {options_text}"

    elif mechanism in ["borda", "runoff", "condorcet"]:
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


async def update_vote_tracking(guild: discord.Guild, proposal_id: int, final_proposal_state: Optional[Dict[str, Any]] = None):
    """Updates or creates a vote tracking message for a proposal in the voting channel."""
    print(f"DEBUG: update_vote_tracking called for proposal {proposal_id}. Status: {final_proposal_state['status'] if final_proposal_state else 'Fetching...'}")
    try:
        # Fetch proposal details (either passed in or fetched from DB)
        proposal = final_proposal_state or await db.get_proposal(proposal_id)
        if not proposal:
            print(f"ERROR: Proposal #{proposal_id} not found in update_vote_tracking.")
            return

        # Ensure hyperparameters is a dict (should be handled by db.get_proposal now)
        hyperparameters = proposal.get('hyperparameters', {})
        if not isinstance(hyperparameters, dict):
            print(f"WARNING: Hyperparameters for #{proposal_id} is {type(hyperparameters)}, not dict. Defaulting. Value: {hyperparameters}")
            hyperparameters = {}

        # Get the voting channel
        voting_channel_name = CHANNELS.get("voting", "voting-room")
        # Ensure bot's user ID is available for channel creation/permission setting
        bot_user_id = guild.me.id if guild.me else None
        voting_channel = await utils.get_or_create_channel(guild, voting_channel_name, bot_user_id=bot_user_id)
        if not voting_channel:
            print(f"ERROR: Could not find or create voting channel '{voting_channel_name}' for #{proposal_id}.")
            return

        # Fetch votes and calculate current standings
        votes = await db.get_proposal_votes(proposal_id)
        # Calculate results (simplified for tracking - real calculation is separate)
        # This is just for the embed display, not final tally.
        # The actual tallying function (e.g., calculate_plurality_results) is more complex.
        current_results_display = "No votes yet."
        if votes:
            # Basic count for display, actual result calculation is more complex
            options = await db.get_proposal_options(proposal_id)
            if not options: # Fallback if no options defined (e.g. simple Yes/No implied)
                options = ["Yes", "No"]

            vote_counts = {opt: 0 for opt in options}
            for vote_entry in votes:
                vote_data = vote_entry.get('vote_data') # vote_data is already a dict
                if isinstance(vote_data, dict) and 'option' in vote_data:
                    if vote_data['option'] in vote_counts:
                        vote_counts[vote_data['option']] += 1
                elif isinstance(vote_data, dict) and 'approved' in vote_data: # Approval
                    for approved_option in vote_data['approved']:
                        if approved_option in vote_counts:
                            vote_counts[approved_option] +=1
                # Add more complex parsing for Borda, Runoff if needed for simple tracking display

            current_results_display = "\n".join([f"- {opt}: {count}" for opt, count in vote_counts.items()])

        # Get total eligible voters (excluding bots)
        eligible_voters_count = 0
        # print(f"DEBUG: Guild members for vote tracking in {guild.name}: {[m.name for m in guild.members]}")
        for member in guild.members:
            if not member.bot:
                eligible_voters_count += 1
        print(f"DEBUG: Found {eligible_voters_count} eligible voters (excluding bots) in guild {guild.id}")

        # Calculate progress
        percentage_voted = (len(votes) / eligible_voters_count * 100) if eligible_voters_count > 0 else 0
        progress_bar = create_progress_bar(percentage_voted)

        # Create the embed
        embed = discord.Embed(
            title=f"🗳️ Live Vote Tracking: #{proposal_id} - {proposal['title']}",
            description=f"**Status:** {proposal['status']}\n**Voting Ends:** {format_deadline(proposal['deadline'])}",
            color=discord.Color.blue()
        )
        embed.add_field(name="Current Votes", value=current_results_display, inline=False)
        embed.add_field(name="Participation", value=f"{len(votes)} / {eligible_voters_count} voters ({percentage_voted:.2f}%)\n{progress_bar}", inline=False)
        embed.set_footer(text=f"Proposal ID: {proposal_id} | Last updated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S %Z')}")

        # Get existing tracking message ID from DB
        tracking_message_id = proposal.get('vote_tracking_message_id')

        if tracking_message_id:
            try:
                message = await voting_channel.fetch_message(tracking_message_id)
                await message.edit(embed=embed)
                # print(f"DEBUG: Edited tracking message for #{proposal_id} (ID: {tracking_message_id})")
            except discord.NotFound:
                print(f"WARN: Tracking message {tracking_message_id} for #{proposal_id} not found. Creating new one.")
                tracking_message_id = None # Force creation of new message
            except discord.Forbidden:
                print(f"ERROR: Bot lacks permissions to edit tracking message for #{proposal_id}.")
                return # Cannot proceed
            except Exception as e:
                print(f"ERROR editing tracking message for #{proposal_id}: {e}")
                # Potentially try to send a new one if edit fails for other reasons
                tracking_message_id = None

        if not tracking_message_id: # If no ID or fetching/editing failed
            try:
                print(f"Sending a new tracking message for proposal {proposal_id}.")
                new_message = await voting_channel.send(embed=embed)
                # Update database with the new message ID
                await db.update_proposal(proposal_id, {'vote_tracking_message_id': new_message.id})
                # print(f"DEBUG: Created new tracking message for #{proposal_id} (ID: {new_message.id}) and updated DB.")
            except discord.Forbidden:
                print(f"ERROR: Bot lacks permissions to send tracking message for #{proposal_id}.")
            except Exception as e:
                print(f"ERROR sending new tracking message for proposal {proposal_id}: {e}")
                # traceback.print_exc() # Add for more detail if needed

    except Exception as e:
        print(f"CRITICAL ERROR in update_vote_tracking for #{proposal_id}: {e}")
        traceback.print_exc()


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


# ==============================================
# 🔹 INITIATE VOTING FOR A PROPOSAL (NEW HELPER)
# ==============================================
async def initiate_voting_for_proposal(guild: discord.Guild, proposal_id: int, bot_instance: commands.Bot, proposal_details: Optional[Dict] = None) -> Tuple[bool, str]:
    """Sets a proposal to 'Voting' status, announces it, updates tracking, and sends DMs."""
    dm_info_message = ""
    try:
        if not proposal_details:
            proposal_details = await db.get_proposal(proposal_id)

        if not proposal_details:
            return False, f"Proposal #{proposal_id} not found for initiating voting."

        if proposal_details['status'] == "Voting":
            return True, f"Proposal #{proposal_id} is already in 'Voting' status."

        # 1. Update proposal status to 'Voting' in DB
        await db.update_proposal_status(proposal_id, "Voting")
        current_status_for_embed = "Voting" # For display purposes

        # 2. Announce in 'proposals' channel
        proposals_channel_name = utils.CHANNELS.get("proposals", "proposals")
        proposals_channel = await utils.get_or_create_channel(guild, proposals_channel_name, bot_instance.user.id)
        if proposals_channel:
            options = await db.get_proposal_options(proposal_id)
            option_names = options if options else ["Yes", "No"]
            proposer_member = guild.get_member(proposal_details['proposer_id']) or proposal_details['proposer_id']

            embed = utils.create_proposal_embed(
                proposal_id, proposer_member, proposal_details['title'], proposal_details['description'],
                proposal_details['voting_mechanism'], proposal_details['deadline'], current_status_for_embed, option_names,
                hyperparameters=proposal_details.get('hyperparameters'),
                campaign_id=proposal_details.get('campaign_id'),
                scenario_order=proposal_details.get('scenario_order')
            )
            await proposals_channel.send(content=f"🎉 Voting has started for Proposal #{proposal_id}!", embed=embed)
        else:
            print(f"Warning: Could not find '{proposals_channel_name}' channel to announce vote start for #{proposal_id}.")

        # 3. Update voting room (voting-room channel)
        voting_room_channel_name = utils.CHANNELS.get("voting-room", "voting-room")
        voting_room_channel = await utils.get_or_create_channel(guild, voting_room_channel_name, bot_instance.user.id)
        if voting_room_channel: # Check if channel exists before calling update_vote_tracking
            await update_vote_tracking(guild, proposal_id) # update_vote_tracking is in voting_utils.py
        else:
            print(f"Warning: Could not find '{voting_room_channel_name}' for #{proposal_id} to update vote tracking.")

        # 4. Send DMs to eligible voters
        # Re-fetch proposal to ensure status is now 'Voting' for DM logic
        refreshed_proposal = await db.get_proposal(proposal_id)
        if refreshed_proposal and refreshed_proposal['status'] == "Voting":
            eligible_voters_list = await get_eligible_voters(guild, refreshed_proposal) # get_eligible_voters is in voting_utils.py
            proposal_options_for_dm = await db.get_proposal_options(proposal_id)
            option_names_list_for_dm = proposal_options_for_dm if proposal_options_for_dm else ["Yes", "No"]

            # Dynamically import send_voting_dm here to avoid circular dependency at module load time
            # voting.py imports from voting_utils.py, so voting_utils.py should not import voting.py at top level.
            from voting import send_voting_dm

            if eligible_voters_list:
                successful_dms_count, failed_dms_count = 0, 0
                for member_to_dm in eligible_voters_list:
                    if member_to_dm.bot: continue
                    dm_sent = await send_voting_dm(member_to_dm, refreshed_proposal, option_names_list_for_dm)
                    if dm_sent:
                        successful_dms_count += 1
                        await db.add_voting_invite(proposal_id, member_to_dm.id)
                    else:
                        failed_dms_count += 1
                dm_info_message = f" ({successful_dms_count} DMs sent, {failed_dms_count} failed)"
            else:
                dm_info_message = " (No eligible voters found for DMs)"
        else:
            dm_info_message = " (DM sending skipped as proposal not confirmed in 'Voting' status after update)"

        print(f"INFO: Initiated voting for #{proposal_id}. Status: Voting. {dm_info_message}")
        return True, f"Voting started for #{proposal_id}.{dm_info_message}"

    except Exception as e:
        print(f"ERROR in initiate_voting_for_proposal for #{proposal_id}: {e}")
        traceback.print_exc()
        return False, f"An error occurred while initiating voting for #{proposal_id}: {e}"


# =====================================================
# 🔹 INITIATE VOTING FOR A CAMPAIGN STAGE (MULTIPLE SCENARIOS)
# =====================================================
async def initiate_campaign_stage_voting(guild: discord.Guild, campaign_id: int, scenario_proposal_ids: List[int], bot_instance: commands.Bot) -> Tuple[bool, str]:
    """Initiates voting for a batch of campaign scenarios: sets status, enrolls voters, and triggers batched DMs."""
    if not scenario_proposal_ids:
        return False, "No scenario proposal IDs provided for campaign stage voting."

    print(f"DEBUG: Initiating campaign stage voting for C#{campaign_id}, Scenarios: {scenario_proposal_ids}")

    campaign_details = await db.get_campaign(campaign_id)
    if not campaign_details:
        return False, f"Campaign C#{campaign_id} not found."

    # --- Ensure all eligible guild members are enrolled in the campaign ---
    eligible_members = [m for m in guild.members if not m.bot]
    if not eligible_members:
        print(f"WARN: No eligible (non-bot) members found in guild {guild.name} for C#{campaign_id} enrollment.")
    else:
        print(f"INFO: Attempting to enroll/verify {len(eligible_members)} eligible members in C#{campaign_id}...")
        enrolled_count = 0
        already_enrolled_count = 0
        enroll_failed_count = 0
        for member in eligible_members:
            # enroll_voter_in_campaign returns True if newly enrolled, False if already exists or error.
            # We need to know the total_tokens_per_voter for the campaign.
            # It's idempotent, so safe to call even if already enrolled.
            success_enroll = await db.enroll_voter_in_campaign(
                campaign_id,
                member.id,
                campaign_details['total_tokens_per_voter']
            )
            # This function's return isn't granular enough to distinguish between already enrolled and failed.
            # We'll assume for now it succeeds or user is already there.
            # To improve, enroll_voter_in_campaign could return more specific status.
            # For logging, let's assume if not new, they were already there or it failed silently (which it shouldn't).
            # The important part is that an attempt was made for everyone.
        print(f"INFO: Enrollment process completed for {len(eligible_members)} members for C#{campaign_id}.")
    # --- End of enrollment section ---

    updated_scenario_count = 0
    failed_to_update_count = 0

    for proposal_id in scenario_proposal_ids:
        proposal = await db.get_proposal(proposal_id)
        if not proposal or proposal.get('campaign_id') != campaign_id:
            print(f"WARN: P#{proposal_id} not found or not part of C#{campaign_id}. Skipping status update.")
            failed_to_update_count +=1
            continue

        if proposal.get('status') == 'ApprovedScenario':
            await db.update_proposal_status(proposal_id, 'Voting')
            # Announce that this specific scenario is now voting
            proposal_channel_name = CHANNELS.get("proposals", "proposals")
            proposal_channel = discord.utils.get(guild.text_channels, name=proposal_channel_name)
            if proposal_channel:
                try:
                    options = await db.get_proposal_options(proposal_id)
                    if not options: options = ["Yes", "No"] # Fallback
                    # Use the utility to create the embed
                    embed = utils.create_proposal_embed(
                        proposal_id=proposal_id,
                        proposer=guild.get_member(proposal['proposer_id']) if proposal.get('proposer_id') else "Unknown Proposer",
                        title=proposal['title'],
                        description=proposal['description'],
                        voting_mechanism=proposal['voting_mechanism'],
                        deadline=proposal['deadline'],
                        status='Voting', # Explicitly set status for embed
                        options=options,
                        hyperparameters=proposal.get('hyperparameters'),
                        campaign_id=campaign_id,
                        campaign_title=campaign_details.get('title'),
                        scenario_order=proposal.get('scenario_order')
                    )
                    await proposal_channel.send(f"📢 Voting has started for Campaign Scenario S#{proposal.get('scenario_order')} (P#{proposal_id}) of Campaign C#{campaign_id}!", embed=embed)
                    print(f"DEBUG: Announced start of voting for P#{proposal_id} (C#{campaign_id} S#{proposal.get('scenario_order')}) in #{proposal_channel_name}.")

                    # Initiate vote tracking for this specific proposal
                    await update_vote_tracking(guild, proposal_id)

                except Exception as e_announce:
                    print(f"ERROR announcing start of voting for P#{proposal_id} in C#{campaign_id}: {e_announce}")
                    traceback.print_exc()
            else:
                print(f"WARN: Proposal channel #{proposal_channel_name} not found for announcement of P#{proposal_id}.")
            updated_scenario_count += 1
        else:
            print(f"WARN: P#{proposal_id} in C#{campaign_id} was not in 'ApprovedScenario' status (was {proposal.get('status')}). Not changing to 'Voting'.")
            failed_to_update_count +=1

    # After updating all relevant scenarios to 'Voting', trigger batched DM sending
    # send_batched_campaign_dms will fetch enrolled voters for this campaign.
    dms_sent_successfully, dm_batch_message = await send_batched_campaign_dms(guild, campaign_id, scenario_proposal_ids, bot_instance)

    overall_success = updated_scenario_count > 0
    status_message = f"Campaign C#{campaign_id} stage processing: {updated_scenario_count} scenarios initiated, {failed_to_update_count} failed. {dm_batch_message}"

    return overall_success, status_message


async def send_batched_campaign_dms(guild: discord.Guild, campaign_id: int, scenario_proposal_ids: List[int], bot_instance: commands.Bot) -> Tuple[bool, str]:
    """Sends batched DMs to enrolled voters for active campaign scenarios."""
    print(f"DEBUG: send_batched_campaign_dms called for C#{campaign_id}, Scenarios: {scenario_proposal_ids}")
    try:
        if not scenario_proposal_ids:
            return False, "No scenario proposal IDs provided for DM batching."

        campaign_details = await db.get_campaign(campaign_id)
        if not campaign_details:
            return False, f"Campaign C#{campaign_id} not found."

        # Get all proposal details for the current stage
        stage_proposals_details = []
        for p_id in scenario_proposal_ids:
            detail = await db.get_proposal(p_id)
            if detail and detail['campaign_id'] == campaign_id and detail['status'] == 'Voting':
                 options = await db.get_proposal_options(p_id)
                 detail['options_list'] = options if options else ["Yes", "No"]
                 stage_proposals_details.append(detail)
            else:
                print(f"WARN: send_batched_campaign_dms: Scenario P#{p_id} not suitable for DM (not found, wrong campaign, or not Voting). Skipping.")

        if not stage_proposals_details:
            return False, "No suitable (Voting status) scenarios found in the list for DM batching."

        # Get eligible voters for the campaign.
        # get_eligible_voters takes a proposal, but campaign eligibility might be broader or stored differently.
        # For now, let's assume get_eligible_voters with the first scenario gives us the campaign members.
        # This might need refinement if campaign enrollment is separate from general server eligibility.
        # A new db function like `db.get_campaign_enrolled_voters(campaign_id)` would be better.
        # Using a placeholder:
        # eligible_voters = await db.get_campaign_enrolled_user_ids(campaign_id) # Returns list of user_ids
        # For now, let's use get_eligible_voters with the first proposal in the batch as a proxy.
        # This assumes all scenarios in a campaign stage have the same eligibility criteria,
        # and that `get_eligible_voters` can correctly interpret campaign context if needed.

        # Placeholder: Ideally, get campaign-specific voters.
        # For now, using get_eligible_voters for the guild and assuming they are campaign voters if enrolled for tokens.
        # This part needs careful review of how campaign voter eligibility is determined.

        # Let's assume we have a way to get campaign voters:
        campaign_voter_members: List[discord.Member] = []
        enrolled_user_ids = await db.get_enrolled_voter_ids_for_campaign(campaign_id)
        for user_id in enrolled_user_ids:
            member = guild.get_member(user_id)
            if member and not member.bot:
                campaign_voter_members.append(member)

        if not campaign_voter_members:
            return True, "No enrolled (non-bot) voters found for Campaign C#{campaign_id} to send DMs to."

        # Dynamically import the DM sender from voting.py to avoid circular import at load time
        try:
            from voting import send_campaign_scenario_dms_to_user
        except ImportError:
            print("CRITICAL: Could not import send_campaign_scenario_dms_to_user from voting.py")
            return False, "Internal error: DM sending function not found."

        total_dms_attempted_users = 0
        total_dms_successful_users = 0

        for member_to_dm in campaign_voter_members:
            total_dms_attempted_users += 1
            try:
                # Fetch this user's current token balance for THIS campaign
                user_remaining_tokens = await db.get_user_remaining_tokens(campaign_id, member_to_dm.id)
                if user_remaining_tokens is None: # Should be enrolled by now
                    user_remaining_tokens = campaign_details.get('total_tokens_per_voter', 0)
                    print(f"WARN: User {member_to_dm.id} in C#{campaign_id} had no token record. Defaulting to campaign total: {user_remaining_tokens}")

                # Prepare list of (proposal_dict, options_list, campaign_id, user_current_tokens_for_campaign)
                # The user_current_tokens_for_campaign will be the same for all DMs in this batch for this user.
                scenario_data_for_this_user_batch = []
                for prop_detail in stage_proposals_details:
                    scenario_data_for_this_user_batch.append({
                        'proposal_dict': prop_detail, # Contains title, desc, id, mechanism, deadline, etc.
                        'options_list': prop_detail['options_list'],
                        'campaign_id': campaign_id,
                        'campaign_title': campaign_details.get('title', 'N/A'),
                        'user_initial_tokens_for_dm_batch': user_remaining_tokens
                    })

                if scenario_data_for_this_user_batch:
                    # Call the function in voting.py to send the batch of DMs
                    # This function in voting.py will loop and send individual DMs
                    dms_sent_for_user_successfully = await send_campaign_scenario_dms_to_user(member_to_dm, scenario_data_for_this_user_batch)
                    if dms_sent_for_user_successfully:
                        total_dms_successful_users += 1
                        # Record that invites were sent for these proposals to this user
                        for p_data in scenario_data_for_this_user_batch:
                            await db.add_voting_invite(p_data['proposal_dict']['proposal_id'], member_to_dm.id) # Mark invite sent
            except Exception as e_user_dm:
                print(f"ERROR sending batched DMs to user {member_to_dm.name} (ID: {member_to_dm.id}) for C#{campaign_id}: {e_user_dm}")
                traceback.print_exc()

        return True, f"Batched DMs for C#{campaign_id}: Attempted for {total_dms_attempted_users} users, successful for {total_dms_successful_users}."

    except Exception as e:
        print(f"CRITICAL ERROR in send_batched_campaign_dms for C#{campaign_id}: {e}")
        traceback.print_exc()
        return False, f"Internal error during batched DM preparation for C#{campaign_id}."

# Modify existing initiate_voting_for_proposal slightly if it might be called for a campaign scenario directly
# For now, assume it's for single proposals or the CampaignControlView will call the new batch function.

# Helper to get campaign voters (example, ideally from db.py)
# async def get_campaign_enrolled_voters(guild: discord.Guild, campaign_id: int) -> List[discord.Member]:
#     enrolled_user_ids = await db.get_enrolled_voter_ids_for_campaign(campaign_id)
#     members = []
#     for user_id in enrolled_user_ids:
#         member = guild.get_member(user_id)
#         if member and not member.bot: # Ensure member is in guild and not a bot
#             members.append(member)
#     return members

# Ensure other functions like format_vote_results, check_expired_proposals, etc. are below this


async def _calculate_campaign_token_stats(campaign_id: int) -> Dict[str, Any]:
    """Compute token distribution stats for a completed campaign."""
    stats = {
        "total_allocated_tokens": 0,
        "total_invested_tokens": 0,
        "unused_tokens": 0,
        "num_enrolled_voters": 0,
        "num_participants": 0,
    }

    campaign = await db.get_campaign(campaign_id)
    if not campaign:
        return stats

    total_tokens_per_voter = campaign.get("total_tokens_per_voter", 0)
    enrolled_user_ids = await db.get_enrolled_voter_ids_for_campaign(campaign_id)
    stats["num_enrolled_voters"] = len(enrolled_user_ids)
    stats["total_allocated_tokens"] = total_tokens_per_voter * stats["num_enrolled_voters"]

    # Gather all votes across scenarios in this campaign
    proposals = await db.get_proposals_by_campaign_id(campaign_id)
    participant_ids = set()
    for proposal in proposals:
        votes = await db.get_proposal_votes(proposal["proposal_id"])
        for vote in votes:
            tokens = vote.get("tokens_invested") or 0
            stats["total_invested_tokens"] += tokens
            participant_ids.add(vote.get("user_id"))

    stats["num_participants"] = len(participant_ids)
    stats["unused_tokens"] = max(stats["total_allocated_tokens"] - stats["total_invested_tokens"], 0)
    return stats


async def _generate_campaign_completion_summary(campaign: Dict[str, Any], scenarios: List[Dict[str, Any]]) -> discord.Embed:
    """Build a detailed embed summarizing campaign outcomes and token stats."""
    campaign_id = campaign.get("campaign_id")
    token_stats = await _calculate_campaign_token_stats(campaign_id)

    embed = discord.Embed(
        title=f"Campaign Completed: '{campaign.get('title', 'Untitled')}' (C#{campaign_id})",
        description=f"All {len(scenarios)} scenario(s) have concluded.",
        color=discord.Color.gold(),
        timestamp=datetime.now(timezone.utc)
    )

    # Scenario outcomes
    scenario_lines = []
    sorted_scenarios = sorted(scenarios, key=lambda s: s.get("scenario_order", 0))
    for sc in sorted_scenarios:
        result = await db.get_proposal_results(sc["proposal_id"])
        winner = result.get("winner") if result else None
        line = f"S#{sc.get('scenario_order')} P#{sc['proposal_id']} - {sc.get('status')}"
        if winner:
            line += f" → {winner}"
        scenario_lines.append(line)
    if scenario_lines:
        embed.add_field(name="Scenario Outcomes", value="\n".join(scenario_lines), inline=False)

    # Token statistics
    token_lines = [
        f"Allocated: {token_stats['total_allocated_tokens']}",
        f"Invested: {token_stats['total_invested_tokens']}",
        f"Unspent: {token_stats['unused_tokens']}",
        f"Enrolled Voters: {token_stats['num_enrolled_voters']}",
        f"Participants: {token_stats['num_participants']}"
    ]
    embed.add_field(name="Token Stats", value="\n".join(token_lines), inline=False)

    return embed


async def _complete_campaign_automatically(campaign_id: int, bot_instance: commands.Bot) -> Tuple[bool, str]:
    """Finalize a campaign when all scenarios are closed and post a summary."""
    try:
        campaign = await db.get_campaign(campaign_id)
        if not campaign:
            return False, f"Campaign C#{campaign_id} not found."

        guild = bot_instance.get_guild(campaign["guild_id"])
        if not guild:
            return False, f"Guild {campaign['guild_id']} not found."

        scenarios = await db.get_proposals_by_campaign_id(campaign_id, campaign.get("guild_id"))
        incomplete = [s for s in scenarios if s.get("status") not in ["Closed", "Passed", "Failed"]]
        if incomplete:
            return False, f"Campaign C#{campaign_id} has active scenarios."

        await db.update_campaign_status(campaign_id, "completed")

        summary_embed = await _generate_campaign_completion_summary(campaign, scenarios)

        results_channel = discord.utils.get(
            guild.text_channels, name=CHANNELS.get("results", "governance-results")
        )
        if results_channel:
            await results_channel.send(embed=summary_embed)

        voting_channel = discord.utils.get(
            guild.text_channels, name=CHANNELS.get("voting_room", "voting-room")
        )
        if voting_channel and (not results_channel or voting_channel.id != results_channel.id):
            await voting_channel.send(
                f"🏁 **Campaign Completed:** '{campaign.get('title')}' (C#{campaign_id})",
                embed=summary_embed,
            )

        audit_channel = discord.utils.get(guild.text_channels, name=CHANNELS.get("audit", "audit-log"))
        if audit_channel:
            await audit_channel.send(
                f"🏁 **Campaign Completed**: C#{campaign_id} ('{campaign.get('title', 'Untitled')}') has concluded."
            )

        try:
            from proposals import _update_campaign_control_panel

            await _update_campaign_control_panel(campaign_id, bot_instance)
        except Exception as e:
            print(f"ERROR: Unable to update campaign control panel for C#{campaign_id}: {e}")

        return True, f"Campaign C#{campaign_id} marked completed."
    except Exception as e:
        print(f"ERROR auto-completing campaign C#{campaign_id}: {e}")
        traceback.print_exc()
        return False, f"Failed to complete campaign C#{campaign_id}."

