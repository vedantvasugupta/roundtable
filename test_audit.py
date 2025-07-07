import asyncio
import json
import unittest
from unittest.mock import MagicMock
from datetime import datetime, timedelta

import db
import main

class TestAudit(unittest.TestCase):
    def setUp(self):
        self.ctx = MagicMock()
        self.guild = MagicMock()
        self.guild.id = 123
        self.guild.text_channels = []

        self.member1 = MagicMock()
        self.member1.id = 1
        self.member1.display_name = "Alice"
        self.member2 = MagicMock()
        self.member2.id = 2
        self.member2.display_name = "Bob"

        self.guild.get_member.side_effect = lambda uid: {1: self.member1, 2: self.member2}.get(uid)
        self.ctx.guild = self.guild
        self.ctx.send = MagicMock()

        asyncio.run(db.init_db())
        asyncio.run(db.add_server(self.guild.id, "Test", self.member1.id, 2))
        asyncio.run(db.init_constitutional_variables(self.guild.id))

    def create_proposal(self):
        deadline = (datetime.utcnow() + timedelta(days=1)).strftime('%Y-%m-%d %H:%M:%S')
        return asyncio.run(db.create_proposal(self.guild.id, self.member1.id, "Title", "Desc", "plurality", deadline, False, None, None, "Voting"))

    def test_identifier_consistency(self):
        proposal_id = 1
        ident1 = asyncio.run(db.get_or_create_vote_identifier(self.guild.id, self.member1.id, proposal_id))
        ident2 = asyncio.run(db.get_or_create_vote_identifier(self.guild.id, self.member1.id, proposal_id))
        self.assertEqual(ident1, ident2)

    def test_audit_public_and_anonymous(self):
        prop_id = self.create_proposal()
        asyncio.run(db.get_or_create_vote_identifier(self.guild.id, self.member1.id, prop_id))
        asyncio.run(db.record_vote(self.member1.id, prop_id, json.dumps({"option": "A"})))
        asyncio.run(db.record_vote(self.member2.id, prop_id, json.dumps({"option": "B"})))

        asyncio.run(db.update_constitutional_variable(self.guild.id, "vote_privacy", "public"))
        asyncio.run(main.audit(self.ctx, prop_id))
        args, _ = self.ctx.send.call_args
        self.assertIn("Alice", args[0])
        self.ctx.send.reset_mock()

        asyncio.run(db.update_constitutional_variable(self.guild.id, "vote_privacy", "anonymous"))
        asyncio.run(main.audit(self.ctx, prop_id))
        args, _ = self.ctx.send.call_args
        self.assertNotIn("Alice", args[0])
        self.assertNotIn("Bob", args[0])

if __name__ == '__main__':
    unittest.main()
