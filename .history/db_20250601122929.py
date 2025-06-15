import aiosqlite
import json
from datetime import datetime, timedelta
from functools import lru_cache
from contextlib import asynccontextmanager
import sqlite3
from typing import Optional, Dict, Any, List

# New table for campaigns (MOVED TO TOP)
CREATE_CAMPAIGNS_TABLE = """
CREATE TABLE IF NOT EXISTS campaigns (
    campaign_id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    creator_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    total_tokens_per_voter INTEGER NOT NULL,
    num_expected_scenarios INTEGER NOT NULL,
    current_defined_scenarios INTEGER DEFAULT 0,
    status TEXT NOT NULL CHECK(status IN ('setup', 'active', 'completed', 'archived')) DEFAULT 'setup',
    creation_timestamp TEXT NOT NULL
);
"""

CREATE_USER_CAMPAIGN_PARTICIPATION_TABLE = """
CREATE TABLE IF NOT EXISTS user_campaign_participation (
    participation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    remaining_tokens INTEGER NOT NULL,
    last_updated_timestamp TEXT NOT NULL,
    UNIQUE (campaign_id, user_id),
    FOREIGN KEY (campaign_id) REFERENCES campaigns(campaign_id) ON DELETE CASCADE
);
"""

CREATE_PENDING_PROPOSAL_NOTIFICATIONS_TABLE = """
CREATE TABLE IF NOT EXISTS pending_proposal_notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    proposal_id INTEGER NOT NULL,
    message TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (proposal_id) REFERENCES proposals(proposal_id) ON DELETE CASCADE
);
"""

# Add custom timestamp adapter to handle malformed timestamp formats
def adapt_datetime(val):
    """Convert datetime to ISO format string for SQLite storage"""
    return val.isoformat()

def convert_timestamp(val):
    """Convert SQLite timestamp to Python datetime, handling malformed formats"""
    try:
        # Standard format with space: "YYYY-MM-DD HH:MM:SS"
        if b' ' in val:
            datepart, timepart = val.split(b" ")
            year, month, day = map(int, datepart.split(b"-"))
            timepart_full = timepart.split(b".")
            hours, minutes, seconds = map(int, timepart_full[0].split(b":"))

            if len(timepart_full) == 2:
                microseconds = int(timepart_full[1])
            else:
                microseconds = 0

            return datetime(year, month, day, hours, minutes, seconds, microseconds)
        # ISO format with T: "YYYY-MM-DDTHH:MM:SS"
        elif b'T' in val:
            datepart, timepart = val.split(b"T")
            year, month, day = map(int, datepart.split(b"-"))
            timepart_full = timepart.split(b".")
            hours, minutes, seconds = map(int, timepart_full[0].split(b":"))

            if len(timepart_full) == 2:
                microseconds = int(timepart_full[1].split(b"+")[0])
            else:
                microseconds = 0

            return datetime(year, month, day, hours, minutes, seconds, microseconds)
        # Just a date: "YYYY-MM-DD"
        else:
            year, month, day = map(int, val.split(b"-"))
            return datetime(year, month, day)
    except Exception as e:
        # If parsing fails, return the current time and log the error
        print(f"ERROR parsing timestamp: {val}, error: {e}")
        return datetime.now()

# Register the adapters
sqlite3.register_adapter(datetime, adapt_datetime)
sqlite3.register_converter("timestamp", convert_timestamp)

DATABASE_FILE = "bot_database.db"

# Enable Write-Ahead Logging (WAL) for better concurrency
PRAGMA_WAL = "PRAGMA journal_mode=WAL;"

# Decorator for database connection management
@asynccontextmanager
async def get_db():
    db = await aiosqlite.connect(DATABASE_FILE)
    db.row_factory = aiosqlite.Row # Access columns by name
    try:
        await db.execute(PRAGMA_WAL) # Enable WAL mode
        yield db
    finally:
        await db.close()

# ========================
# 🔹 SERVER FUNCTIONS
# ========================

async def add_server(server_id, server_name, owner_id, member_count):
    async with get_db() as conn:
        await conn.execute(
            "INSERT INTO servers (server_id, server_name, owner_id, member_count) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(server_id) DO NOTHING",
            (server_id, server_name, owner_id, member_count)
        )
        await conn.commit()


# ========================
# 🔹 SETTINGS FUNCTIONS
# ========================

async def update_setting(server_id, setting_key, setting_value):
    async with get_db() as conn:
        await conn.execute(
            "INSERT INTO settings (server_id, setting_key, setting_value) VALUES (?, ?, ?) "
            "ON CONFLICT(server_id, setting_key) DO UPDATE SET setting_value = EXCLUDED.setting_value",
            (server_id, setting_key, setting_value)
        )
        await conn.commit()

        # Invalidate cache
        get_settings.cache_clear()


from async_lru import alru_cache  # Install via: pip install async_lru

@alru_cache(maxsize=32)
async def get_settings(server_id):
    async with get_db() as conn:
        async with conn.execute("SELECT setting_key, setting_value FROM settings WHERE server_id = ?", (server_id,)) as cursor:
            rows = await cursor.fetchall()
            return {row[0]: row[1] for row in rows}


# ========================
# 🔹 CONSTITUTIONAL VARIABLES
# ========================

async def init_constitutional_variables(server_id):
    """Initialize default constitutional variables for a server"""
    defaults = {
        "proposal_requires_approval": {
            "value": "true",
            "type": "boolean",
            "description": "Whether proposals require admin approval before voting"
        },
        "eligible_voters_role": {
            "value": "everyone",
            "type": "role",
            "description": "Role required to vote on proposals (everyone = all members)"
        },
        "eligible_proposers_role": {
            "value": "everyone",
            "type": "role",
            "description": "Role required to create proposals (everyone = all members)"
        },
        "warning_threshold": {
            "value": "3",
            "type": "number",
            "description": "Number of warnings before automatic action"
        },
        "warning_action": {
            "value": "kick",
            "type": "text",
            "description": "Action to take when warning threshold is reached (kick, ban, mute)"
        },
        "mute_role": {
            "value": "Muted",
            "type": "role",
            "description": "Role to assign when muting a user"
        }
    }

    async with get_db() as conn:
        for var_name, var_data in defaults.items():
            await conn.execute(
                """
                INSERT INTO constitutional_variables
                (server_id, variable_name, variable_value, variable_type, description)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(server_id, variable_name) DO NOTHING
                """,
                (server_id, var_name, var_data["value"], var_data["type"], var_data["description"])
            )
        await conn.commit()


