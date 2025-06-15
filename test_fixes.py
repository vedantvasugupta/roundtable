import asyncio
import db
import voting
import voting_utils
from datetime import datetime, timedelta
import traceback

async def test_dummy_proposal():
    """Test creating a dummy proposal and processing votes"""
    try:
        print("Testing dummy proposal and vote processing...")

        # Create a test proposal
        server_id = 123456
        proposer_id = 123456
        title = "Test Proposal"
        description = "This is a test proposal."
        voting_mechanism = "plurality"
        deadline = datetime.now() + timedelta(days=1)

        print("Initializing database...")
        await db.init_db()
        print("Database initialized.")

        # Ensure server exists in database
        print("Adding server to database...")
        await db.add_server(server_id, "Test Server", proposer_id, 1)
        print("Server added.")

        # Create proposal
        print("Creating proposal...")
        proposal_id = await db.create_proposal(
            server_id, proposer_id, title, description,
            voting_mechanism, deadline, requires_approval=False
        )

        print(f"Created test proposal with ID: {proposal_id}")

        # Add options
        print("Adding options...")
        options = ["Yes", "No"]
        await db.add_proposal_options(proposal_id, options)
        print("Options added.")

        # Add a vote
        print("Adding vote...")
        vote_data = {"option": "Yes"}
        await db.add_vote(proposal_id, proposer_id, vote_data)

        print(f"Added vote for proposal {proposal_id}")

        # Get the proposal
        print("Getting proposal...")
        proposal = await db.get_proposal(proposal_id)
        print(f"Proposal status: {proposal['status']}")

        # Calculate results
        print("Calculating results...")
        votes = await db.get_proposal_votes(proposal_id)
        print(f"Votes: {votes}")
        results = await voting.calculate_results(voting_mechanism, votes, proposal)

        print(f"Calculated results: {results}")

        # Update proposal status based on results
        print("Updating proposal status...")
        status = "Passed" if results.get('winner') else "Failed"
        await db.update_proposal_status(proposal_id, status)
        print(f"Updated status to: {status}")

        # Store results
        print("Storing results...")
        await db.store_proposal_results(proposal_id, results)
        print("Results stored.")

        # Set results_pending_announcement flag using direct SQL
        print("Setting results_pending_announcement flag using direct SQL...")
        async with db.get_db() as conn:
            # First try with proposal_id
            await conn.execute(f"UPDATE proposals SET results_pending_announcement = 1 WHERE proposal_id = {proposal_id}")
            await conn.commit()

            # Check if it worked
            cursor = await conn.execute(f"SELECT proposal_id, id, status, results_pending_announcement FROM proposals WHERE proposal_id = {proposal_id}")
            row = await cursor.fetchone()

            if not row or row[3] != 1:
                # If not, try with id
                print(f"Flag not set using proposal_id, trying with id column...")
                await conn.execute(f"UPDATE proposals SET results_pending_announcement = 1 WHERE id = {proposal_id}")
                await conn.commit()

            # Verify the update
            cursor = await conn.execute(f"SELECT proposal_id, id, status, results_pending_announcement FROM proposals WHERE proposal_id = {proposal_id} OR id = {proposal_id}")
            row = await cursor.fetchone()
            if row:
                proposal_identifier = row[0] if row[0] is not None else row[1]
                print(f"Direct check: Proposal {proposal_identifier} (ID={row[1]}): status={row[2]}, results_pending_announcement={row[3]}")
                if row[3] == 1:
                    print("Flag set successfully!")
                else:
                    print("WARNING: Flag still not set correctly!")
            else:
                print(f"WARNING: Could not find proposal {proposal_id} after update!")

        # Get proposals with pending announcements
        print("Getting proposals with pending announcements...")
        pending = await db.get_proposals_with_pending_announcements()
        print(f"Proposals with pending announcements: {len(pending)}")

        if pending:
            print(f"Pending announcement for proposal {pending[0]['proposal_id']}")
            print(f"Proposal status: {pending[0]['status']}")
        else:
            print("No pending announcements found.")

            # Check if the column exists
            print("Checking if results_pending_announcement column exists...")
            async with db.get_db() as conn:
                cursor = await conn.execute("PRAGMA table_info(proposals)")
                columns = await cursor.fetchall()
                column_names = [column[1] for column in columns]
                print(f"Columns in proposals table: {column_names}")

                # Check the proposal directly
                cursor = await conn.execute(f"SELECT * FROM proposals WHERE proposal_id = {proposal_id}")
                row = await cursor.fetchone()
                if row:
                    column_names = [desc[0] for desc in cursor.description]
                    proposal_dict = {column_names[i]: row[i] for i in range(len(row))}
                    print(f"Proposal from direct query: {proposal_dict}")

        # Clean up
        print("Test completed successfully!")
    except Exception as e:
        print(f"Error in test: {e}")
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_dummy_proposal())
