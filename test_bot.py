import asyncio
import discord
import unittest
from unittest.mock import MagicMock, patch
import json
import os
import sys
from datetime import datetime, timedelta

# Add the current directory to the path so we can import our modules
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Import our modules
import db
import voting
import proposals
import moderation
import main

class TestBot(unittest.TestCase):
    """Test cases for the Discord bot"""
    
    def setUp(self):
        """Set up test environment"""
        # Create mock objects
        self.bot = MagicMock()
        self.ctx = MagicMock()
        self.guild = MagicMock()
        self.channel = MagicMock()
        self.author = MagicMock()
        self.member = MagicMock()
        
        # Set up mock relationships
        self.ctx.guild = self.guild
        self.ctx.channel = self.channel
        self.ctx.author = self.author
        self.guild.text_channels = []
        self.guild.members = [self.member]
        
        # Set up IDs
        self.guild.id = 123456789
        self.author.id = 987654321
        self.member.id = 111222333
        
        # Set up names
        self.guild.name = "Test Server"
        self.author.name = "Test Author"
        self.member.name = "Test Member"
        
        # Set up mentions
        self.author.mention = "<@987654321>"
        self.member.mention = "<@111222333>"
        
        # Set up roles
        self.admin_role = MagicMock()
        self.admin_role.name = "Admin"
        self.admin_role.permissions = discord.Permissions()
        self.admin_role.permissions.update(administrator=True)
        
        self.verified_role = MagicMock()
        self.verified_role.name = "Verified"
        
        self.muted_role = MagicMock()
        self.muted_role.name = "Muted"
        
        self.guild.roles = [self.admin_role, self.verified_role, self.muted_role]
        
        # Mock discord.utils.get
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
        
        discord.utils.get = mock_get
        
        # Set up database
        asyncio.run(self.setup_database())
    
    async def setup_database(self):
        """Set up test database"""
        # Initialize database
        await db.init_db()
        
        # Add server
        await db.add_server(self.guild.id, self.guild.name, self.author.id, 1)
        
        # Add settings
        await db.update_setting(self.guild.id, "admission_method", "admin")
        await db.update_setting(self.guild.id, "removal_method", "admin")
        
        # Initialize constitutional variables
        await db.init_constitutional_variables(self.guild.id)
    
    def test_command_error_handler(self):
        """Test the command error handler"""
        # Create a CommandNotFound error
        error = discord.ext.commands.CommandNotFound("Command 'constiution' is not found")
        
        # Call the error handler
        asyncio.run(main.on_command_error(self.ctx, error))
        
        # Check that ctx.send was called with a suggestion
        self.ctx.send.assert_called_once()
        args, kwargs = self.ctx.send.call_args
        self.assertIn("constitution", args[0])
    
    def test_see_settings(self):
        """Test the see_settings command"""
        # Call the see_settings command
        asyncio.run(main.see_settings(self.ctx))
        
        # Check that ctx.send was called with an embed
        self.ctx.send.assert_called_once()
        args, kwargs = self.ctx.send.call_args
        self.assertIsInstance(kwargs['embed'], discord.Embed)
        
        # Check that the embed has the correct title
        self.assertEqual(kwargs['embed'].title, "⚙️ Server Settings Overview")
    
    def test_constitution(self):
        """Test the constitution command"""
        # Call the constitution command
        asyncio.run(main.constitution(self.ctx))
        
        # Check that ctx.send was called with an embed
        self.ctx.send.assert_called_once()
        args, kwargs = self.ctx.send.call_args
        self.assertIsInstance(kwargs['embed'], discord.Embed)
        
        # Check that the embed has the correct title
        self.assertEqual(kwargs['embed'].title, "📜 Server Constitution")
        
        # Check that the embed has examples
        self.assertIn("Examples", [field.name for field in kwargs['embed'].fields])
    
    def test_help_guide(self):
        """Test the help_guide command"""
        # Call the help_guide command
        asyncio.run(main.help_guide(self.ctx))
        
        # Check that ctx.send was called with an embed
        self.ctx.send.assert_called_once()
        args, kwargs = self.ctx.send.call_args
        self.assertIsInstance(kwargs['embed'], discord.Embed)
        
        # Check that the embed has the correct title
        self.assertEqual(kwargs['embed'].title, "📚 Bot Command Guide")
        
        # Check that the embed has constitutional variables section
        self.assertIn("Constitutional Variables", [field.name for field in kwargs['embed'].fields])
    
    @patch('proposals.open_proposal_form')
    def test_propose_command_no_args(self, mock_open_form):
        """Test the propose command with no arguments"""
        # Call the propose command with no arguments
        asyncio.run(main.propose_command(self.ctx))
        
        # Check that open_proposal_form was called
        mock_open_form.assert_called_once_with(self.ctx)
    
    @patch('proposals.create_proposal')
    def test_propose_command_with_args(self, mock_create_proposal):
        """Test the propose command with arguments"""
        # Set up mock return value
        mock_create_proposal.return_value = 1
        
        # Call the propose command with arguments
        args = '"Test Title" "Test Description" plurality Yes No'
        asyncio.run(main.propose_command(self.ctx, args=args))
        
        # Check that create_proposal was called with the correct arguments
        mock_create_proposal.assert_called_once()
        call_args = mock_create_proposal.call_args[0]
        self.assertEqual(call_args[1], "Test Title")
        self.assertEqual(call_args[2], "Test Description")
        self.assertEqual(call_args[3], "plurality")
        self.assertEqual(call_args[4], ["Yes", "No"])
    
    @patch('voting.process_vote')
    def test_vote_command(self, mock_process_vote):
        """Test the vote command"""
        # Set up mock return values
        mock_process_vote.return_value = (True, "Your vote has been recorded.")
        
        # Mock db.get_proposal
        async def mock_get_proposal(proposal_id):
            return {
                'proposal_id': proposal_id,
                'status': 'Voting',
                'voting_mechanism': 'plurality',
                'description': 'Test Description'
            }
        
        with patch('db.get_proposal', mock_get_proposal):
            # Create a mock DM channel
            dm_channel = MagicMock(spec=discord.DMChannel)
            self.ctx.channel = dm_channel
            
            # Call the vote command
            asyncio.run(main.vote(self.ctx, 1, "Yes"))
            
            # Check that process_vote was called with the correct arguments
            mock_process_vote.assert_called_once_with(self.author.id, 1, {"option": "Yes"})
            
            # Check that ctx.send was called with the success message
            self.ctx.send.assert_called_once_with("Your vote has been recorded.")
    
    def test_extract_options_from_description(self):
        """Test the extract_options_from_description function"""
        # Test with bullet points
        description = """
        This is a test proposal.
        
        Options:
        • Option 1
        • Option 2
        • Option 3
        """
        options = proposals.extract_options_from_description(description)
        self.assertEqual(options, ["Option 1", "Option 2", "Option 3"])
        
        # Test with Yes/No format
        description = "Should we approve this? Yes or No?"
        options = proposals.extract_options_from_description(description)
        self.assertEqual(options, ["Yes", "No"])
        
        # Test with no options
        description = "This is a test with no options."
        options = proposals.extract_options_from_description(description)
        self.assertIsNone(options)
    
    def test_parse_duration(self):
        """Test the parse_duration function"""
        # Test days
        duration = "2d"
        seconds = moderation.parse_duration(duration)
        self.assertEqual(seconds, 2 * 86400)
        
        # Test hours
        duration = "3h"
        seconds = moderation.parse_duration(duration)
        self.assertEqual(seconds, 3 * 3600)
        
        # Test minutes
        duration = "45m"
        seconds = moderation.parse_duration(duration)
        self.assertEqual(seconds, 45 * 60)
        
        # Test seconds
        duration = "30s"
        seconds = moderation.parse_duration(duration)
        self.assertEqual(seconds, 30)
        
        # Test combined
        duration = "1d2h30m15s"
        seconds = moderation.parse_duration(duration)
        self.assertEqual(seconds, 86400 + 7200 + 1800 + 15)
        
        # Test invalid
        duration = "invalid"
        seconds = moderation.parse_duration(duration)
        self.assertEqual(seconds, 0)
    
    def test_format_duration(self):
        """Test the format_duration function"""
        # Test seconds
        seconds = 30
        formatted = moderation.format_duration(seconds)
        self.assertEqual(formatted, "30 seconds")
        
        # Test minutes
        seconds = 90
        formatted = moderation.format_duration(seconds)
        self.assertEqual(formatted, "1 minute")
        
        # Test hours
        seconds = 7200
        formatted = moderation.format_duration(seconds)
        self.assertEqual(formatted, "2 hours")
        
        # Test days
        seconds = 86400 * 3
        formatted = moderation.format_duration(seconds)
        self.assertEqual(formatted, "3 days")
    
    def test_get_voting_mechanism(self):
        """Test the get_voting_mechanism function"""
        # Test plurality
        mechanism = voting.get_voting_mechanism("plurality")
        self.assertEqual(mechanism, voting.PluralityVoting)
        
        # Test borda
        mechanism = voting.get_voting_mechanism("borda")
        self.assertEqual(mechanism, voting.BordaCount)
        
        # Test approval
        mechanism = voting.get_voting_mechanism("approval")
        self.assertEqual(mechanism, voting.ApprovalVoting)
        
        # Test runoff
        mechanism = voting.get_voting_mechanism("runoff")
        self.assertEqual(mechanism, voting.RunoffVoting)
        
        # Test condorcet
        mechanism = voting.get_voting_mechanism("condorcet")
        self.assertEqual(mechanism, voting.CondorcetMethod)
        
        # Test invalid
        mechanism = voting.get_voting_mechanism("invalid")
        self.assertIsNone(mechanism)
    
    @unittest.skip("PluralityVoting legacy test skipped")
    def test_plurality_voting(self):
        pass
    
    @unittest.skip("ApprovalVoting legacy test skipped")
    def test_approval_voting(self):
        pass

if __name__ == '__main__':
    unittest.main()
