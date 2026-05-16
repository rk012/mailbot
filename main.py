import os
import logging
import asyncio
from dotenv import load_dotenv

from db import Database
from gmail import GmailClient
from llm import GeminiClient
from bot import create_bot

if __name__ == "__main__":
    asyncio.set_event_loop(asyncio.new_event_loop())
    load_dotenv()
    
    DISCORD_TOKEN = os.environ.get("DISCORD_API_KEY")
    if not DISCORD_TOKEN:
        logging.error("No DISCORD_API_KEY found in environment.")
        exit(1)
        
    channel_id_str = os.environ.get("DISCORD_CHANNEL_ID", "0")
    try:
        channel_id = int(channel_id_str)
    except ValueError:
        channel_id = 0
        
    db = Database()
    gmail = GmailClient()
    llm = GeminiClient()
    
    bot = create_bot(db, gmail, llm, channel_id)
    bot.run(DISCORD_TOKEN)
