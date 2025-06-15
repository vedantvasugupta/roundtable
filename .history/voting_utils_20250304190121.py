import discord
import asyncio
from datetime import datetime
import db
import json
from typing import List, Dict, Any, Optional, Union, Tuple

# ========================
# 🔹 VOTING MECHANISMS
# ========================

class PluralityVoting:
    """Simple plurality (first past the post) voting system"""
    
    @staticmethod
    def count_votes(votes):
        """Count votes and return results"""
        results = {}
        for vote in votes:
            option = vote['vote_data'].get('option')
            if option:
                results[option] = results.get(option, 0) + 1
        
        # Convert to sorted list of tuples
        sorted_results = sorted(results.items(), key=lambda x: x[1], reverse=True)
        
        # Determine winner
        winner = sorted_results[0][0] if sorted_results else None
        
        return {
            'mechanism': 'plurality',
            'results': sorted_results,
            'winner': winner,
            'details': f"Winner: {winner}" if winner else "No votes cast"
        }
    
    @staticmethod
    def get_description():
        return "Each voter selects one option. The option with the most votes wins."
    
    @staticmethod
    def get_vote_instructions():
        return "Vote by typing `!vote <proposal_id> <option>` where `option` is your chosen option."


class BordaCount:
    """Borda count voting system - gives points based on rankings"""
    
    @staticmethod
    def count_votes(votes):
        """Count ranked votes using Borda count"""
        # Get all options from rankings
        all_options = set()
        for vote in votes:
            rankings = vote['vote_data'].get('rankings', [])
            all_options.update(rankings)
        
        # Initialize points for all options
        points = {option: 0 for option in all_options}
        
        # Calculate Borda points
        for vote in votes:
            rankings = vote['vote_data'].get('rankings', [])
            for i, option in enumerate(rankings):
                # Points are (n-i) where n is the number of options and i is the rank (0-indexed)
                points[option] += len(all_options) - i
        
        # Convert to sorted list of tuples
        sorted_results = sorted(points.items(), key=lambda x: x[1], reverse=True)
        
        # Determine winner
        winner = sorted_results[0][0] if sorted_results else None
        
        return {
            'mechanism': 'borda',
            'results': sorted_results,
            'winner': winner,
            'details': f"Winner: {winner} with {sorted_results[0][1]} points" if winner else "No votes cast"
        }
    
    @staticmethod
    def get_description():
        return "Voters rank options in order of preference. Points are assigned based on rankings, and the option with the most points wins."
    
    @staticmethod
    def get_vote_instructions():
        return "Vote by typing `!vote <proposal_id> rank option1,option2,...` where options are listed in your order of preference."


class ApprovalVoting:
    """Approval voting system"""
    
    @staticmethod
    def count_votes(votes):
        """Count approval votes"""
        results = {}
        for vote in votes:
            approved = vote['vote_data'].get('approved', [])
            for option in approved:
                results[option] = results.get(option, 0) + 1
        
        # Convert to sorted list of tuples
        sorted_results = sorted(results.items(), key=lambda x: x[1], reverse=True)
        
        # Determine winner
        winner = sorted_results[0][0] if sorted_results else None
        
        return {
            'mechanism': 'approval',
            'results': sorted_results,
            'winner': winner,
            'details': f"Winner: {winner} with {sorted_results[0][1]} approvals" if winner else "No votes cast"
        }
    
    @staticmethod
    def get_description():
        return "Voters can approve of any number of options. The option with the most approvals wins."
    
    @staticmethod
    def get_vote_instructions():
        return "Vote by typing `!vote <proposal_id> approve option1,option2,...` where options are all the choices you approve of."


class RunoffVoting:
    """Instant runoff voting system"""
    
    @staticmethod
    def count_votes(votes):
        """Count votes using instant runoff"""
        # Get all options from rankings
        all_options = set()
        for vote in votes:
            rankings = vote['vote_data'].get('rankings', [])
            all_options.update(rankings)
        
        # If no votes or options, return empty results
        if not votes or not all_options:
            return {
                'mechanism': 'runoff',
                'results': [],
                'winner': None,
                'details': "No votes cast"
            }
        
        # Convert all_options to a list for consistency
        all_options = list(all_options)
        
        # Initialize counts
        round_results = []
        eliminated = []
        winner = None
        
        # Process rounds until a winner is found
        while winner is None and len(eliminated) < len(all_options):
            # Count first choices for each vote
            counts = {option: 0 for option in all_options if option not in eliminated}
            for vote in votes:
                rankings = vote['vote_data'].get('rankings', [])
                # Find first choice that hasn't been eliminated
                for option in rankings:
                    if option not in eliminated:
                        counts[option] += 1
                        break
            
            # Convert to list and sort
            round_counts = sorted(counts.items(), key=lambda x: x[1], reverse=True)
            
            # Record this round's results
            round_results.append({
                'round': len(round_results) + 1,
                'counts': round_counts
            })
            
            # Check for majority
            total_votes = sum(counts.values())
            threshold = total_votes / 2
            
            if round_counts and round_counts[0][1] > threshold:
                # Majority found
                winner = round_counts[0][0]
            elif round_counts:
                # No majority, eliminate last place
                min_votes = min(counts.values())
                to_eliminate = [option for option, count in counts.items() if count == min_votes]
                eliminated.extend(to_eliminate)
        
        # If no winner found after eliminating all but one, the remaining option is the winner
        if winner is None and len(eliminated) == len(all_options) - 1:
            for option in all_options:
                if option not in eliminated:
                    winner = option
                    break
        
        return {
            'mechanism': 'runoff',
            'results': round_results,
            'winner': winner,
            'rounds': len(round_results),
            'details': f"Winner after {len(round_results)} rounds: {winner}" if winner else "No clear winner"
        }
    
    @staticmethod
    def get_description():
        return "Voters rank options in order of preference. If no option has a majority, the option with the fewest first-choice votes is eliminated, and those votes are redistributed based on the next choices."
    