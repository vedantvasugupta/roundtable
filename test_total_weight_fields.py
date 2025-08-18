import os
import sys
import asyncio
from unittest.mock import AsyncMock, patch

# Ensure bundled dependencies like discord are available
sys.path.append(os.path.join(os.path.dirname(__file__), 'Lib', 'site-packages'))

import voting_utils

class DummyMechanism:
    def __init__(self, mechanism_name, field_name, value):
        self.mechanism_name = mechanism_name
        self.field_name = field_name
        self.value = value

    def count_votes(self, votes, options, hyperparameters=None):
        return {
            'mechanism': self.mechanism_name,
            self.field_name: self.value,
            'winner': None,
            'reason_for_no_winner': None,
        }

def run_calc(mechanism_name, field_name, value):
    async def runner():
        dummy = DummyMechanism(mechanism_name, field_name, value)
        with patch('voting_utils.get_voting_mechanism', return_value=dummy), \
             patch('voting_utils.db.get_proposal', new=AsyncMock(return_value={'proposal_id': 1, 'voting_mechanism': mechanism_name, 'description': '', 'hyperparameters': {}})), \
             patch('voting_utils.db.get_proposal_votes', new=AsyncMock(return_value=[])), \
             patch('voting_utils.db.get_proposal_options', new=AsyncMock(return_value=['A', 'B'])):
            return await voting_utils.calculate_results(1)
    return asyncio.run(runner())

# Helper to test mapping for a mechanism. Ensures that the correct
# total-weight field is inspected based on the mechanism name.
def check_mechanism(mech_name, field_name):
    # When weight > 0, final status should remain Unknown (not No Votes)
    result = run_calc(mech_name, field_name, 5)
    assert result['final_status_derived'] == 'Unknown'
    # When weight == 0, final status should be No Votes
    result_zero = run_calc(mech_name, field_name, 0)
    assert result_zero['final_status_derived'] == 'No Votes'

def test_plurality_mapping():
    check_mechanism('plurality', 'total_weighted_votes')

def test_borda_mapping():
    check_mechanism('borda', 'total_weighted_vote_power')

def test_dhondt_mapping():
    check_mechanism("d'hondt", 'total_weighted_vote_power')

def test_approval_mapping():
    check_mechanism('approval', 'total_weighted_voting_power')

def test_runoff_mapping():
    check_mechanism('runoff', 'total_weighted_ballot_power')