async def get_constitutional_variable(server_id, variable_name):
    """Get a specific constitutional variable"""
    async with get_db() as conn:
        async with conn.execute(
            "SELECT variable_value, variable_type FROM constitutional_variables WHERE server_id = ? AND variable_name = ?",
            (server_id, variable_name)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return {"value": row[0], "type": row[1]}
            return None


async def get_constitutional_variables(server_id):
    """Get all constitutional variables for a server"""
    async with get_db() as conn:
        async with conn.execute(
            """
            SELECT variable_name, variable_value, variable_type, description
            FROM constitutional_variables
            WHERE server_id = ?
            """,
            (server_id,)
        ) as cursor:
            rows = await cursor.fetchall()
            return {
                row[0]: {
                    "value": row[1],
                    "type": row[2],
                    "description": row[3]
                } for row in rows
            }


async def update_constitutional_variable(server_id, variable_name, variable_value):
    """Update a constitutional variable"""
    async with get_db() as conn:
        await conn.execute(
            """
            UPDATE constitutional_variables
            SET variable_value = ?
            WHERE server_id = ? AND variable_name = ?
            """,
            (variable_value, server_id, variable_name)
        )
        await conn.commit()


# ========================
# 🔹 PROPOSAL FUNCTIONS
# ========================

async def create_proposal(
    server_id: int,
    proposer_id: int,
    title: str,
    description: str,
    voting_mechanism: str,
    deadline: str,
    requires_approval: bool = True,
    hyperparameters: Optional[Dict[str, Any]] = None,
    campaign_id: Optional[int] = None,
    scenario_order: Optional[int] = None
):
    """Create a new proposal and return its ID. Can be part of a campaign."""
    hyperparameters_json = json.dumps(hyperparameters) if hyperparameters else None
    # Ensure description has a default if empty, to avoid DB constraint issues if any
    description_to_store = description if description and description.strip() else "No description provided."

    # Determine status based on requires_approval
    status = "Pending Approval" if requires_approval else "Voting"
    current_time_utc = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S.%f')

    # proposal_text might be same as description for now, or could be a more detailed version later
    proposal_text_to_store = description_to_store

    # guild_id is often same as server_id in Discord context, ensure it's passed if your schema strictly requires it
    # If guild_id is not a separate concept from server_id for your bot, you might simplify this.
    # Assuming server_id can be used for guild_id if the column exists and needs a value.
    guild_id_to_store = server_id

    sql = """
        INSERT INTO proposals
        (server_id, guild_id, proposer_id, title, description, proposal_text,
         voting_mechanism, deadline, requires_approval, status, hyperparameters,
         creation_timestamp, last_updated_timestamp, campaign_id, scenario_order)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    params = (
        server_id, guild_id_to_store, proposer_id, title, description_to_store, proposal_text_to_store,
        voting_mechanism, deadline, requires_approval, status, hyperparameters_json,
        current_time_utc, current_time_utc, campaign_id, scenario_order
    )

    try:
        async with get_db() as conn:
            cursor = await conn.execute(sql, params)
            await conn.commit()
            proposal_id = cursor.lastrowid
            print(f"DEBUG: Created proposal P#{proposal_id} with campaign_id={campaign_id}, scenario_order={scenario_order}, requires_approval={requires_approval}, title='{title}'")
            return proposal_id
    except Exception as e:
        print(f"ERROR creating proposal '{title}': {e}")
        # traceback.print_exc() # Re-enable for deeper debugging if needed
        return None


async def get_proposal(proposal_id):
    """Get a proposal by ID"""
    async with get_db() as conn:
        async with conn.execute(
            "SELECT * FROM proposals WHERE proposal_id = ? OR id = ?",
            (proposal_id, proposal_id)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                column_names = [desc[0] for desc in cursor.description]
                proposal = {column_names[i]: row[i] for i in range(len(row))}
                # Ensure the dictionary has the key 'proposal_id'
                if proposal.get('proposal_id') is None and 'id' in proposal:
                    proposal['proposal_id'] = proposal['id']

                # Deserialize hyperparameters
                hyperparameters_json = proposal.get('hyperparameters')
                if hyperparameters_json:
                    try:
                        proposal['hyperparameters'] = json.loads(hyperparameters_json)
                    except json.JSONDecodeError:
                        print(f"WARNING: Failed to deserialize hyperparameters for proposal {proposal.get('proposal_id')}. Value: {hyperparameters_json}")
                        proposal['hyperparameters'] = None # Or {}
                else:
                    proposal['hyperparameters'] = None # Or {}

                return proposal
            return None


async def get_server_proposals(server_id, status=None):
    """Get all proposals for a server, optionally filtered by status"""
    async with get_db() as conn:
        if (status):
            query = "SELECT * FROM proposals WHERE server_id = ? AND status = ? ORDER BY created_at DESC"
            params = (server_id, status)
        else:
            query = "SELECT * FROM proposals WHERE server_id = ? ORDER BY created_at DESC"
            params = (server_id,)

        async with conn.execute(query, params) as cursor:
            rows = await cursor.fetchall()
            if rows:
                column_names = [desc[0] for desc in cursor.description]
                proposals_list = [{column_names[i]: row[i] for i in range(len(row))} for row in rows]
                for proposal in proposals_list:
                    # Deserialize hyperparameters for each proposal
                    hyperparameters_json = proposal.get('hyperparameters')
                    if hyperparameters_json:
                        try:
                            proposal['hyperparameters'] = json.loads(hyperparameters_json)
                        except json.JSONDecodeError:
                            print(f"WARNING: Failed to deserialize hyperparameters for proposal {proposal.get('proposal_id')} in list. Value: {hyperparameters_json}")
                            proposal['hyperparameters'] = None # Or {}
                    else:
                        proposal['hyperparameters'] = None # Or {}
                return proposals_list
            return []


async def get_proposals_by_status(status):
    """Get all proposals with a specific status"""
    async with get_db() as conn:
        async with conn.execute(
            "SELECT * FROM proposals WHERE status = ?",
            (status,)
        ) as cursor:
            rows = await cursor.fetchall()
            if rows:
                column_names = [desc[0] for desc in cursor.description]
                proposals_list = [{column_names[i]: row[i] for i in range(len(row))} for row in rows]
                for proposal in proposals_list:
                    # Deserialize hyperparameters for each proposal
                    hyperparameters_json = proposal.get('hyperparameters')
                    if hyperparameters_json:
                        try:
                            proposal['hyperparameters'] = json.loads(hyperparameters_json)
                        except json.JSONDecodeError:
                            print(f"WARNING: Failed to deserialize hyperparameters for proposal {proposal.get('proposal_id')} in list. Value: {hyperparameters_json}")
                            proposal['hyperparameters'] = None # Or {}
                    else:
                        proposal['hyperparameters'] = None # Or {}
                return proposals_list
            return []


async def update_proposal_status(proposal_id, new_status, approved_by=None):
    """Update a proposal's status"""
    async with get_db() as conn:
        if approved_by:
            await conn.execute(
                "UPDATE proposals SET status = ?, approved_by = ? WHERE proposal_id = ? OR id = ?",
                (new_status, approved_by, proposal_id, proposal_id)
            )
        else:
            await conn.execute(
                "UPDATE proposals SET status = ? WHERE proposal_id = ? OR id = ?",
                (new_status, proposal_id, proposal_id)
            )
        await conn.commit()


async def store_proposal_results(proposal_id, results_dict):
    """Store the results of a proposal vote"""
    # Convert dictionary to JSON string
    if isinstance(results_dict, dict):
        results_json = json.dumps(results_dict)
    else:
        # If it's already a string, use it as is
        results_json = results_dict

    print(f"DEBUG: Storing results for proposal {proposal_id}: {results_json[:100]}...")

    async with get_db() as conn:
        # First check if we need to use id or proposal_id
        async with conn.execute(
            "SELECT id, proposal_id FROM proposals WHERE id = ? OR proposal_id = ?",
            (proposal_id, proposal_id)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                # Use the proposal_id column if it exists, otherwise use id
                actual_id = row[1] if row[1] is not None else row[0]

                try:
                    await conn.execute(
                        """
                        INSERT INTO proposal_results (proposal_id, results)
                        VALUES (?, ?)
                        ON CONFLICT(proposal_id) DO UPDATE SET results = EXCLUDED.results
                        """,
                        (actual_id, results_json)
                    )
                    await conn.commit()
                    print(f"DEBUG: Successfully stored results for proposal {proposal_id}")
                    return True
                except Exception as e:
                    print(f"ERROR storing results: {e}")
                    import traceback
                    traceback.print_exc()
                    return False


async def get_proposal_results(proposal_id):
    """Get the results of a proposal vote"""
    async with get_db() as conn:
        # Try to get results using both id and proposal_id
        async with conn.execute(
            "SELECT results FROM proposal_results WHERE proposal_id = ? OR proposal_id IN (SELECT id FROM proposals WHERE id = ?)",
            (proposal_id, proposal_id)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return json.loads(row[0])
            return None


async def get_expired_proposals():
    """
    Get all proposals that have passed their deadline but are still in 'Voting' status.
    Returns:
        list: A list of expired proposal dictionaries, each GUARANTEED to have a 'proposal_id' key.
    """
    # Use the async context manager to get a connection
    async with get_db() as conn:  # <--- Correctly use the 'conn' object
        # Use the connection object 'conn' for all DB operations inside this block
        async with conn.execute(  # <--- Use conn.execute, NOT db.execute
            "SELECT * FROM proposals WHERE status = 'Voting' AND deadline < datetime('now')"
        ) as cursor:
            rows = await cursor.fetchall()
            if not rows:
                return []

            column_names = [desc[0] for desc in cursor.description]
            expired = []

            for row in rows:
                # Create a dict from the row using column names
                proposal_dict = {}
                for i, col_name in enumerate(column_names):
                    proposal_dict[col_name] = row[i]

                # Ensure 'proposal_id' key exists and contains the primary key value
                # This handles potential historical data where 'id' might have been used
                # as the primary key instead of 'proposal_id'.
                identifier = proposal_dict.get('proposal_id')
                if identifier is None and 'id' in proposal_dict:
                    identifier = proposal_dict['id']

                if identifier is None:
                    # This should ideally not happen if 'id' or 'proposal_id' is the PK and selected by SELECT *
                    print(
                        f"WARNING: Could not determine primary key (id or proposal_id) for a row in get_expired_proposals. Skipping row: {proposal_dict}")
                    continue

                # Ensure the dictionary returned consistently uses 'proposal_id' as the key
                # for the primary key value.
                proposal_dict['proposal_id'] = identifier

                # Remove the redundant 'id' key if it's just a duplicate of 'proposal_id'
                # and the column name was actually 'id', not 'proposal_id'.
                if 'id' in proposal_dict and proposal_dict['id'] == proposal_dict['proposal_id'] and 'id' != 'proposal_id':
                    del proposal_dict['id']
                # Note: If 'id' is just an alias for the primary key 'proposal_id' due to
                # sqlite settings or query optimization, the check 'id' != 'proposal_id'
                # might be false. The primary goal is to ensure 'proposal_id' holds the PK value.

                expired.append(proposal_dict)

            return expired


async def update_proposal(proposal_id, update_data):
    """Update a proposal with arbitrary fields"""
    async with get_db() as conn:
        # Convert boolean values to integers for SQLite
        processed_data = {}
        for key, value in update_data.items():
            if isinstance(value, bool):
                processed_data[key] = 1 if value else 0
            else:
                processed_data[key] = value

        # Create update fields dynamically
        set_fields = ", ".join([f"{key} = ?" for key in processed_data.keys()])
        values = list(processed_data.values())

        # Debug print
        print(f"DEBUG: Updating proposal {proposal_id} with fields: {set_fields}")
        print(f"DEBUG: Values: {values}")

        # Try to update using proposal_id first
        values.append(proposal_id)
        result = await conn.execute(
            f"UPDATE proposals SET {set_fields} WHERE proposal_id = ?",
            values
        )

        # Check if any rows were affected
        if result.rowcount == 0:
            # If no rows were affected, try using id column
            print(f"DEBUG: No rows affected using proposal_id, trying with id column")
            await conn.execute(
                f"UPDATE proposals SET {set_fields} WHERE id = ?",
                values
            )

        await conn.commit()

        # Verify the update
        async with conn.execute(
            "SELECT proposal_id, id, status, results_pending_announcement FROM proposals WHERE proposal_id = ? OR id = ?",
            (proposal_id, proposal_id)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                print(f"DEBUG: After update - Proposal {row[0] or row[1]}: status={row[2]}, results_pending_announcement={row[3]}")
            else:
                print(f"DEBUG: Could not find proposal {proposal_id} after update")

        return True


async def add_proposal_note(proposal_id, note_type, note_text):
    """Add a note to a proposal (e.g., rejection reason)"""
    async with get_db() as conn:
        await conn.execute(
            "INSERT INTO proposal_notes (proposal_id, note_type, note_text, created_at) VALUES (?, ?, ?, CURRENT_TIMESTAMP)",
            (proposal_id, note_type, note_text)
        )
        await conn.commit()
        return True


async def get_proposal_notes(proposal_id, note_type=None):
    """Get notes for a proposal, optionally filtered by type"""
    async with get_db() as conn:
        if note_type:
            async with conn.execute(
                "SELECT * FROM proposal_notes WHERE proposal_id = ? AND note_type = ? ORDER BY created_at DESC",
                (proposal_id, note_type)
            ) as cursor:
                notes = await cursor.fetchall()
        else:
            async with conn.execute(
                "SELECT * FROM proposal_notes WHERE proposal_id = ? ORDER BY created_at DESC",
                (proposal_id,)
            ) as cursor:
                notes = await cursor.fetchall()
        return notes


async def get_all_active_proposals():
    """Get all proposals with 'Voting' status"""
    async with get_db() as conn:
        async with conn.execute(
            "SELECT * FROM proposals WHERE status = 'Voting'"
        ) as cursor:
            proposals = await cursor.fetchall()
        return proposals


# ========================
# 🔹 VOTE FUNCTIONS
# ========================

async def add_vote(proposal_id, voter_id, vote_data):
    """Add a vote for a proposal"""
    vote_json = json.dumps(vote_data)
    async with get_db() as conn:
        # First check if we need to use id or proposal_id
        async with conn.execute(
            "SELECT id, proposal_id FROM proposals WHERE id = ? OR proposal_id = ?",
            (proposal_id, proposal_id)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                # Use the proposal_id column if it exists, otherwise use id
                actual_id = row[1] if row[1] is not None else row[0]

                await conn.execute(
                    """
                    INSERT INTO votes (proposal_id, voter_id, vote_data)
                    VALUES (?, ?, ?)
                    """,
                    (actual_id, voter_id, vote_json)
                )
                await conn.commit()


async def update_vote(vote_id, vote_data):
    """Update an existing vote"""
    vote_json = json.dumps(vote_data)
    async with get_db() as conn:
        await conn.execute(
            "UPDATE votes SET vote_data = ? WHERE vote_id = ?",
            (vote_json, vote_id)
        )
        await conn.commit()


async def get_user_vote(proposal_id, voter_id):
    """Get a user's vote for a proposal"""
    async with get_db() as conn:
        async with conn.execute(
            """
            SELECT * FROM votes WHERE
            (proposal_id = ? OR proposal_id IN (SELECT id FROM proposals WHERE id = ?))
            AND voter_id = ?
            """,
            (proposal_id, proposal_id, voter_id)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                column_names = [desc[0] for desc in cursor.description]
                vote = {column_names[i]: row[i] for i in range(len(row))}
                # Manually deserialize vote_data if it's a string
                if 'vote_data' in vote and isinstance(vote.get('vote_data'), str):
                    try:
                        vote['vote_data'] = json.loads(vote['vote_data'])
                    except json.JSONDecodeError:
                        print(
                            f"WARNING: Failed to deserialize vote_data for user vote. Keeping as string.")
                        # Keep as string
                return vote
            return None


async def get_proposal_votes(proposal_id):
    """Get all votes for a proposal"""
    async with get_db() as conn:  # Use conn
        async with conn.execute(
            """
            SELECT * FROM votes WHERE
            proposal_id = ? OR proposal_id IN (SELECT id FROM proposals WHERE id = ?)
            """,
            (proposal_id, proposal_id)
        ) as cursor:
            rows = await cursor.fetchall()
            if rows:
                column_names = [desc[0] for desc in cursor.description]
                results = []
                vote_data_index = column_names.index(
                    'vote_data') if 'vote_data' in column_names else -1

                for row in rows:
                    vote = {column_names[i]: row[i] for i in range(len(row))}
                    # Manually deserialize vote_data if it's a string and the column exists
                    if vote_data_index != -1 and isinstance(vote.get('vote_data'), str):
                        try:
                            vote['vote_data'] = json.loads(vote['vote_data'])
                        except json.JSONDecodeError:
                            print(
                                f"WARNING: Failed to deserialize vote_data for vote {vote.get('vote_id')}. Keeping as string.")
                            # Keep it as a string if deserialization fails
                    results.append(vote)
                return results
            return []

async def get_invited_voters(proposal_id):
    """Get all voters who have been invited to vote on a proposal"""
    async with get_db() as conn:
        async with conn.execute(
            """
            SELECT * FROM voting_invites WHERE proposal_id = ?
            """,
            (proposal_id,)
        ) as cursor:
            rows = await cursor.fetchall()
            if rows:
                column_names = [desc[0] for desc in cursor.description]
                return [{column_names[i]: row[i] for i in range(len(row))} for row in rows]
            return []


async def add_voting_invite(proposal_id, voter_id):
    """Record that a voter has been invited to vote on a proposal"""
    async with get_db() as conn:
        await conn.execute(
            """
            INSERT INTO voting_invites (proposal_id, voter_id)
            VALUES (?, ?)
            ON CONFLICT(proposal_id, voter_id) DO NOTHING
            """,
            (proposal_id, voter_id)
        )
        await conn.commit()
        return True


async def record_vote(
    user_id: int,
    proposal_id: int,
    vote_data: str,
    is_abstain: bool = False, # New from more recent schema
    tokens_invested: Optional[int] = None # New
):
    """Record or update a user's vote. Includes is_abstain and tokens_invested."""
    vote_json = json.dumps(vote_data) # Assuming vote_data might be complex for some types, ensure it's serializable if not already string.
                                     # If vote_data is always a simple string (like an option ID), direct usage is fine.
    current_time_utc = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S.%f')

    # In the current schema, votes are unique by (proposal_id, user_id), so this is an INSERT or UPDATE.
    # However, the old record_vote had an INSERT or UPDATE logic based on SELECT first.
    # The most robust way with UNIQUE constraint is INSERT ... ON CONFLICT DO UPDATE.

    sql_insert_vote = """
        INSERT INTO votes (proposal_id, user_id, vote_data, timestamp, is_abstain, tokens_invested)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(proposal_id, user_id) DO UPDATE SET
            vote_data = excluded.vote_data,
            timestamp = excluded.timestamp,
            is_abstain = excluded.is_abstain,
            tokens_invested = excluded.tokens_invested
    """
    params = (proposal_id, user_id, vote_json, current_time_utc, is_abstain, tokens_invested)

    try:
        async with get_db() as conn:
            await conn.execute(sql_insert_vote, params)
            await conn.commit()
            print(f"DEBUG: Vote recorded/updated for P#{proposal_id} U#{user_id}. Abstain: {is_abstain}, Tokens: {tokens_invested}, Data: {vote_json[:50]}")
            return True
    except Exception as e:
        print(f"ERROR recording/updating vote for P:{proposal_id} U:{user_id}: {e}")
        # traceback.print_exc()
        return False


# ========================
# 🔹 WARNING SYSTEM
# ========================

async def add_warning(server_id, user_id, moderator_id, reason):
    """Add a warning for a user and return the total warning count"""
    async with get_db() as conn:
        await conn.execute(
            """
            INSERT INTO warnings (server_id, user_id, moderator_id, reason, level)
            VALUES (?, ?, ?, ?, (
                SELECT COUNT(*) + 1 FROM warnings
                WHERE server_id = ? AND user_id = ?
            ))
            """,
            (server_id, user_id, moderator_id, reason, server_id, user_id)
        )
        await conn.commit()

        # Get the new warning count
        async with conn.execute(
            "SELECT COUNT(*) FROM warnings WHERE server_id = ? AND user_id = ?",
            (server_id, user_id)
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0


async def get_user_warnings(server_id, user_id):
    """Get all warnings for a user"""
    async with get_db() as conn:
        async with conn.execute(
            """
            SELECT w.*, u.username as moderator_name
            FROM warnings w
            LEFT JOIN users u ON w.moderator_id = u.user_id
            WHERE w.server_id = ? AND w.user_id = ?
            ORDER BY w.timestamp DESC
            """,
            (server_id, user_id)
        ) as cursor:
            rows = await cursor.fetchall()
            if rows:
                column_names = [desc[0] for desc in cursor.description]
                return [{column_names[i]: row[i] for i in range(len(row))} for row in rows]
            return []


async def clear_warnings(server_id, user_id):
    """Clear all warnings for a user"""
    async with get_db() as conn:
        await conn.execute(
            "DELETE FROM warnings WHERE server_id = ? AND user_id = ?",
            (server_id, user_id)
        )
        await conn.commit()


# ========================
# 🔹 TEMPORARY MODERATION
# ========================

async def add_temp_moderation(server_id, user_id, moderator_id, action_type, reason, expires_at):
    """Add a temporary moderation action"""
    async with get_db() as conn:
        await conn.execute(
            """
            INSERT INTO temp_moderation
            (server_id, user_id, moderator_id, action_type, reason, expires_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (server_id, user_id, moderator_id, action_type, reason, expires_at.isoformat())
        )
        await conn.commit()


async def get_expired_moderations(action_type=None):
    """Get all expired moderation actions"""
    now = datetime.now().isoformat()

    async with get_db() as conn:
        if action_type:
            query = """
                SELECT * FROM temp_moderation
                WHERE expires_at <= ? AND action_type = ?
                """
            params = (now, action_type)
        else:
            query = "SELECT * FROM temp_moderation WHERE expires_at <= ?"
            params = (now,)

        async with conn.execute(query, params) as cursor:
            rows = await cursor.fetchall()
            if rows:
                column_names = [desc[0] for desc in cursor.description]
                return [{column_names[i]: row[i] for i in range(len(row))} for row in rows]
            return []


async def remove_temp_moderation(action_id):
    """Remove a temporary moderation action after it's been handled"""
    async with get_db() as conn:
        await conn.execute(
            "DELETE FROM temp_moderation WHERE action_id = ?",
            (action_id,)
        )
        await conn.commit()


# ========================
# 🔹 DATABASE INITIALIZATION
# ========================

# --- helper ---------------------------------------------------------------
async def _ensure_column(conn: aiosqlite.Connection, table: str, column_def: str):
    """
    Add `column_def` (e.g. 'results_pending_announcement INTEGER DEFAULT 0')
    to `table` if the column is not already present.
    """
    col_name = column_def.split()[0]
    async with conn.execute(f"PRAGMA table_info({table});") as cur:
        existing = {row[1] for row in await cur.fetchall()}
    if col_name not in existing:
        await conn.execute(f"ALTER TABLE {table} ADD COLUMN {column_def}")


@asynccontextmanager
async def get_db(db_file: str = "bot_database.db"):
    # Add detect_types for automatic datetime parsing
    # Add json_deserializer for automatic JSON column parsing (like vote_data)
    db_conn = await aiosqlite.connect(
        db_file,
        detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES,  # <-- Use sqlite3 here
    )
    # Set row factory for easier column name access
    db_conn.row_factory = aiosqlite.Row
    try:
        # YIELD THE RENAMED VARIABLE
        yield db_conn  # <--- Rename the connection object here
    finally:
        if db_conn:  # Check if connection was successfully created
            await db_conn.close()


async def _ensure_column(conn: aiosqlite.Connection, table: str, column_def: str):
    """
    Add `column_def` (e.g. 'results_pending_announcement INTEGER DEFAULT 0')
    to `table` if the column is not already present. Handles CURRENT_TIMESTAMP default.
    """
    # 1. Parse the column definition
    parts = column_def.split()
    col_name = parts[0]
    col_type = parts[1]
    has_default = "DEFAULT" in parts
    is_current_timestamp_default = has_default and "CURRENT_TIMESTAMP" in column_def

    # 2. Check if column exists
    async with conn.execute(f"PRAGMA table_info({table});") as cur:
        existing_columns = {row[1] for row in await cur.fetchall()}

    if col_name not in existing_columns:
        print(
            f"Column '{col_name}' not found in table '{table}'. Attempting to add.")

        try:
            # If it's a CURRENT_TIMESTAMP default, add without default, then update
            if is_current_timestamp_default:
                # Add the column without the default clause first
                await conn.execute(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type}")
                print(
                    f"Added column '{col_name}' to '{table}' without default.")

                # Update existing rows to set the timestamp (where it's NULL)
                await conn.execute(f"UPDATE {table} SET {col_name} = CURRENT_TIMESTAMP WHERE {col_name} IS NULL")
                print(
                    f"Updated existing rows in '{table}.{col_name}' with CURRENT_TIMESTAMP.")

                # Note: We are NOT adding the DEFAULT constraint via ALTER TABLE
                # as this is complex and version-dependent in SQLite.
                # Ensure your INSERT statements handle setting this value or rely on table creation DEFAULT.

            # Otherwise (constant default or no default), add the column with the provided definition
            else:
                await conn.execute(f"ALTER TABLE {table} ADD COLUMN {column_def}")
                print(f"Added column '{column_def}' to '{table}'.")

            # Changes are committed in init_db, no commit here needed
            # await conn.commit() # Removed, init_db handles commit

        except sqlite3.OperationalError as e:
            print(f"SQLITE ERROR adding column '{col_name}' to '{table}': {e}")
            # Re-raise the error if it's not the specific 'duplicate column' error
            # (which shouldn't happen here due to the initial check, but defensive coding)
            # If the error is "duplicate column", it means our check was slightly off, but the column exists.
            # For other OperationalErrors (like 'Cannot add a column...'), we should let it propagate if it's not the one we handled.
            if "duplicate column" not in str(e):
                raise e
            else:
                print(
                    f"Warning: Attempted to add duplicate column '{col_name}' to '{table}'. It already exists.")


# --- complete, self‑contained init_db -------------------------------------
async def init_db() -> None:
    """Create all tables (idempotently) and ensure necessary columns exist."""
    async with get_db() as conn:
        await conn.execute("PRAGMA foreign_keys = ON")

        # Core tables - Standardized Definitions
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS servers (
                server_id       INTEGER PRIMARY KEY,
                server_name     TEXT NOT NULL,
                owner_id        INTEGER NOT NULL,
                member_count    INTEGER,
                created_at      TEXT NOT NULL -- Store as ISO 8601 string
            )
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id       INTEGER PRIMARY KEY,
                username      TEXT NOT NULL,
                discriminator TEXT,
                last_updated  TEXT NOT NULL -- Store as ISO 8601 string
            )
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                setting_id INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id INTEGER NOT NULL UNIQUE,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                FOREIGN KEY (server_id) REFERENCES servers(server_id) ON DELETE CASCADE
            )
        """)

        await conn.execute(CREATE_CAMPAIGNS_TABLE) # Defined as a global constant
        await conn.execute(CREATE_USER_CAMPAIGN_PARTICIPATION_TABLE) # Defined as a global constant

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS proposals (
                proposal_id INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id INTEGER NOT NULL,
                proposer_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                description TEXT,
                voting_mechanism TEXT NOT NULL,
                deadline TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('Pending', 'Voting', 'Closed', 'Cancelled', 'Pending Approval', 'Rejected')) DEFAULT 'Pending Approval',
                requires_approval BOOLEAN DEFAULT TRUE,
                approved_by INTEGER,
                hyperparameters TEXT,
                creation_timestamp TEXT NOT NULL,
                last_updated_timestamp TEXT NOT NULL,
                results_message_id INTEGER,
                results_channel_id INTEGER,
                results_pending BOOLEAN DEFAULT FALSE,
                vote_tracking_message_id INTEGER,
                campaign_id INTEGER,
                scenario_order INTEGER,
                FOREIGN KEY (server_id) REFERENCES servers(server_id) ON DELETE CASCADE,
                FOREIGN KEY (proposer_id) REFERENCES users(user_id) ON DELETE SET NULL,
                FOREIGN KEY (approved_by) REFERENCES users(user_id) ON DELETE SET NULL,
                FOREIGN KEY (campaign_id) REFERENCES campaigns(campaign_id) ON DELETE SET NULL
            )
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS proposal_options (
                option_id INTEGER PRIMARY KEY AUTOINCREMENT,
                proposal_id INTEGER NOT NULL,
                option_text TEXT NOT NULL,
                option_order INTEGER,
                FOREIGN KEY (proposal_id) REFERENCES proposals(proposal_id) ON DELETE CASCADE,
                UNIQUE (proposal_id, option_text)
            )
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS votes (
                vote_id INTEGER PRIMARY KEY AUTOINCREMENT,
                proposal_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                vote_data TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                is_abstain BOOLEAN DEFAULT FALSE,
                tokens_invested INTEGER,
                FOREIGN KEY (proposal_id) REFERENCES proposals(proposal_id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
                UNIQUE (proposal_id, user_id)
            )
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS constitutional_variables (
                variable_id INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                value TEXT NOT NULL,
                type TEXT NOT NULL,
                description TEXT,
                last_updated_timestamp TEXT NOT NULL,
                UNIQUE (server_id, name),
                FOREIGN KEY (server_id) REFERENCES servers(server_id) ON DELETE CASCADE
            )
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS voting_invites (
                invite_id INTEGER PRIMARY KEY AUTOINCREMENT,
                proposal_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                dm_message_id INTEGER,
                status TEXT NOT NULL CHECK(status IN ('sent', 'failed', 'pending')) DEFAULT 'pending',
                timestamp TEXT NOT NULL,
                UNIQUE (proposal_id, user_id),
                FOREIGN KEY (proposal_id) REFERENCES proposals(proposal_id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
            )
        """)

        await conn.execute(CREATE_PENDING_PROPOSAL_NOTIFICATIONS_TABLE) # Assumed to be a global constant

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS proposal_results (
                proposal_id INTEGER PRIMARY KEY,
                results     TEXT    NOT NULL,
                FOREIGN KEY (proposal_id) REFERENCES proposals(proposal_id) ON DELETE CASCADE
            )
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS proposal_notes (
                note_id     INTEGER PRIMARY KEY AUTOINCREMENT,
                proposal_id INTEGER NOT NULL,
                note_type   TEXT    NOT NULL,
                note_text   TEXT    NOT NULL,
                created_at  TEXT NOT NULL,
                FOREIGN KEY (proposal_id) REFERENCES proposals(proposal_id) ON DELETE CASCADE
            )
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS warnings (
                warning_id   INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id    INTEGER NOT NULL,
                user_id      INTEGER NOT NULL,
                moderator_id INTEGER NOT NULL,
                reason       TEXT    NOT NULL,
                level        INTEGER NOT NULL,
                timestamp    TEXT NOT NULL,
                FOREIGN KEY (server_id) REFERENCES servers(server_id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
                FOREIGN KEY (moderator_id) REFERENCES users(user_id) ON DELETE SET NULL
            )
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS temp_moderation (
                action_id   INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id   INTEGER NOT NULL,
                user_id     INTEGER NOT NULL,
                moderator_id INTEGER NOT NULL,
                action_type TEXT    NOT NULL,
                reason      TEXT,
                expires_at  TEXT NOT NULL,
                FOREIGN KEY (server_id) REFERENCES servers(server_id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
                FOREIGN KEY (moderator_id) REFERENCES users(user_id) ON DELETE SET NULL
            )
        """)

        # Ensure columns - Standardized Calls
        await _ensure_column(conn, 'servers', 'created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP')
        await _ensure_column(conn, 'users', 'last_updated TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP')

        await _ensure_column(conn, 'proposals', 'requires_approval BOOLEAN DEFAULT TRUE')
        await _ensure_column(conn, 'proposals', 'approved_by INTEGER')
        await _ensure_column(conn, 'proposals', 'hyperparameters TEXT')
        await _ensure_column(conn, 'proposals', 'creation_timestamp TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP')
        await _ensure_column(conn, 'proposals', 'last_updated_timestamp TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP')
        await _ensure_column(conn, 'proposals', 'results_message_id INTEGER')
        await _ensure_column(conn, 'proposals', 'results_channel_id INTEGER')
        await _ensure_column(conn, 'proposals', 'results_pending BOOLEAN DEFAULT FALSE')
        await _ensure_column(conn, 'proposals', 'vote_tracking_message_id INTEGER')
        await _ensure_column(conn, 'proposals', 'campaign_id INTEGER')
        await _ensure_column(conn, 'proposals', 'scenario_order INTEGER')

        await _ensure_column(conn, 'proposal_options', 'option_order INTEGER')

        await _ensure_column(conn, 'votes', 'timestamp TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP')
        await _ensure_column(conn, 'votes', 'is_abstain BOOLEAN DEFAULT FALSE')
        await _ensure_column(conn, 'votes', 'tokens_invested INTEGER')

        # --- Special handling for votes.user_id migration ---
        cursor = await conn.execute("PRAGMA table_info(votes)")
        columns_in_votes = [row[1] for row in await cursor.fetchall()]

        if 'user_id' not in columns_in_votes:
            print("Column 'user_id' not found in 'votes'. Adding and attempting to populate from 'voter_id'.")
            try:
                await conn.execute("ALTER TABLE votes ADD COLUMN user_id INTEGER")
                print("Added column 'user_id INTEGER' to 'votes'.")
                if 'voter_id' in columns_in_votes:
                    await conn.execute("UPDATE votes SET user_id = voter_id WHERE voter_id IS NOT NULL AND user_id IS NULL")
                    print("Populated 'votes.user_id' from 'votes.voter_id' where possible.")

                    async with conn.execute("SELECT COUNT(*) FROM votes WHERE user_id IS NULL") as c:
                        null_user_id_count = (await c.fetchone())[0]
                        if null_user_id_count > 0:
                            print(f"WARNING: {null_user_id_count} rows in 'votes' still have NULL user_id after migration from voter_id.")
                else:
                    print("'voter_id' column not found in 'votes'. Cannot populate 'user_id' from it. Existing votes may lack user association if table was not empty.")
            except sqlite3.OperationalError as e_add_user_id:
                print(f"SQLite error during special handling for votes.user_id: {e_add_user_id}")
        else:
            print("Column 'user_id' already exists in 'votes'. Skipping special add/populate logic.")
        # --- End of special handling for votes.user_id ---

        await _ensure_column(conn, 'votes', 'user_id INTEGER NOT NULL') # Ensure user_id consistency if old schema used voter_id

        await _ensure_column(conn, 'constitutional_variables', 'last_updated_timestamp TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP')

        await _ensure_column(conn, 'voting_invites', 'dm_message_id INTEGER')
        await _ensure_column(conn, 'voting_invites', "status TEXT NOT NULL CHECK(status IN ('sent', 'failed', 'pending')) DEFAULT 'pending'")
        await _ensure_column(conn, 'voting_invites', 'timestamp TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP')

        await _ensure_column(conn, 'proposal_notes', 'created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP')
        await _ensure_column(conn, 'warnings', 'timestamp TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP')
        await _ensure_column(conn, 'temp_moderation', 'expires_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP') # Assuming it needs a default for NOT NULL

        await _ensure_column(conn, 'campaigns', 'creation_timestamp TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP')
        await _ensure_column(conn, 'user_campaign_participation', 'last_updated_timestamp TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP')

        await conn.commit()
        print("Database initialized and tables created/verified (standardized init_db).")

    # fix_all_timestamps should be called cautiously and separately if needed.
    # await fix_all_timestamps()
    print("DB Initialization and column checks complete (standardized init_db).")

# --- Data Modification and Retrieval Functions ---
# Make sure global constants CREATE_CAMPAIGNS_TABLE, CREATE_USER_CAMPAIGN_PARTICIPATION_TABLE,
# and CREATE_PENDING_PROPOSAL_NOTIFICATIONS_TABLE are defined above this function if used by init_db.

async def add_proposal_option(proposal_id, option_text, option_order):
    """Add an option for a proposal"""
    async with get_db() as conn:
        await conn.execute(
            """
            INSERT INTO proposal_options (proposal_id, option_text, option_order)
            VALUES (?, ?, ?)
            ON CONFLICT(proposal_id, option_text) DO UPDATE SET option_order = EXCLUDED.option_order
            """,
            (proposal_id, option_text, option_order)
        )
        await conn.commit()
        return True

async def add_proposal_options(proposal_id, options):
    """Add multiple options for a proposal"""
    async with get_db() as conn:
        for i, option in enumerate(options):
            await conn.execute(
                """
                INSERT INTO proposal_options (proposal_id, option_text, option_order)
                VALUES (?, ?, ?)
                ON CONFLICT(proposal_id, option_text) DO UPDATE SET option_order = EXCLUDED.option_order
                """,
                (proposal_id, option, i)
            )
        await conn.commit()
        return True

async def get_proposal_options(proposal_id):
    """Get all options for a proposal, ordered by option_order"""
    async with get_db() as conn:
        async with conn.execute(
            """
            SELECT option_text FROM proposal_options
            WHERE proposal_id = ?
            ORDER BY option_order
            """,
            (proposal_id,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [row[0] for row in rows] if rows else []


async def get_proposals_with_pending_announcements():
    """Get all proposals with pending result announcements (from 100% voting)"""
    async with get_db() as conn:
        # Debug: Print all proposals to see their status and results_pending_announcement values
        print("DEBUG: Checking for proposals with pending announcements...")

        # First check if the column exists
        cursor = await conn.execute("PRAGMA table_info(proposals)")
        columns = await cursor.fetchall()
        column_names = [column[1] for column in columns]

        if "results_pending_announcement" not in column_names:
            print("DEBUG: results_pending_announcement column does not exist in proposals table")
            return []

        # Get all proposals to debug
        async with conn.execute(
            """
            SELECT id, proposal_id, status, results_pending_announcement
            FROM proposals
            ORDER BY id DESC
            """
        ) as debug_cursor:
            debug_rows = await debug_cursor.fetchall()
            for row in debug_rows:
                # Use id if proposal_id is None
                proposal_identifier = row[1] if row[1] is not None else row[0]
                # print(f"DEBUG: Proposal {proposal_identifier} (ID={row[0]}): status={row[2]}, results_pending_announcement={row[3]}")

        # Try with different status values since we're updating to Passed/Failed now
        # Also check both id and proposal_id columns
        async with conn.execute(
            """
            SELECT * FROM proposals
            WHERE (status = 'Closed' OR status = 'Passed' OR status = 'Failed')
            AND results_pending_announcement = 1
            ORDER BY id DESC
            """
        ) as cursor:
            rows = await cursor.fetchall()
            if rows:
                column_names = [desc[0] for desc in cursor.description]
                proposals = [{column_names[i]: row[i] for i in range(len(row))} for row in rows]
                print(f"DEBUG: Found {len(proposals)} proposals with pending announcements")

                # Ensure each proposal has a proposal_id
                for proposal in proposals:
                    if proposal.get('proposal_id') is None and 'id' in proposal:
                        proposal['proposal_id'] = proposal['id']

                return proposals

            print("DEBUG: No proposals found with pending announcements")

            # Try a direct SQL approach as a fallback
            print("DEBUG: Trying fallback query for pending announcements")
            async with conn.execute(
                """
                SELECT * FROM proposals
                WHERE results_pending_announcement = 1
                ORDER BY id DESC
                """
            ) as fallback_cursor:
                fallback_rows = await fallback_cursor.fetchall()
                if fallback_rows:
                    column_names = [desc[0] for desc in fallback_cursor.description]
                    proposals = [{column_names[i]: row[i] for i in range(len(row))} for row in fallback_rows]
                    print(f"DEBUG: Found {len(proposals)} proposals with pending announcements using fallback query")

                    # Ensure each proposal has a proposal_id
                    for proposal in proposals:
                        if proposal.get('proposal_id') is None and 'id' in proposal:
                            proposal['proposal_id'] = proposal['id']

                    return proposals

            return []

# In conn.py
async def get_invited_voters_ids(proposal_id):
    """Get a list of user IDs who have been invited to vote on a proposal"""
    async with get_db() as conn:
        # Try to get invites using both id and proposal_id
        async with conn.execute(
            """
            SELECT voter_id FROM voting_invites WHERE
            proposal_id = ? OR proposal_id IN (SELECT id FROM proposals WHERE id = ?)
            """,
            (proposal_id, proposal_id)
        ) as cursor:
            rows = await cursor.fetchall()
            # Return a set for efficient lookup, or list if order matters (list is fine)
            return [row[0] for row in rows] if rows else []


# In conn.py
async def get_proposal_results_json(proposal_id):
    """Get the raw JSON string results of a proposal vote"""
    async with get_db() as conn:
        # Try to get results using both id and proposal_id
        async with conn.execute(
            "SELECT results FROM proposal_results WHERE proposal_id = ? OR proposal_id IN (SELECT id FROM proposals WHERE id = ?)",
            (proposal_id, proposal_id)
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None # Returns the JSON string or None

async def fix_malformed_timestamps():
    """Fix any malformed timestamps in the database by standardizing their format"""
    print("Checking and fixing malformed timestamps in the database...")
    async with get_db() as conn:
        # Check proposals table
        async with conn.execute("SELECT * FROM proposals") as cursor:
            columns = [desc[0] for desc in cursor.description]
            has_proposal_id = 'proposal_id' in columns
            has_id = 'id' in columns
            has_created_at = 'created_at' in columns
            has_deadline = 'deadline' in columns
            proposals = await cursor.fetchall()

            for proposal in proposals:
                # Use the correct column names based on what's available
                try:
                    # Try dictionary access first (aiosqlite.Row)
                    if has_proposal_id:
                        proposal_id = proposal['proposal_id']
                    elif has_id:
                        proposal_id = proposal['id']
                    else:
                        # Skip if we can't identify the proposal
                        print("Warning: Could not identify proposal ID column")
                        continue

                    created_at = proposal['created_at'] if has_created_at else None
                    deadline = proposal['deadline'] if has_deadline else None
                except (TypeError, KeyError):
                    # Fall back to tuple indexing - need to determine column positions
                    id_index = columns.index('proposal_id') if has_proposal_id else columns.index('id') if has_id else -1
                    created_at_index = columns.index('created_at') if has_created_at else -1
                    deadline_index = columns.index('deadline') if has_deadline else -1

                    if id_index == -1:
                        # Skip if we can't identify the proposal
                        print("Warning: Could not identify proposal ID column")
                        continue

                    proposal_id = proposal[id_index]
                    created_at = proposal[created_at_index] if created_at_index != -1 else None
                    deadline = proposal[deadline_index] if deadline_index != -1 else None

                # Fix created_at if needed
                if created_at and isinstance(created_at, str) and 'T' in created_at:
                    # Convert ISO format to standard SQLite timestamp format
                    try:
                        dt = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                        new_timestamp = dt.strftime('%Y-%m-%d %H:%M:%S')
                        id_column = 'proposal_id' if has_proposal_id else 'id'
                        await conn.execute(
                            f"UPDATE proposals SET created_at = ? WHERE {id_column} = ?",
                            (new_timestamp, proposal_id)
                        )
                        print(f"Fixed created_at timestamp for proposal {proposal_id}: {created_at} -> {new_timestamp}")
                    except Exception as e:
                        print(f"Error fixing created_at for proposal {proposal_id}: {e}")

                # Fix deadline if needed
                if deadline and isinstance(deadline, str) and 'T' in deadline:
                    # Convert ISO format to standard SQLite timestamp format
                    try:
                        dt = datetime.fromisoformat(deadline.replace('Z', '+00:00'))
                        new_timestamp = dt.strftime('%Y-%m-%d %H:%M:%S')
                        id_column = 'proposal_id' if has_proposal_id else 'id'
                        await conn.execute(
                            f"UPDATE proposals SET deadline = ? WHERE {id_column} = ?",
                            (new_timestamp, proposal_id)
                        )
                        print(f"Fixed deadline timestamp for proposal {proposal_id}: {deadline} -> {new_timestamp}")
                    except Exception as e:
                        print(f"Error fixing deadline for proposal {proposal_id}: {e}")

        # Check if votes table exists and has the expected columns
        try:
            # First check if the votes table exists
            async with conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='votes'") as table_cursor:
                table_exists = await table_cursor.fetchone()

            if table_exists:
                # Check which columns exist in the votes table
                async with conn.execute("PRAGMA table_info(votes)") as col_cursor:
                    vote_columns = [col[1] for col in await col_cursor.fetchall()]

                if 'timestamp' in vote_columns:
                    # Determine the primary key column name
                    id_column = 'vote_id' if 'vote_id' in vote_columns else 'id' if 'id' in vote_columns else None

                    if id_column:
                        # Get all votes
                        async with conn.execute(f"SELECT {id_column}, timestamp FROM votes") as cursor:
                            votes = await cursor.fetchall()
                            for vote in votes:
                                # Get the ID using either indexing or dictionary access
                                try:
                                    # Try dictionary access first (aiosqlite.Row)
                                    vote_id = vote[id_column]
                                    timestamp = vote['timestamp']
                                except (TypeError, KeyError):
                                    # Fall back to tuple indexing
                                    vote_id = vote[0]  # Assuming ID is the first column
                                    timestamp = vote[1]  # Assuming timestamp is the second column

                                if timestamp and isinstance(timestamp, str) and 'T' in timestamp:
                                    try:
                                        dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                                        new_timestamp = dt.strftime('%Y-%m-%d %H:%M:%S')
                                        await conn.execute(
                                            f"UPDATE votes SET timestamp = ? WHERE {id_column} = ?",
                                            (new_timestamp, vote_id)
                                        )
                                        print(f"Fixed timestamp for vote {vote_id}: {timestamp} -> {new_timestamp}")
                                    except Exception as e:
                                        print(f"Error fixing timestamp for vote {vote_id}: {e}")
                    else:
                        print("Warning: Could not find primary key column in votes table")
        except Exception as e:
            print(f"Error checking votes table: {e}")

        # Check warnings table with similar safeguards
        try:
            # First check if the warnings table exists
            async with conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='warnings'") as table_cursor:
                table_exists = await table_cursor.fetchone()

            if table_exists:
                # Check which columns exist in the warnings table
                async with conn.execute("PRAGMA table_info(warnings)") as col_cursor:
                    warning_columns = [col[1] for col in await col_cursor.fetchall()]

                if 'timestamp' in warning_columns:
                    # Determine the primary key column name
                    id_column = 'warning_id' if 'warning_id' in warning_columns else 'id' if 'id' in warning_columns else None

                    if id_column:
                        # Get all warnings
                        async with conn.execute(f"SELECT {id_column}, timestamp FROM warnings") as cursor:
                            warnings = await cursor.fetchall()
                            for warning in warnings:
                                # Get the ID using either indexing or dictionary access
                                try:
                                    # Try dictionary access first (aiosqlite.Row)
                                    warning_id = warning[id_column]
                                    timestamp = warning['timestamp']
                                except (TypeError, KeyError):
                                    # Fall back to tuple indexing
                                    warning_id = warning[0]  # Assuming ID is the first column
                                    timestamp = warning[1]  # Assuming timestamp is the second column

                                if timestamp and isinstance(timestamp, str) and 'T' in timestamp:
                                    try:
                                        dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                                        new_timestamp = dt.strftime('%Y-%m-%d %H:%M:%S')
                                        await conn.execute(
                                            f"UPDATE warnings SET timestamp = ? WHERE {id_column} = ?",
                                            (new_timestamp, warning_id)
                                        )
                                        print(f"Fixed timestamp for warning {warning_id}: {timestamp} -> {new_timestamp}")
                                    except Exception as e:
                                        print(f"Error fixing timestamp for warning {warning_id}: {e}")
                    else:
                        print("Warning: Could not find primary key column in warnings table")
        except Exception as e:
            print(f"Error checking warnings table: {e}")

        # Check temp_moderation table with similar safeguards
        try:
            # First check if the temp_moderation table exists
            async with conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='temp_moderation'") as table_cursor:
                table_exists = await table_cursor.fetchone()

            if table_exists:
                # Check which columns exist in the temp_moderation table
                async with conn.execute("PRAGMA table_info(temp_moderation)") as col_cursor:
                    moderation_columns = [col[1] for col in await col_cursor.fetchall()]

                if 'expires_at' in moderation_columns:
                    # Determine the primary key column name
                    id_column = 'action_id' if 'action_id' in moderation_columns else 'id' if 'id' in moderation_columns else None

                    if id_column:
                        # Get all moderation actions
                        async with conn.execute(f"SELECT {id_column}, expires_at FROM temp_moderation") as cursor:
                            actions = await cursor.fetchall()
                            for action in actions:
                                # Get the ID using either indexing or dictionary access
                                try:
                                    # Try dictionary access first (aiosqlite.Row)
                                    action_id = action[id_column]
                                    expires_at = action['expires_at']
                                except (TypeError, KeyError):
                                    # Fall back to tuple indexing
                                    action_id = action[0]  # Assuming ID is the first column
                                    expires_at = action[1]  # Assuming expires_at is the second column

                                if expires_at and isinstance(expires_at, str) and 'T' in expires_at:
                                    try:
                                        dt = datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
                                        new_timestamp = dt.strftime('%Y-%m-%d %H:%M:%S')
                                        await conn.execute(
                                            f"UPDATE temp_moderation SET expires_at = ? WHERE {id_column} = ?",
                                            (new_timestamp, action_id)
                                        )
                                        print(f"Fixed expires_at timestamp for moderation action {action_id}: {expires_at} -> {new_timestamp}")
                                    except Exception as e:
                                        print(f"Error fixing expires_at for moderation action {action_id}: {e}")
                    else:
                        print("Warning: Could not find primary key column in temp_moderation table")
        except Exception as e:
            print(f"Error checking temp_moderation table: {e}")

        # Commit all changes
        await conn.commit()

    print("Timestamp fixing complete.")

async def delete_proposal_data(proposal_id: int):
    """Deletes a proposal and all its associated data (options, votes)."""
    try:
        async with get_db() as db:
            # CASCADE should handle options and votes, but explicit deletion can be a safeguard
            # await db.execute("DELETE FROM proposal_options WHERE proposal_id = ?", (proposal_id,))
            # await db.execute("DELETE FROM votes WHERE proposal_id = ?", (proposal_id,))
            await db.execute("DELETE FROM proposals WHERE proposal_id = ?", (proposal_id,))
            await db.commit()
            print(f"DEBUG: Deleted proposal data for P#{proposal_id}")
    except Exception as e:
        print(f"Error deleting proposal data for P#{proposal_id}: {e}")
        # traceback.print_exc() # Re-enable if needed for debugging

# --- Campaign Functions ---
async def create_campaign(guild_id: int, creator_id: int, title: str, description: Optional[str], total_tokens_per_voter: int, num_expected_scenarios: int) -> Optional[int]:
    """Creates a new campaign and returns its ID."""
    current_time_utc = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S.%f')
    try:
        async with get_db() as db:
            cursor = await db.execute(
                "INSERT INTO campaigns (guild_id, creator_id, title, description, total_tokens_per_voter, num_expected_scenarios, creation_timestamp, status, current_defined_scenarios) VALUES (?, ?, ?, ?, ?, ?, ?, 'setup', 0)",
                (guild_id, creator_id, title, description, total_tokens_per_voter, num_expected_scenarios, current_time_utc)
            )
            await db.commit()
            campaign_id = cursor.lastrowid
            print(f"DEBUG: Campaign created with ID {campaign_id}")
            return campaign_id
    except Exception as e:
        print(f"ERROR: Could not create campaign '{title}': {e}")
        # traceback.print_exc()
        return None

async def get_campaign(campaign_id: int) -> Optional[Dict[str, Any]]:
    """Fetches a campaign by its ID."""
    try:
        async with get_db() as db:
            async with db.execute("SELECT * FROM campaigns WHERE campaign_id = ?", (campaign_id,)) as cursor:
                campaign = await cursor.fetchone()
                return dict(campaign) if campaign else None
    except Exception as e:
        print(f"ERROR: Could not fetch campaign {campaign_id}: {e}")
        # traceback.print_exc()
        return None

async def update_campaign_status(campaign_id: int, status: str) -> bool:
    """Updates the status of a campaign."""
    allowed_statuses = ['setup', 'active', 'completed', 'archived']
    if status not in allowed_statuses:
        print(f"ERROR: Invalid status '{status}' for campaign {campaign_id}. Allowed: {allowed_statuses}")
        return False
    # current_time_utc = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S.%f') # If we add a last_updated field to campaigns
    try:
        async with get_db() as db:
            await db.execute("UPDATE campaigns SET status = ? WHERE campaign_id = ?", (status, campaign_id))
            await db.commit()
            print(f"DEBUG: Updated campaign {campaign_id} status to '{status}'")
            return True
    except Exception as e:
        print(f"ERROR: Could not update status for campaign {campaign_id}: {e}")
        # traceback.print_exc()
        return False

async def increment_defined_scenarios(campaign_id: int) -> Optional[int]:
    """Increments the count of defined scenarios for a campaign and returns the new count."""
    try:
        async with get_db() as db:
            await db.execute("UPDATE campaigns SET current_defined_scenarios = current_defined_scenarios + 1 WHERE campaign_id = ?", (campaign_id,))
            await db.commit()
            # Fetch the updated count
            async with db.execute("SELECT current_defined_scenarios FROM campaigns WHERE campaign_id = ?", (campaign_id,)) as cursor:
                result = await cursor.fetchone()
                if result:
                    new_count = result[0]
                    print(f"DEBUG: Incremented defined scenarios for campaign {campaign_id} to {new_count}")
                    return new_count
                return None
    except Exception as e:
        print(f"ERROR: Could not increment defined scenarios for campaign {campaign_id}: {e}")
        # traceback.print_exc()
        return None

async def get_campaigns_by_status(guild_id: int, status: str) -> List[Dict[str, Any]]:
    """Fetches all campaigns for a guild with a specific status."""
    try:
        async with get_db() as db:
            async with db.execute("SELECT * FROM campaigns WHERE guild_id = ? AND status = ? ORDER BY creation_timestamp DESC", (guild_id, status)) as cursor:
                campaigns = await cursor.fetchall()
                return [dict(campaign) for campaign in campaigns]
    except Exception as e:
        print(f"ERROR: Could not fetch campaigns for guild {guild_id} with status {status}: {e}")
        # traceback.print_exc()
        return []

# --- User Campaign Participation Functions ---
async def enroll_voter_in_campaign(campaign_id: int, user_id: int, total_tokens: int) -> bool:
    """Enrolls a voter in a campaign with their initial token allocation. Returns True if newly enrolled, False if already exists or error."""
    current_time_utc = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S.%f')
    try:
        async with get_db() as db:
            try:
                await db.execute(
                    "INSERT INTO user_campaign_participation (campaign_id, user_id, remaining_tokens, last_updated_timestamp) VALUES (?, ?, ?, ?)",
                    (campaign_id, user_id, total_tokens, current_time_utc)
                )
                await db.commit()
                print(f"DEBUG: Enrolled user {user_id} in campaign {campaign_id} with {total_tokens} tokens.")
                return True
            except sqlite3.IntegrityError:
                print(f"DEBUG: User {user_id} already enrolled in campaign {campaign_id}.")
                # Optionally, update tokens if re-enrollment logic is desired.
                # For now, just confirm they exist, maybe fetch current tokens to be sure.
                # existing_tokens = await get_user_remaining_tokens(campaign_id, user_id)
                # if existing_tokens != total_tokens: print(f"WARN: User {user_id} in campaign {campaign_id} has {existing_tokens} but new enrollment tried {total_tokens}")
                return False # Indicates not newly enrolled
    except Exception as e:
        print(f"ERROR: Could not enroll user {user_id} in campaign {campaign_id}: {e}")
        # traceback.print_exc()
        return False

async def get_user_remaining_tokens(campaign_id: int, user_id: int) -> Optional[int]:
    """Gets the remaining tokens for a user in a campaign."""
    try:
        async with get_db() as db:
            async with db.execute("SELECT remaining_tokens FROM user_campaign_participation WHERE campaign_id = ? AND user_id = ?", (campaign_id, user_id)) as cursor:
                row = await cursor.fetchone()
                return row['remaining_tokens'] if row else None
    except Exception as e:
        print(f"ERROR: Could not get remaining tokens for user {user_id} in campaign {campaign_id}: {e}")
        # traceback.print_exc()
        return None

async def update_user_remaining_tokens(campaign_id: int, user_id: int, tokens_spent: int) -> bool:
    """Updates a user's remaining tokens in a campaign after spending some. Returns True if successful."""
    current_time_utc = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S.%f')
    current_tokens = await get_user_remaining_tokens(campaign_id, user_id)
    if current_tokens is None:
        print(f"ERROR: User {user_id} not found in campaign {campaign_id} for token update.")
        return False
    if tokens_spent > current_tokens:
        print(f"ERROR: User {user_id} in campaign {campaign_id} tried to spend {tokens_spent} but only has {current_tokens}.")
        return False

    try:
        async with get_db() as db:
            await db.execute(
                "UPDATE user_campaign_participation SET remaining_tokens = remaining_tokens - ?, last_updated_timestamp = ? WHERE campaign_id = ? AND user_id = ?",
                (tokens_spent, current_time_utc, campaign_id, user_id)
            )
            await db.commit()
            # Verify update
            updated_tokens = await get_user_remaining_tokens(campaign_id, user_id)
            print(f"DEBUG: User {user_id} in campaign {campaign_id} spent {tokens_spent} tokens. New balance: {updated_tokens}")
            return True
    except Exception as e:
        print(f"ERROR: Could not update remaining tokens for user {user_id} in campaign {campaign_id}: {e}")
        # traceback.print_exc()
        return False

# --- Constitutional Variables Functions ---
async def get_constitutional_variables(server_id: int) -> Dict[str, Dict[str, Any]]:
    async with get_db() as conn:
        async with conn.execute(
            """
            SELECT variable_name, variable_value, variable_type, description
            FROM constitutional_variables
            WHERE server_id = ?
            """,
            (server_id,)
        ) as cursor:
            rows = await cursor.fetchall()
            return {
                row[0]: {
                    "value": row[1],
                    "type": row[2],
                    "description": row[3]
                } for row in rows
            }