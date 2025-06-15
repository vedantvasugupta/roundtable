import discord
import asyncio
import os
import sys

# Add the current directory to the path so we can import our modules
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import db

async def create_vote_results_channel(guild):
    """Create a dedicated vote-results channel if it doesn't exist"""
    # Check if the channel already exists
    channel_name = "vote-results"
    results_channel = discord.utils.get(guild.text_channels, name=channel_name)

    if results_channel:
        print(f"✅ Vote results channel already exists in {guild.name}")
        return results_channel

    # Create the channel if it doesn't exist
    try:
        # Define permission overwrites: Everyone can read, but only the bot can send messages
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=True, send_messages=False),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }

        results_channel = await guild.create_text_channel(name=channel_name, overwrites=overwrites)

        # Send initial message
        await results_channel.send("📊 **Vote Results Channel**\nThis channel displays the results of completed votes.")

        print(f"✅ Created #{channel_name} in {guild.name} with correct permissions")
        return results_channel
    except Exception as e:
        print(f"❌ Error creating vote-results channel: {e}")
        return None

async def main():
    """Main function to run the script"""
    # Create a temporary bot client to access the guild
    intents = discord.Intents.default()
    intents.guilds = True
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready():
        print(f"🤖 Bot is online as {client.user}")

        # Process each guild
        for guild in client.guilds:
            print(f"📌 Creating vote-results channel in {guild.name}")
            await create_vote_results_channel(guild)

        # Close the client after processing
        await client.close()

    # Read the bot token
    with open("bot_token.txt", "r") as f:
        bot_token = f.readline().strip()

    # Run the client
    await client.start(bot_token)

if __name__ == "__main__":
    # Run the async main function
    asyncio.run(main())
    print("✅ Script completed successfully")
