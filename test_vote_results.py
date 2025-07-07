"""
Test script for verifying vote results announcements in a low-user environment.

This script tests the proposal creation, voting, and result announcement process,
with a focus on ensuring results are properly announced in a small server.
"""

import asyncio
import unittest
from unittest.mock import MagicMock, patch
import json
import os
import sys
from datetime import datetime, timedelta
import sqlite3

# Add the current directory to the path so we can import our modules
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Import our modules
import db
import voting_utils
import proposals

class TestVoteResults(unittest.TestCase):
    """Test cases for vote results announcements"""
    
    def setUp(self):
        """Set up test environment"""
        # Create mock objects
        self.bot = MagicMock()
        self.guild = MagicMock()
        self.channel = MagicMock()
        self.author = MagicMock()
        self.member1 = MagicMock()
        self.member2 = MagicMock()
        
        # Set up mock relationships
        self.guild.text_channels = []
        self.guild.members = [self.member1, self.member2]
        
        # Set up IDs
        self.guild.id = 123456789
        self.member1.id = 111222333
        self.member2.id = 444555666
        
        # Set up names
        self.guild.name = "Test Server"
        self.member1.name = "Test Member 1"
        self.member2.name = "Test Member 2"
        
        # Set up mentions
        self.member1.mention = "<@111222333>"
        self.member2.mention = "<@444555666>"
        
        # Mock channels
        self.results_channel = MagicMock()
        self.results_channel.name = "governance-results"
        self.results_channel.id = 777888999
        self.results_channel.mention = "<#777888999>"
        
        self.proposals_channel = MagicMock()
        self.proposals_channel.name = "proposals"
        
        # Add channels to guild
        self.guild.text_channels = [self.results_channel, self.proposals_channel]
        
        # Set up mock discord.utils.get
        def mock_get(iterable, **attrs):
            for item in iterable:
                match = True
                for attr, value in attrs.items():
                    if getattr(item, attr) != value:
                        match = False
                        break
                if match:
                    return item
            return None
        
        # Apply the mock
        discord_utils_patcher = patch('discord.utils.get', mock_get)
        discord_utils_patcher.start()
        self.addCleanup(discord_utils_patcher.stop)
        
        # Set up database
        asyncio.run(self.setup_database())
    
    async def setup_database(self):
        """Set up test database"""
        # Initialize database
        await db.init_db()
        
        # Add server
        await db.add_server(self.guild.id, self.guild.name, self.member1.id, 2)
        
        # Initialize constitutional variables
        await db.init_constitutional_variables(self.guild.id)
    
    async def create_test_proposal(self):
        """Create a test proposal"""
        # Create proposal
        title = "Test Proposal"
        description = "This is a test proposal description."
        voting_mechanism = "plurality"
        options = ["Option A", "Option B"]
        deadline = datetime.now() + timedelta(minutes=5)
        
        # Insert into database
        proposal_id = await db.create_proposal(
            self.guild.id, self.member1.id, title, description, 
            voting_mechanism, deadline, False
        )
        
        # Update status to Voting
        await db.update_proposal_status(proposal_id, "Voting")
        
        return proposal_id
    
    async def add_test_votes(self, proposal_id):
        """Add test votes to a proposal"""
        # Vote from member 1
        vote_data1 = {"option": "Option A"}
        await db.add_vote(proposal_id, self.member1.id, vote_data1)
        
        # Vote from member 2
        vote_data2 = {"option": "Option B"}
        await db.add_vote(proposal_id, self.member2.id, vote_data2)
    
    def test_close_proposal(self):
        """Test the close_proposal function"""
        # Create a proposal
        proposal_id = asyncio.run(self.create_test_proposal())
        
        # Add votes
        asyncio.run(self.add_test_votes(proposal_id))
        
        # Close the proposal
        results = asyncio.run(voting_utils.close_proposal(proposal_id))
        
        # Check that results were generated
        self.assertIsNotNone(results)
        self.assertEqual(results['mechanism'], 'plurality')
        
        # Check that votes were counted
        self.assertEqual(len(results['results']), 2)
        self.assertIn(('Option A', 1), results['results'])
        self.assertIn(('Option B', 1), results['results'])
        
        # Get the proposal to check status
        proposal = asyncio.run(db.get_proposal(proposal_id))
        self.assertEqual(proposal['status'], 'Closed')

    def test_close_proposal_integrity(self):
        """Ensure closing a proposal does not raise IntegrityError."""
        proposal_id = asyncio.run(self.create_test_proposal())
        asyncio.run(self.add_test_votes(proposal_id))
        try:
            asyncio.run(voting_utils.close_proposal(proposal_id))
        except sqlite3.IntegrityError as e:
            self.fail(f"IntegrityError raised: {e}")
        proposal = asyncio.run(db.get_proposal(proposal_id))
        self.assertEqual(proposal['status'], 'Closed')
    
    @patch('proposals.get_or_create_channel')
    async def test_close_and_announce_results(self, mock_get_or_create_channel):
        """Test the close_and_announce_results function"""
        # Set up mock channel
        mock_get_or_create_channel.return_value = self.results_channel
        
        # Create a proposal
        proposal_id = await self.create_test_proposal()
        
        # Add votes
        await self.add_test_votes(proposal_id)
        
        # Close the proposal
        results = await voting_utils.close_proposal(proposal_id)
        
        # Get the proposal
        proposal = await db.get_proposal(proposal_id)
        
        # Announce results
        await proposals.close_and_announce_results(self.guild, proposal, results)
        
        # Check that results were sent to channels
        self.results_channel.send.assert_called()
        self.proposals_channel.send.assert_called()
    
    def test_check_expired_proposals(self):
        """Test the check_expired_proposals function"""
        # Create a proposal with deadline in the past
        asyncio.run(self.setup_past_deadline_proposal())
        
        # Check for expired proposals
        closed_proposals = asyncio.run(voting_utils.check_expired_proposals())
        
        # Check that proposal was found and closed
        self.assertEqual(len(closed_proposals), 1)
        
        # Check that results were generated
        proposal, results = closed_proposals[0]
        self.assertIsNotNone(results)
        self.assertEqual(results['mechanism'], 'plurality')
    
    async def setup_past_deadline_proposal(self):
        """Create a proposal with deadline in the past"""
        # Create proposal
        title = "Past Deadline Proposal"
        description = "This proposal's deadline has already passed."
        voting_mechanism = "plurality"
        deadline = datetime.now() - timedelta(minutes=10)
        
        # Insert into database
        proposal_id = await db.create_proposal(
            self.guild.id, self.member1.id, title, description, 
            voting_mechanism, deadline, False
        )
        
        # Update status to Voting
        await db.update_proposal_status(proposal_id, "Voting")
        
        # Add votes
        vote_data = {"option": "Option A"}
        await db.add_vote(proposal_id, self.member1.id, vote_data)
        
        return proposal_id
    
    def test_full_vote_flow(self):
        """Test the full flow from proposal creation to result announcement"""
        # Create proposal
        proposal_id = asyncio.run(self.create_test_proposal())
        self.assertIsNotNone(proposal_id)
        
        # Add votes
        asyncio.run(self.add_test_votes(proposal_id))
        
        # Get votes
        votes = asyncio.run(db.get_proposal_votes(proposal_id))
        self.assertEqual(len(votes), 2)
        
        # Close proposal
        results = asyncio.run(voting_utils.close_proposal(proposal_id))
        self.assertIsNotNone(results)
        
        # Get proposal
        proposal = asyncio.run(db.get_proposal(proposal_id))
        self.assertEqual(proposal['status'], 'Closed')
        
        # Announce results
        with patch('proposals.get_or_create_channel') as mock_get_channel:
            mock_get_channel.return_value = self.results_channel
            result = asyncio.run(proposals.close_and_announce_results(self.guild, proposal, results))
            self.assertTrue(result)
            
            # Check that results were sent to channels
            self.results_channel.send.assert_called()
            self.proposals_channel.send.assert_called()
    
if __name__ == '__main__':
    unittest.main()