import discord
from discord.ext import commands
import asyncio
import os
import sys
import traceback
import json
from datetime import datetime

# Add the current directory to the path so we can import our modules
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Import our modules
import db
import voting
import proposals
import moderation

# Define intents
intents = discord.Intents.default()
intents.messages = True
intents.guilds = True
intents.reactions = True
intents.members = True
intents.message_content = True

# Create bot instance
bot = commands.Bot(command_prefix="!", intents=intents)

# Configuration
TEST_CHANNEL_NAME = "bot-testing"
LOG_CHANNEL_NAME = "test-results"
TEST_GUILD_ID = None  # Set this to your guild ID

# Test cases
test_cases = [
    {
        "name": "Help Guide Test",
        "command": "!help_guide",
        "expected_response_contains": ["Bot Command Guide", "Constitutional Variables"],
        "description": "Tests that the help guide command returns the expected embed with constitutional variables section"
    },
    {
        "name": "Constitution Test",
        "command": "!constitution",
        "expected_response_contains": ["Server Constitution", "Examples"],
        "description": "Tests that the constitution command returns the expected embed with examples section"
    },
    {
        "name": "See Settings Test",
        "command": "!see_settings",
        "expected_response_contains": ["Server Settings Overview", "Governance Settings", "Constitutional Variables"],
        "description": "Tests that the see_settings command returns the expected embed with both settings categories"
    },
    {
        "name": "See Settings Governance Test",
        "command": "!see_settings governance",
        "expected_response_contains": ["Governance Settings", "admission_method", "removal_method"],
        "description": "Tests that the see_settings governance command returns the expected embed with governance settings"
    },
    {
        "name": "See Settings Constitutional Test",
        "command": "!see_settings constitutional",
        "expected_response_contains": ["Constitutional Variables", "proposal_requires_approval", "eligible_voters_role"],
        "description": "Tests that the see_settings constitutional command returns the expected embed with constitutional variables"
    },
    {
        "name": "Ping Test",
        "command": "!ping",
        "expected_response_contains": ["Pong"],
        "description": "Tests that the ping command returns the expected response"
    },
    {
        "name": "Command Not Found Test",
        "command": "!constiution",
        "expected_response_contains": ["not found", "constitution"],
        "description": "Tests that the command error handler suggests the correct command for typos"
    }
]

# Test results
test_results = {
    "passed": [],
    "failed": [],
    "errors": []
}

@bot.event
async def on_ready():
    """Called when the bot is ready"""
    print(f"🤖 Bot is online as {bot.user}")
    
    # Initialize database
    await db.init_db()
    
    # Get the test guild
    if TEST_GUILD_ID:
        guild = bot.get_guild(TEST_GUILD_ID)
    else:
        guild = bot.guilds[0]
    
    if not guild:
        print("❌ Could not find test guild")
        await bot.close()
        return
    
    print(f"📌 Running tests in {guild.name}")
    
    # Get or create test channel
    test_channel = discord.utils.get(guild.text_channels, name=TEST_CHANNEL_NAME)
    if not test_channel:
        # Create the channel
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=True, send_messages=False),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }
        test_channel = await guild.create_text_channel(name=TEST_CHANNEL_NAME, overwrites=overwrites)
        print(f"✅ Created #{TEST_CHANNEL_NAME} channel")
    
    # Get or create log channel
    log_channel = discord.utils.get(guild.text_channels, name=LOG_CHANNEL_NAME)
    if not log_channel:
        # Create the channel
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=True, send_messages=False),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }
        log_channel = await guild.create_text_channel(name=LOG_CHANNEL_NAME, overwrites=overwrites)
        print(f"✅ Created #{LOG_CHANNEL_NAME} channel")
    
    # Send test start message
    await log_channel.send(f"🧪 **Starting automated tests at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}**")
    
    # Run tests
    await run_tests(guild, test_channel, log_channel)
    
    # Send test summary
    summary = f"📊 **Test Summary**\n"
    summary += f"✅ Passed: {len(test_results['passed'])}\n"
    summary += f"❌ Failed: {len(test_results['failed'])}\n"
    summary += f"⚠️ Errors: {len(test_results['errors'])}\n"
    
    if test_results['failed']:
        summary += "\n**Failed Tests:**\n"
        for test in test_results['failed']:
            summary += f"• {test['name']}: {test['reason']}\n"
    
    if test_results['errors']:
        summary += "\n**Errors:**\n"
        for error in test_results['errors']:
            summary += f"• {error['name']}: {error['error']}\n"
    
    await log_channel.send(summary)
    
    # Close the bot
    await bot.close()

async def run_tests(guild, test_channel, log_channel):
    """Run all test cases"""
    for test_case in test_cases:
        try:
            # Send the command
            await test_channel.send(test_case['command'])
            
            # Wait for the bot's response
            def check(message):
                return message.author.id == bot.user.id and message.channel.id == test_channel.id
            
            try:
                response = await bot.wait_for('message', check=check, timeout=5.0)
                
                # Check if the response contains the expected text
                content = response.content
                if hasattr(response, 'embeds') and response.embeds:
                    embed = response.embeds[0]
                    content += f"\nEmbed Title: {embed.title}"
                    content += f"\nEmbed Description: {embed.description}"
                    for field in embed.fields:
                        content += f"\nField {field.name}: {field.value}"
                
                passed = True
                missing = []
                for expected in test_case['expected_response_contains']:
                    if expected not in content:
                        passed = False
                        missing.append(expected)
                
                if passed:
                    test_results['passed'].append(test_case)
                    await log_channel.send(f"✅ Test '{test_case['name']}' passed")
                else:
                    test_case['reason'] = f"Response did not contain: {', '.join(missing)}"
                    test_results['failed'].append(test_case)
                    await log_channel.send(f"❌ Test '{test_case['name']}' failed: {test_case['reason']}")
            
            except asyncio.TimeoutError:
                test_case['reason'] = "Timed out waiting for response"
                test_results['failed'].append(test_case)
                await log_channel.send(f"❌ Test '{test_case['name']}' failed: {test_case['reason']}")
        
        except Exception as e:
            error_info = {
                "name": test_case['name'],
                "error": str(e),
                "traceback": traceback.format_exc()
            }
            test_results['errors'].append(error_info)
            await log_channel.send(f"⚠️ Error in test '{test_case['name']}': {str(e)}")

# Run the bot
if __name__ == "__main__":
    # Check if TEST_GUILD_ID is set
    if not TEST_GUILD_ID:
        print("⚠️ TEST_GUILD_ID is not set. Using the first guild the bot is in.")
    
    # Run the bot
    bot.run("MTMzNzgxODMzMzIzOTU3NDU5OA.GqUJaI.6IH-rcE1U2rOpbgQYNm7avRi71yg5jM8yHBKaI")
