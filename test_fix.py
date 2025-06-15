import asyncio
import aiosqlite
import json
from datetime import datetime
import sys
import os

# Add the directory containing db.py to the system path
# Assumes this script is in the same directory as db.py
sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), '.')))

# Import the necessary parts from your db module
try:
    # Use get_db if it's a context manager
    from db import get_db
    print("Successfully imported db module.")
except ImportError as e:
    print(f"Error importing db module: {e}")
    print("Please ensure clean_db.py is in the same directory as db.py")
    sys.exit(1)  # Exit if import fails

# --- Use the get_db context manager from db.py ---
# (Assuming your db.py now uses the asynccontextmanager pattern)

# List the proposal IDs you want to delete based on your logs
PROPOSAL_IDS_TO_DELETE = [68, 66, 12]  # Add or remove IDs as needed


async def delete_orphaned_proposals(proposal_ids: list[int]):
    """Deletes specific proposals and their associated data from the database."""
    if not proposal_ids:
        print("No proposal IDs provided to delete.")
        return

    print(f"Attempting to delete proposals with IDs: {proposal_ids}")

    # Use the async context manager from db.py
    async with get_db() as conn:
        print("Database connection established.")
        for proposal_id in proposal_ids:
            try:
                print(f"Deleting data for proposal ID {proposal_id}...")

                # Delete from child tables first due to lack of ON DELETE CASCADE in schema
                # Check for existence first for cleaner output
                cursor = await conn.execute("SELECT COUNT(*) FROM voting_invites WHERE proposal_id = ? OR proposal_id IN (SELECT id FROM proposals WHERE id = ?)", (proposal_id, proposal_id))
                count = (await cursor.fetchone())[0]
                if count > 0:
                    await conn.execute("DELETE FROM voting_invites WHERE proposal_id = ? OR proposal_id IN (SELECT id FROM proposals WHERE id = ?)", (proposal_id, proposal_id))
                    print(f"  Deleted {count} voting invites.")

                cursor = await conn.execute("SELECT COUNT(*) FROM proposal_options WHERE proposal_id = ? OR proposal_id IN (SELECT id FROM proposals WHERE id = ?)", (proposal_id, proposal_id))
                count = (await cursor.fetchone())[0]
                if count > 0:
                    await conn.execute("DELETE FROM proposal_options WHERE proposal_id = ? OR proposal_id IN (SELECT id FROM proposals WHERE id = ?)", (proposal_id, proposal_id))
                    print(f"  Deleted {count} proposal options.")

                cursor = await conn.execute("SELECT COUNT(*) FROM votes WHERE proposal_id = ? OR proposal_id IN (SELECT id FROM proposals WHERE id = ?)", (proposal_id, proposal_id))
                count = (await cursor.fetchone())[0]
                if count > 0:
                    await conn.execute("DELETE FROM votes WHERE proposal_id = ? OR proposal_id IN (SELECT id FROM proposals WHERE id = ?)", (proposal_id, proposal_id))
                    print(f"  Deleted {count} votes.")

                cursor = await conn.execute("SELECT COUNT(*) FROM proposal_results WHERE proposal_id = ? OR proposal_id IN (SELECT id FROM proposals WHERE id = ?)", (proposal_id, proposal_id))
                count = (await cursor.fetchone())[0]
                if count > 0:
                    await conn.execute("DELETE FROM proposal_results WHERE proposal_id = ? OR proposal_id IN (SELECT id FROM proposals WHERE id = ?)", (proposal_id, proposal_id))
                    print(f"  Deleted {count} proposal results.")

                cursor = await conn.execute("SELECT COUNT(*) FROM proposal_notes WHERE proposal_id = ? OR proposal_id IN (SELECT id FROM proposals WHERE id = ?)", (proposal_id, proposal_id))
                count = (await cursor.fetchone())[0]
                if count > 0:
                    await conn.execute("DELETE FROM proposal_notes WHERE proposal_id = ? OR proposal_id IN (SELECT id FROM proposals WHERE id = ?)", (proposal_id, proposal_id))
                    print(f"  Deleted {count} proposal notes.")

                # Finally, delete the proposal itself
                result = await conn.execute("DELETE FROM proposals WHERE proposal_id = ? OR id = ?", (proposal_id, proposal_id))

                if result.rowcount > 0:
                    print(
                        f"✅ Successfully deleted proposal ID {proposal_id} from the proposals table.")
                else:
                    print(
                        f"⚠️ Proposal ID {proposal_id} not found in the database.")

            except Exception as e:
                print(f"❌ Error deleting proposal ID {proposal_id}: {e}")
                # Continue to the next ID even if one fails

        # Commit all changes at the end
        await conn.commit()
        print("Database changes committed.")
        print("Cleanup script finished.")

# --- Main execution block ---
if __name__ == "__main__":
    print("Starting database cleanup script...")
    print(f"Targeting proposals with IDs: {PROPOSAL_IDS_TO_DELETE}")
    # Run the async function
    asyncio.run(delete_orphaned_proposals(PROPOSAL_IDS_TO_DELETE))

    # Alternative: Just clear the pending flag (less destructive)
    # async def clear_pending_flag(proposal_ids: list[int]):
    #      if not proposal_ids:
    #          print("No proposal IDs provided to update.")
    #          return
    #      print(f"Attempting to clear pending flag for proposals with IDs: {proposal_ids}")
    #      async with get_db() as conn:
    #          for proposal_id in proposal_ids:
    #              try:
    #                  result = await conn.execute(
    #                      "UPDATE proposals SET results_pending_announcement = 0 WHERE proposal_id = ? OR id = ?",
    #                      (proposal_id, proposal_id)
    #                  )
    #                  if result.rowcount > 0:
    #                      print(f"✅ Cleared pending flag for proposal ID {proposal_id}.")
    #                  else:
    #                      print(f"⚠️ Proposal ID {proposal_id} not found (or flag already cleared).")
    #              except Exception as e:
    #                  print(f"❌ Error clearing flag for proposal ID {proposal_id}: {e}")
    #          await conn.commit()
    #      print("Cleanup script (clear flag) finished.")
    # # Uncomment the line below and comment out the delete block above to use the flag clearing alternative
    # asyncio.run(clear_pending_flag(PROPOSAL_IDS_TO_DELETE))
