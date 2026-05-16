import logging
import re
import json
from typing import Optional

import discord
from discord.ext import tasks
from discord.commands import slash_command

from db import Database
from gmail import GmailClient
from llm import GeminiClient
import os

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("MailBot")

# --- Discord UI Views ---

class CorrectionView(discord.ui.View):
    def __init__(self, message_id: str, subject: str, snippet: str, sender: str, recipient: str, cc: str, predicted: str, db: Database, gmail: GmailClient):
        super().__init__(timeout=None) # No timeout for buttons
        self.message_id = message_id
        self.subject = subject
        self.snippet = snippet
        self.sender = sender
        self.recipient = recipient
        self.cc = cc
        self.predicted = predicted
        self.db = db
        self.gmail = gmail

    async def handle_correction(self, interaction: discord.Interaction, corrected: str):
        if corrected == self.predicted:
            await interaction.response.edit_message(content=interaction.message.content + f"\n\n*✅ Dismissed (Kept as {corrected}).*", view=None)
            return

        # Log the correction
        self.db.add_correction(self.message_id, self.subject, self.snippet, self.sender, self.recipient, self.cc, self.predicted, corrected)
        
        # Apply Gmail actions based on the *new* state
        try:
            if corrected == "Routine":
                self.gmail.modify_email_state(self.message_id, "read")
                self.db.add_or_update_email(self.message_id, "routine", self.subject)
            elif corrected == "Important" or corrected == "Quick_Reply":
                self.db.update_email_status(self.message_id, corrected.lower())
                self.gmail.modify_email_state(self.message_id, "unread")
                
            await interaction.response.edit_message(content=interaction.message.content + f"\n\n*✅ Corrected to {corrected}. Thanks!*", view=None)
        except Exception as e:
            logger.error(f"Error handling correction: {e}")
            await interaction.response.send_message(f"Error applying correction: {e}", ephemeral=True)

    @discord.ui.button(label="Mark Routine", style=discord.ButtonStyle.secondary, custom_id="mark_routine")
    async def btn_routine(self, button: discord.ui.Button, interaction: discord.Interaction):
        await self.handle_correction(interaction, "Routine")

    @discord.ui.button(label="Mark Quick Reply", style=discord.ButtonStyle.primary, custom_id="mark_qr")
    async def btn_qr(self, button: discord.ui.Button, interaction: discord.Interaction):
        await self.handle_correction(interaction, "Quick_Reply")

    @discord.ui.button(label="Mark Important", style=discord.ButtonStyle.danger, custom_id="mark_important")
    async def btn_important(self, button: discord.ui.Button, interaction: discord.Interaction):
        await self.handle_correction(interaction, "Important")

    @discord.ui.button(label="Correct (Dismiss)", style=discord.ButtonStyle.success, custom_id="correct_dismiss")
    async def btn_dismiss(self, button: discord.ui.Button, interaction: discord.Interaction):
        await self.handle_correction(interaction, self.predicted)


class ArchiveReviewView(discord.ui.View):
    def __init__(self, emails: list, db: Database, gmail: GmailClient):
        super().__init__(timeout=180)
        self.emails = emails
        self.db = db
        self.gmail = gmail

    @discord.ui.button(label="Confirm Archive All", style=discord.ButtonStyle.success)
    async def confirm_btn(self, button: discord.ui.Button, interaction: discord.Interaction):
        await interaction.response.defer()
        archived_count = 0
        for email in self.emails:
            msg_id = email['message_id']
            try:
                self.gmail.modify_email_state(msg_id, "archive")
                self.db.remove_email(msg_id)
                archived_count += 1
            except Exception as e:
                logger.error(f"Failed to archive {msg_id}: {e}")
        
        await interaction.followup.edit_message(message_id=interaction.message.id, content=f"✅ Successfully archived {archived_count} routine emails.", view=None)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel_btn(self, button: discord.ui.Button, interaction: discord.Interaction):
        await interaction.response.edit_message(content="❌ Archive cancelled.", view=None)


# --- Bot Cog ---

