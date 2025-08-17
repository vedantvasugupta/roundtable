import json
import types
import sys

# Stub out heavy dependencies from voting_utils
db_stub = types.ModuleType("db")
db_stub.get_proposal_options = lambda *args, **kwargs: []
sys.modules.setdefault("db", db_stub)

# Minimal stub for discord module used during imports
class _DiscordStub(types.ModuleType):
    def __getattr__(self, name):
        attr = type(name, (), {})
        setattr(self, name, attr)
        return attr

discord_stub = _DiscordStub("discord")

class _CommandsStub(types.ModuleType):
    def __getattr__(self, name):
        attr = type(name, (), {})
        setattr(self, name, attr)
        return attr

discord_ext_stub = types.ModuleType("discord.ext")
discord_commands_stub = _CommandsStub("discord.ext.commands")
discord_stub.ext = discord_ext_stub
discord_ext_stub.commands = discord_commands_stub

sys.modules.setdefault("discord", discord_stub)
sys.modules.setdefault("discord.ext", discord_ext_stub)
sys.modules.setdefault("discord.ext.commands", discord_commands_stub)

import voting_utils

def test_plurality_zero_token_weight():
    votes = [
        {'vote_data': json.dumps({'option': 'A'}), 'tokens_invested': 0},
        {'vote_data': json.dumps({'option': 'A'}), 'tokens_invested': 5},
        {'vote_data': json.dumps({'option': 'B'}), 'tokens_invested': None},
    ]
    options = ['A', 'B']
    result = voting_utils.PluralityVoting.count_votes(votes, options)
    results = {opt: details for opt, details in result['results_detailed']}
    assert results['A']['weighted_votes'] == 5
    assert results['B']['weighted_votes'] == 1
    assert result['total_weighted_votes'] == 6

def test_borda_zero_token_weight():
    votes = [
        {'vote_data': json.dumps({'rankings': ['A', 'B']}), 'tokens_invested': 0},
        {'vote_data': json.dumps({'rankings': ['B', 'A']}), 'tokens_invested': None},
        {'vote_data': json.dumps({'rankings': ['A', 'B']}), 'tokens_invested': 2},
    ]
    options = ['A', 'B']
    result = voting_utils.BordaCount.count_votes(votes, options)
    results = {opt: details for opt, details in result['results_detailed']}
    assert results['A']['weighted_score'] == 2
    assert results['B']['weighted_score'] == 1
    assert result['total_weighted_vote_power'] == 3

def test_approval_zero_token_weight():
    votes = [
        {'vote_data': json.dumps({'approved': ['A']}), 'tokens_invested': 0},
        {'vote_data': json.dumps({'approved': ['B']}), 'tokens_invested': None},
        {'vote_data': json.dumps({'approved': ['A', 'B']}), 'tokens_invested': 3},
    ]
    options = ['A', 'B']
    result = voting_utils.ApprovalVoting.count_votes(votes, options)
    results = {opt: details for opt, details in result['results_detailed']}
    assert results['A']['weighted_approvals'] == 3
    assert results['B']['weighted_approvals'] == 4
    assert result['total_weighted_voting_power'] == 4

def test_runoff_zero_token_weight():
    votes = [
        {'vote_data': json.dumps({'rankings': ['A', 'B']}), 'tokens_invested': 0},
        {'vote_data': json.dumps({'rankings': ['B', 'A']}), 'tokens_invested': None},
        {'vote_data': json.dumps({'rankings': ['A', 'B']}), 'tokens_invested': 2},
    ]
    options = ['A', 'B']
    result = voting_utils.RunoffVoting.count_votes(votes, options)
    assert result['winner'] == 'A'
    first_round = result['rounds_detailed'][0]
    assert first_round['weighted_votes_per_option']['A'] == 2
    assert first_round['weighted_votes_per_option']['B'] == 1
    assert result['total_weighted_ballot_power'] == 3
