# Discord Emoji Bot
## Overview
A Python Discord bot that backs up and restores all server emojis and stickers.


# Files
bot.py            # Main bot code<br>
requirements.txt  # Python dependencies


# Setup
Install dependencies:<br>

pip install -r requirements.txt


# Run the bot:

python bot.py<br>
The Discord Emoji Bot workflow handles this automatically.


# Required Configuration
Bot needs Manage Emojis and Stickers permission in the server
Message Content Intent must be enabled in the Discord Developer Portal:
Go to discord.com/developers/applications
Select your bot → Bot → Privileged Gateway Intents
Enable Message Content Intent → Save


# Commands

/backup	Backs up all emojis and stickers to a ZIP file
/restore	Attach a backup ZIP file — the bot uploads all emojis and stickers back to the server


# Features
Rate limit handling with automatic retry and exponential backoff<br>
Processes uploads in batches to avoid hitting limits<br>
Skips emojis/stickers that already exist (no duplicates)<br>
Respects server slot limits and reports when they're reached<br>
Stickers with no stored related emoji get one randomly assigned<br>
Live progress updates in Discord during backup/restore