class MailTriageCog(discord.Cog):
    def __init__(self, bot: discord.Bot, db: Database, gmail: GmailClient, llm: GeminiClient, channel_id: int):
        self.bot = bot
        self.db = db
        self.gmail = gmail
        self.llm = llm
        self.channel_id = channel_id
        
        try:
            user_info = self.gmail.get_user_info()
            self.user_email = user_info.get('email', 'Unknown')
            self.user_name = user_info.get('name', 'User')
            
            # Fallback to env var if name is not properly set by OAuth
            if self.user_name == 'User' and os.environ.get("USER_NAME"):
                self.user_name = os.environ.get("USER_NAME")
                
            logger.info(f"Authenticated as {self.user_name} ({self.user_email})")
        except Exception as e:
            logger.error(f"Failed to fetch user info: {e}")
            self.user_email = "Unknown"
            self.user_name = os.environ.get("USER_NAME", "User")
            
        self.process_inbox.start()

    def cog_unload(self):
        self.process_inbox.cancel()

    def classify_emails(self, emails: list) -> dict:
        if not emails:
            return {}

        recent_corrections = self.db.get_recent_corrections(limit=5)
        corrections_text = ""
        for c in recent_corrections:
            cc_str = f" | CC: {c['cc']}" if c.get('cc') else ""
            corrections_text += f"- From: {c.get('sender', 'Unknown')} | To: {c.get('recipient', 'Unknown')}{cc_str}\n"
            corrections_text += f"  Subject: '{c['email_subject']}' | Body Snippet: '{c.get('email_snippet', '')}'\n"
            corrections_text += f"  Model Predicted: '{c['predicted_category']}' -> User Corrected To: '{c['corrected_category']}'\n\n"
        
        if not corrections_text:
            corrections_text = "No past corrections yet.\n"

        emails_text = ""
        for email_data in emails:
            body_snippet = email_data['body'][:1000] if email_data['body'] else ""
            cc_str = f" | CC: {email_data.get('cc')}" if email_data.get('cc') else ""
            emails_text += f"Message ID: {email_data['message_id']}\n"
            emails_text += f"Subject: {email_data['subject']}\n"
            emails_text += f"From: {email_data['sender']}\n"
            emails_text += f"To: {email_data.get('recipient', 'Unknown')}{cc_str}\n"
            emails_text += f"Body: {body_snippet}\n"
            emails_text += "-" * 20 + "\n"
        
        user_context = f"You are acting on behalf of {self.user_name} ({self.user_email})."
        
        custom_rules = ""
        try:
            if os.path.exists("rules.txt"):
                with open("rules.txt", "r") as f:
                    content = f.read().strip()
                    if content:
                        custom_rules = f"\nUser's Custom Rules:\n{content}\n"
        except Exception as e:
            logger.error(f"Failed to read rules.txt: {e}")
        
        prompt = f"""You are an AI assistant that triages a user's Gmail inbox. {user_context}
Classify the following emails into one of three categories:
1. Routine: Newsletters, automated alerts, marketing, generic updates where no reply is expected. Also use this for emails sent to mailing lists or where the user is merely CC'd and no direct action is needed from them.
2. Quick_Reply: ANY email (including those from recruiters, professors, or important contacts) that can be fully answered with a brief (< 3 sentences) reply.
3. Important: Recruiters, professors, interviews, personal matters, or urgent tasks that require a longer, thoughtful response, or action outside of a quick email reply.

Reply EXACTLY with a JSON dictionary mapping the Message ID string to its classification object:
{{
  "message_id_1": {{
    "category": "Routine" | "Quick_Reply" | "Important",
    "reasoning": "brief explanation",
    "draft_needed": true/false,
    "draft_text": "draft text or empty"
  }},
  "message_id_2": {{ ... }}
}}

Rules:
- If you provide a draft, the category MUST be "Quick_Reply". Never provide a draft for "Important" or "Routine".
- draft_needed should ONLY be true if the category is Quick_Reply.
- draft_text MUST be strictly under 3 sentences.{custom_rules}

Consider these past user corrections to improve your accuracy:
{corrections_text}

Emails to classify:
{emails_text}
"""
        
        response = self.llm.generate_response(prompt)
        
        try:
            json_str = response
            match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', response, re.DOTALL)
            if match:
                json_str = match.group(1)
            else:
                 start = response.find('{')
                 end = response.rfind('}')
                 if start != -1 and end != -1:
                     json_str = response[start:end+1]
                     
            result = json.loads(json_str)
            clean_result = {}
            for msg_id, classification in result.items():
                if isinstance(classification, dict):
                    if "category" not in classification:
                        classification["category"] = "Important"
                    clean_result[msg_id] = classification
            return clean_result
        except Exception as e:
            logger.error(f"Failed to parse LLM response for batch. Response: {response}. Error: {e}")
            return {email['message_id']: {"category": "Important", "reasoning": "Failed to parse LLM response", "draft_needed": False, "draft_text": ""} for email in emails}

    @discord.slash_command(name="review-archive", description="Review and batch archive routine emails.")
    async def review_archive(self, ctx: discord.ApplicationContext):
        routine_emails = self.db.get_emails_by_status("routine")
        if not routine_emails:
            await ctx.respond("No routine emails to archive right now.")
            return
            
        summary = "**Routine Emails Pending Archive:**\n"
        for idx, e in enumerate(routine_emails[:10], 1):
            summary += f"{idx}. {e['subject']} (from {e['sender']})\n"
            
        if len(routine_emails) > 10:
            summary += f"...and {len(routine_emails) - 10} more.\n"
            
        summary += "\nDo you want to archive all of these?"
        view = ArchiveReviewView(routine_emails, self.db, self.gmail)
        await ctx.respond(summary, view=view)


    @tasks.loop(minutes=30)
    async def process_inbox(self):
        if not self.channel_id:
            logger.warning("DISCORD_CHANNEL_ID is not set. Cannot send notifications.")
            return
            
        channel = self.bot.get_channel(self.channel_id)
        if not channel:
            logger.warning(f"Could not find channel with ID {self.channel_id}.")
            return

        logger.info("Polling Gmail inbox for new unread messages...")
        try:
            raw_emails = self.gmail.get_inbox_emails(limit=20, unread_only=True)
            if not raw_emails:
                logger.info("No new unread emails.")
                return
                
            emails = [e for e in raw_emails if not self.db.get_email(e['message_id'])]
            
            if not emails:
                logger.info("No *unprocessed* new unread emails.")
                return
                
            logger.info(f"Classifying {len(emails)} emails in batch...")
            classifications = self.classify_emails(emails)
            
            for email in emails:
                msg_id = email['message_id']
                
                classification = classifications.get(msg_id, {
                    "category": "Important",
                    "reasoning": "Missing from LLM batch output."
                })
                
                category = classification.get("category", "Important")
                reasoning = classification.get("reasoning", "No reasoning provided.")
                
                snippet = email['body'][:100] + "..." if email['body'] else ""
                view = CorrectionView(msg_id, email['subject'], snippet, email['sender'], email['recipient'], email['cc'], category, self.db, self.gmail)
                
                if category == "Routine":
                    self.gmail.modify_email_state(msg_id, "read")
                    self.db.add_or_update_email(msg_id, "routine", email['subject'], email['body'], email['sender'], email['recipient'], email['cc'])
                    logger.info(f"Marked {msg_id} as Routine.")
                    
                elif category == "Quick_Reply":
                    self.db.add_or_update_email(msg_id, "quick_reply", email['subject'], email['body'], email['sender'], email['recipient'], email['cc'])
                    
                    draft_needed = classification.get("draft_needed", False)
                    draft_text = classification.get("draft_text", "")
                    
                    notification = f"**⚡ Quick Reply Detected:** `{email['subject']}`\n**From:** {email['sender']}\n**Reason:** {reasoning}\n"
                    
                    if draft_needed and draft_text:
                        try:
                            self.gmail.create_draft_reply(msg_id, draft_text)
                            notification += f"**Draft Created:** `{draft_text}`\n"
                            notification += f"[View Email/Draft](https://mail.google.com/mail/u/0/#inbox/{msg_id})"
                        except Exception as e:
                            logger.error(f"Failed to create draft: {e}")
                            notification += f"\n*Failed to create draft: {e}*"
                    else:
                        notification += f"[View Email](https://mail.google.com/mail/u/0/#inbox/{msg_id})"
                        
                    await channel.send(notification, view=view)
                    
                else: # Important or fallback
                    self.db.add_or_update_email(msg_id, "important", email['subject'], email['body'], email['sender'], email['recipient'], email['cc'])
                    
                    notification = f"**🚨 Important Email:** `{email['subject']}`\n**From:** {email['sender']}\n**Reason:** {reasoning}\n[View Email](https://mail.google.com/mail/u/0/#inbox/{msg_id})"
                    await channel.send(notification, view=view)
                    
        except Exception as e:
            logger.error(f"Error during inbox processing: {e}")

    @process_inbox.before_loop
    async def before_process_inbox(self):
        await self.bot.wait_until_ready()

def create_bot(db: Database, gmail: GmailClient, llm: GeminiClient, channel_id: int) -> discord.Bot:
    intents = discord.Intents.default()
    bot = discord.Bot(intents=intents)
    bot.add_cog(MailTriageCog(bot, db, gmail, llm, channel_id))
    
    @bot.event
    async def on_ready():
        logger.info(f"Bot logged in as {bot.user}")
        
    return bot
