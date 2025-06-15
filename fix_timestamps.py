# fix_malformed_timestamps_v2.py

import sqlite3
from datetime import datetime
import re
import sys

DATABASE_FILE = "bot_database.db"  # Adjust if your DB file is elsewhere


def fix_malformed_timestamps(db_path):
    """
    Connects to the SQLite database, finds strings in TIMESTAMP columns
    that do not contain a space (like YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS),
    and attempts to fix them by appending ' 00:00:00' or replacing 'T'.
    """
    try:
        # Connect WITHOUT detect_types to read timestamps as raw strings
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        print(f"Attempting to fix malformed timestamps in {db_path}...")

        # Define tables and columns to check that are expected to be TIMESTAMP
        # These tables and columns should exist after running init_db from the updated db.py
        tables_and_columns = {
            "proposals": ["created_at", "deadline"],
            "temp_moderation": ["expires_at"],
            "warnings": ["timestamp"],
            "proposal_notes": ["created_at"],
            "users": ["last_updated"],
            "voting_invites": ["invited_at"],
            "votes": ["timestamp"],
        }

        total_fixed_count = 0

        for table, columns in tables_and_columns.items():
            try:
                # Check if table exists (handles cases where tables might not be created yet)
                cursor.execute(
                    f"SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
                if not cursor.fetchone():
                    print(f"Table '{table}' not found in database. Skipping.")
                    continue

            except sqlite3.OperationalError as e:
                print(
                    f"Error checking table existence for '{table}': {e}. Skipping.")
                continue

            for column in columns:
                fixed_count = 0
                try:
                    # Check if column exists in table
                    cursor.execute(f"PRAGMA table_info({table});")
                    table_info = cursor.fetchall()
                    column_names = [info[1] for info in table_info]
                    if column not in column_names:
                        print(
                            f"Column '{column}' not found in table '{table}'. Skipping.")
                        continue

                    print(f"Checking table '{table}', column '{column}'...")

                    # Select rowid and the column value for all rows
                    # rowid is a universal hidden column guaranteed to be the PK alias if PK is INTEGER PRIMARY KEY
                    # Handle potential non-string values gracefully
                    cursor.execute(f"SELECT rowid, {column} FROM {table};")
                    rows = cursor.fetchall()

                    for rowid, value in rows:
                        # Only attempt to fix if it's a string value
                        if isinstance(value, str):
                            original_value = value
                            needs_fix = False
                            new_value = value

                            # Check for common malformed patterns that lack a space
                            if ' ' not in original_value:
                                needs_fix = True
                                # If it contains a 'T' (like ISO format), try replacing 'T' with a space
                                if 'T' in original_value:
                                    new_value = original_value.replace(
                                        'T', ' ')
                                    print(
                                        f"  Found ISO-like value '{original_value}' at rowid {rowid}. Replacing 'T' with space...")
                                # If it's just YYYY-MM-DD or some other format without space, append time
                                else:
                                    new_value = f"{original_value} 00:00:00"
                                    print(
                                        f"  Found string value without space '{original_value}' at rowid {rowid}. Appending time...")
                            # else: The string contains a space, assume it might be parsed correctly or is already okay

                            if needs_fix:
                                try:
                                    # Optional: Attempt to parse the *new_value* to confirm it's now valid before updating
                                    # This adds a layer of safety but might be slow for large DBs
                                    # datetime.strptime(new_value, '%Y-%m-%d %H:%M:%S')
                                    pass  # Skip parsing for speed, rely on the space check

                                    # If we reach here, we assume the new_value is correct
                                    cursor.execute(
                                        f"UPDATE {table} SET {column} = ? WHERE rowid = ?", (new_value, rowid))
                                    fixed_count += 1
                                except Exception as parse_err:
                                    print(
                                        f"  Could not parse or update fixed value '{new_value}' for rowid {rowid}: {parse_err}")

                    if fixed_count > 0:
                        conn.commit()
                        print(
                            f"  Fixed {fixed_count} entries in {table}.{column}")
                        total_fixed_count += fixed_count
                    else:
                        print(
                            f"  No strings needing space added found in {table}.{column}.")

                except sqlite3.OperationalError as e:
                    print(
                        f"  Database error while checking {table}.{column}: {e}")
                except Exception as e:
                    print(
                        f"  An unexpected error occurred while checking {table}.{column}: {e}")

        conn.close()
        print("\nTimestamp fixing process completed.")
        if total_fixed_count > 0:
            print(
                f"Successfully fixed {total_fixed_count} malformed timestamp entries.")
            print("You can now restart your Discord bot.")
        else:
            print("No malformed timestamp entries were found needing a space added.")
            print("If you still encounter the ValueError, the issue might be a different, less common format, or involve non-string data in a timestamp column.")

    except Exception as e:
        print(f"A critical error occurred during the fixing process: {e}")
        sys.exit(1)  # Exit with error code


if __name__ == "__main__":
    # --- IMPORTANT ---
    print("--- READ THIS BEFORE RUNNING ---")
    print(
        f"This script will attempt to modify your database file: {DATABASE_FILE}")
    print("1. MAKE A BACKUP COPY OF YOUR 'bot_database.db' FILE FIRST!")
    print("2. Ensure your Discord bot is NOT running.")
    print("---------------------------------")
    input("Press Enter to continue or Ctrl+C to cancel...")
    print("-" * 35)

    fix_malformed_timestamps(DATABASE_FILE)
