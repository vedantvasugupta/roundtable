import asyncio
import db
import voting
import json
from datetime import datetime, timedelta

async def test_vote_fix():
    """Test the fix for the vote result announcement issue"""
    print("Starting vote fix test...")

    # 1. Create a test proposal
    proposal_id = 999  # Use a high number to avoid conflicts
    server_id = 123456789  # Dummy server ID

    # Check if proposal already exists
    existing = await db.get_proposal(proposal_id)
    if existing:
        print(f"Test proposal {proposal_id} already exists, using it")
    else:
        # Create a test proposal
        now = datetime.now()
        deadline = now + timedelta(minutes=5)

        proposal_data = {
            'proposal_id': proposal_id,
            'server_id': server_id,
            'title': 'Test Proposal for Vote Fix',
            'description': 'This is a test proposal to verify the vote announcement fix.\n- Option A\n- Option B\n- Option C',
            'proposer_id': 111111111,  # Dummy user ID
            'status': 'Voting',
            'voting_mechanism': 'plurality',
            'deadline': deadline.isoformat(),
            'created_at': now.isoformat(),
            'results_pending_announcement': 0
        }

        # Create the proposal using the db functions
        await db.create_proposal(
            server_id=proposal_data['server_id'],
            proposer_id=proposal_data['proposer_id'],
            title=proposal_data['title'],
            description=proposal_data['description'],
            voting_mechanism=proposal_data['voting_mechanism'],
            deadline=datetime.fromisoformat(proposal_data['deadline']),
            requires_approval=False
        )

        # Update the proposal_id to match our test ID
        async with db.get_db() as conn:
            await conn.execute(
                "UPDATE proposals SET proposal_id = ? WHERE title = ?",
                (proposal_id, proposal_data['title'])
            )
            await conn.commit()

        # Add options to the database
        options = ['Option A', 'Option B', 'Option C']
        await db.add_proposal_options(proposal_id, options)

        print(f"Created test proposal {proposal_id}")

    # 2. Add a vote to trigger the all-voters-voted condition
    user_id = 706569427843416076  # Use the same user ID from the error message
    vote_data = {"option": "Option C"}

    # Add a voting invite to simulate all eligible voters
    async with db.get_db() as conn:
        await conn.execute(
            "INSERT INTO voting_invites (proposal_id, voter_id) VALUES (?, ?) ON CONFLICT DO NOTHING",
            (proposal_id, user_id)
        )
        await conn.commit()

    print(f"Added voting invite for user {user_id}")

    # 3. Process the vote to trigger the result calculation and announcement
    print("Processing vote to trigger result calculation...")
    success, message = await voting.process_vote(user_id, proposal_id, vote_data)
    print(f"Vote processing result: {success}, Message: {message}")

    # 4. Check if the results_pending_announcement flag was set
    proposal = await db.get_proposal(proposal_id)
    if proposal and proposal.get('results_pending_announcement'):
        print("SUCCESS: results_pending_announcement flag was set correctly")
    else:
        print("ERROR: results_pending_announcement flag was not set")

    # 5. Check if results were stored
    results = await db.get_proposal_results(proposal_id)
    if results:
        print(f"SUCCESS: Results were stored: {results}")
    else:
        print("ERROR: No results were stored")

    print("Test completed!")

async def main():
    # Initialize the database
    await db.init_db()

    # Run the test
    await test_vote_fix()

if __name__ == "__main__":
    asyncio.run(main())
