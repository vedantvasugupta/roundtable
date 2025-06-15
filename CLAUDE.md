# Discord Governance Bot - Development Guidelines

## Commands
- Run a single test: `python -m unittest test_bot.TestBot.test_name`
- Run all tests: `python -m unittest discover`
- Run the bot: `python main.py`
- Run database migrations: `python db_migration.py`

## Code Style
- **Imports**: Group standard libs, third-party libs (like discord), then local modules
- **Naming**: snake_case for functions and variables, CamelCase for classes
- **Async/Await**: All database operations and discord API calls should be async
- **Error Handling**: Use try/except blocks for API operations, provide clear error messages
- **Comments**: Add docstrings to all functions and classes using triple quotes
- **Type Hints**: Use where appropriate, especially for function parameters and returns
- **Discord Embeds**: Follow the established color scheme (blue for info, red for errors)
- **Database Access**: All database operations should go through the db.py module

## Project Structure
- **main.py**: Bot initialization and core event handlers
- **db.py**: Database operations
- **voting.py**: Voting mechanisms
- **proposals.py**: Proposal management
- **moderation.py**: Moderation commands
- **test_*.py**: Test files

## Testing
- Mock Discord objects and API calls when writing tests
- Run tests before committing changes
- Use unittest for test organization