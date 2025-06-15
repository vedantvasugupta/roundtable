# Tech Context: Technologies and Setup

## Core Technologies

*   **Programming Language:** Python (version 3.10+ recommended)
*   **Discord API Library:** `discord.py` (version 2.0+), utilizing its features for commands, events, UI components (Views, Modals, Buttons), and background tasks.
*   **Database:** SQLite (single file database: `bot_database.db`)
*   **Async SQLite Driver:** `aiosqlite` for non-blocking database operations, crucial for use with `discord.py`'s async nature.

## Development Setup

*   **Virtual Environment:** A Python virtual environment (e.g., `venv`, `conda`) is strongly recommended to manage dependencies.
    *   The project root contains a `pyvenv.cfg` which suggests a venv is in use.
    *   Dependencies would typically be listed in a `requirements.txt` file (though one isn't explicitly visible in the provided file tree, it's a standard practice).
*   **Bot Token:** The Discord bot token is stored in a file named `bot_token.txt` at the project root. This file should be in `.gitignore` to prevent accidental commits.
*   **Execution:** The bot is started by running `python main.py` from the project root directory.

## Key Libraries and Their Usage (Implicit/Explicit)

*   **`discord.py`**: Core library for all Discord interactions.
    *   `commands.Bot`: Main bot instance.
    *   `discord.ui.View`, `discord.ui.Modal`, `discord.ui.Button`, `discord.ui.TextInput`: For building interactive UI components. **Note:** Modals are limited to a maximum of 5 `TextInput` components.
    *   `discord.Intents`: To specify which events the bot needs to listen to.
    *   `discord.ext.tasks`: For running background loops (e.g., checking deadlines).
*   **`aiosqlite`**: Asynchronous interface for SQLite database operations.
*   **`asyncio`**: Python's built-in library for asynchronous programming, used extensively by `discord.py` and `aiosqlite`.
*   **`json`**: Used for serializing and deserializing hyperparameter dictionaries into JSON strings for storage in the database.
*   **`datetime`**, **`timedelta`**: For handling proposal deadlines, durations, and timestamps.
*   **`sqlite3`** (standard library): While `aiosqlite` is the primary interface, `sqlite3`'s constants or type converters might occasionally be relevant (as seen in debugging timestamp issues).

## Database Schema Management

*   The database schema is defined and initialized within `db.py` in the `init_db()` async function. This function creates tables if they don't exist.
*   Schema migrations or alterations (like adding new columns) seem to be handled by ad-hoc scripts (e.g., `add_columns.py`, `add_guild_id.py`) or by modifying `init_db()` to include checks for existing columns before attempting to add them (as seen with `_ensure_column` helper in `db.py`).
    *   Recent additions include tables for `campaigns` and `user_campaign_participation`, and columns like `campaign_id`, `scenario_order` to `proposals`, and `tokens_invested`, `is_abstain` to `votes` to support the Weighted Campaign feature.
    *   Resolved issues with `NOT NULL` constraints on timestamp columns during `_ensure_column` by adding `DEFAULT CURRENT_TIMESTAMP`.

## Tool Usage Patterns

*   **Logging/Printing:** Standard Python `print()` statements are used for logging and debugging information to the console.
*   **Error Handling:** `try-except` blocks are used to catch and handle exceptions, with `traceback.print_exc()` often used for detailed error logging during development.