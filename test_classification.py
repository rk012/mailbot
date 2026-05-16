import asyncio
import os
from dotenv import load_dotenv

from db import Database
from gmail import GmailClient
from llm import GeminiClient
from bot import MailTriageCog

class DummyBot:
    async def wait_until_ready(self):
        pass

async def main():
    load_dotenv()
    db = Database()
    
    print("Initializing Gmail Client... (If your browser opens, please authenticate)")
    gmail = GmailClient()
    
    llm = GeminiClient()
    
    # Initialize Cog with a dummy bot to access the classify_emails method
    # Note: process_inbox will start in the background but exit immediately since channel_id is 0
    cog = MailTriageCog(DummyBot(), db, gmail, llm, 0)
    
    print("Fetching up to 50 emails from Inbox (both read and unread)...")
    emails = gmail.get_inbox_emails(limit=50, unread_only=False)
    
    if not emails:
        print("No emails found in your inbox.")
        return
        
    print(f"Fetched {len(emails)} emails. Running batch classification...")
    classifications = cog.classify_emails(emails)
    
    print("\n" + "="*50)
    print("CLASSIFICATION RESULTS")
    print("="*50 + "\n")
    
    important_count = 0
    
    for i, email in enumerate(emails, 1):
        msg_id = email['message_id']
        classification = classifications.get(msg_id, {})
        
        print(f"[{i}/{len(emails)}]")
        print(f"Subject: {email['subject']}")
        print(f"From: {email['sender']}")
        print(f"To: {email.get('recipient', 'N/A')}")
        if email.get('cc'):
            print(f"CC: {email['cc']}")
        
        category = classification.get('category', 'UNKNOWN')
        if category.lower() == 'important':
            important_count += 1
            
        print(f"Category:  {category}")
        print(f"Reasoning: {classification.get('reasoning', 'N/A')}")
        
        if classification.get('draft_needed'):
            print(f"Draft:     {classification.get('draft_text', '')}")
            
        print("-" * 50)
        
    print(f"\nTotal Important Emails: {important_count}/{len(emails)}")

if __name__ == "__main__":
    asyncio.run(main())
