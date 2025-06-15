import asyncio
import discord
from discord.ext import commands
import main

async def test_dummy_command():
    """Test the !dummy command"""
    try:
        print("Testing !dummy command...")

        # Create a mock context
        class MockContext:
            def __init__(self):
                self.guild = type('obj', (object,), {'id': 123456, 'name': 'Test Guild'})
                self.author = type('obj', (object,), {'id': 123456, 'name': 'Test User'})
                self.channel = type('obj', (object,), {'id': 123456, 'name': 'Test Channel'})
                self.message = type('obj', (object,), {'id': 123456, 'delete': lambda: None})
                self.send = lambda *args, **kwargs: print(f"Message sent: {args}")

        ctx = MockContext()

        # Call the dummy_proposal function
        await main.dummy_proposal(ctx)

        print("Test completed successfully!")
    except Exception as e:
        print(f"Error in test: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_dummy_command())
