import asyncio
import db # Assuming your database functions are in db.py

async def cleanup_stuck_proposals():
    proposal_ids_to_check = [155, 156, 157, 158, 159, 160, 161, 163, 164, 167]
    cleaned_count = 0
    already_ok_count = 0
    error_count = 0

    print("Starting cleanup of proposals with pending announcements but no actual results...")

    await db.init_db() # Ensure DB is ready

    for proposal_id in proposal_ids_to_check:
        try:
            proposal = await db.get_proposal(proposal_id)
            if not proposal:
                print(f"Proposal ID {proposal_id}: Not found. Skipping.")
                error_count += 1
                continue

            pending_announcement = proposal.get('results_pending_announcement')
            # The get_proposal_results function in db.py might return None or an empty dict/list if no results
            # We need to be careful how we check for "no results".
            # Let's assume get_proposal_results returns None if no results row, or a dict that might be empty/minimal if row exists but no actual result data.
            results = await db.get_proposal_results(proposal_id)

            # Condition for cleanup:
            # 1. results_pending_announcement is True (or 1)
            # 2. results is None (no entry in proposal_results table) OR results dictionary is empty or lacks a 'results' key.
            #    A more robust check for "no actual results" might be needed depending on what get_proposal_results returns.
            #    For now, let's assume if `results` is falsy (None, empty dict), it means no meaningful results.

            is_stuck = False
            if pending_announcement: # Checks for True or 1
                if not results: # No entry in proposal_results or empty dict/list
                    is_stuck = True
                elif isinstance(results, dict) and not results.get('results'): # Entry exists, but no actual result details
                     is_stuck = True

            if is_stuck:
                print(f"Proposal ID {proposal_id}: Is pending announcement AND has no/empty results. Clearing flag.")
                await db.update_proposal(proposal_id, {'results_pending_announcement': False})
                # You might also want to set status to 'Failed' or 'Error' if appropriate
                # await db.update_proposal_status(proposal_id, "Failed - No Results")
                print(f"Proposal ID {proposal_id}: Flag cleared.")
                cleaned_count += 1
            elif pending_announcement and results:
                 print(f"Proposal ID {proposal_id}: Pending announcement and HAS results. Leaving as is.")
                 already_ok_count +=1
            else: # Not pending announcement, or pending but has results
                print(f"Proposal ID {proposal_id}: Not pending announcement or already has results. No action needed.")
                already_ok_count += 1

        except Exception as e:
            print(f"Proposal ID {proposal_id}: Error during processing - {e}")
            error_count += 1

    print("\n--- Cleanup Summary ---")
    print(f"Proposals checked: {len(proposal_ids_to_check)}")
    print(f"Flags cleared (stuck proposals fixed): {cleaned_count}")
    print(f"Proposals already OK or not requiring action: {already_ok_count}")
    print(f"Errors encountered: {error_count}")

if __name__ == "__main__":
    asyncio.run(cleanup_stuck_proposals())