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
            return None
            
        votes = await db.get_proposal_votes(proposal_id)
        
        mechanism = get_voting_mechanism(proposal['voting_mechanism'])
        if not mechanism:
            return None
            
        results = await mechanism.tally_votes(votes)
        if results:
            new_status = 'Passed' if votes and results.get('winner') else 'Failed'
            await db.update_proposal_status(proposal_id, new_status)
            
            results_json = json.dumps(results)
            await db.store_proposal_results(proposal_id, results_json)
            
        return results
    except Exception as e:
        print(f"Error in close_proposal: {e}")
        return None
