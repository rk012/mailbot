import logging
import re
import json
from datetime import datetime, timedelta, timezone
from typing import Optional

import discord
from discord.ext import tasks

from db import Database
from gmail import GmailClient
from llm import GeminiClient
import os

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("MailBot")
INBOX_REFRESH_INTERVAL = timedelta(minutes=30)
INBOX_REFRESH_CHECK_INTERVAL_SECONDS = 60
MAX_DRAFT_SENTENCES = 2
CORRECTABLE_ROUTINE_CATEGORIES = ["Important", "Quick_Reply"]
DISCORD_MESSAGE_LIMIT = 2000

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
            elif corrected in ["Important", "Quick_Reply"]:
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

class MailbotCog(discord.Cog):
    def __init__(self, bot: discord.Bot, db: Database, gmail: GmailClient, llm: GeminiClient, channel_id: int):
        self.bot = bot
        self.db = db
        self.gmail = gmail
        self.llm = llm
        self.channel_id = channel_id
        self.started_at = datetime.now(timezone.utc)
        
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

        self.last_inbox_refresh_at: Optional[datetime] = None
        self.process_inbox.start()

    def cog_unload(self):
        self.process_inbox.cancel()

    def _normalize_category(self, category: str) -> str:
        normalized = (category or "").strip().lower().replace("-", "_").replace(" ", "_")
        category_map = {
            "routine": "Routine",
            "quickreply": "Quick_Reply",
            "quick_reply": "Quick_Reply",
            "important": "Important",
        }
        return category_map.get(normalized, "Important")

    def _truncate_text(self, text: str, limit: int) -> str:
        clean_text = re.sub(r'\s+', ' ', text or '').strip()
        if len(clean_text) <= limit:
            return clean_text
        return clean_text[: max(0, limit - 3)].rstrip() + "..."

    def _limit_draft_sentences(self, draft_text: str) -> str:
        clean_text = re.sub(r'\s+', ' ', draft_text or '').strip().strip('"')
        if not clean_text:
            return ""

        sentences = re.split(r'(?<=[.!?])\s+', clean_text)
        return " ".join(sentences[:MAX_DRAFT_SENTENCES]).strip()

    def _generate_quick_reply_draft(self, email: dict) -> str:
        prompt = f"""Write a concise Gmail reply on behalf of {self.user_name} ({self.user_email}).
The reply must be fewer than 3 sentences. Return only the draft text.

Email:
From: {email.get('sender', 'Unknown')}
To: {email.get('recipient', 'Unknown')}
Subject: {email.get('subject', '')}
Body:
{self._truncate_text(email.get('body', ''), 3000)}
"""
        return self._limit_draft_sentences(self.llm.generate_response(prompt))

    def _is_preexisting_unread_email(self, email: dict) -> bool:
        internal_date = email.get('internal_date')
        return bool(internal_date and internal_date < self.started_at)

    def _build_routine_summary_chunks(self, emails: list) -> list:
        header = (
            f"**Routine Sync Summary ({len(emails)})**\n"
            "Use `/correct-routine` with the message ID if one needs attention.\n"
        )
        if not emails:
            return [header + "No routine emails were processed."]

        available_chars = max(600, DISCORD_MESSAGE_LIMIT - 50 - len(header))
        line_budget = max(70, available_chars // len(emails))
        subject_limit = max(20, min(45, line_budget // 3))
        body_limit = max(20, min(55, line_budget - subject_limit - 35))

        chunks = []
        current_chunk = header
        for email in emails:
            subject = self._truncate_text(email.get('subject') or "(No subject)", subject_limit)
            body = self._truncate_text(email.get('body') or "[No body]", body_limit)
            line = f"- `{email['message_id']}` {subject} - {body}\n"
            if len(current_chunk) + len(line) > DISCORD_MESSAGE_LIMIT:
                chunks.append(current_chunk.rstrip())
                current_chunk = line
            else:
                current_chunk += line

        if current_chunk.strip():
            chunks.append(current_chunk.rstrip())

        return chunks

    async def _send_routine_summary(self, channel, emails: list):
        if not emails:
            logger.info("No routine emails in this sync batch; skipping summary.")
            return

        chunks = self._build_routine_summary_chunks(emails)
        logger.info(
            f"Sending routine sync summary for {len(emails)} emails to Discord channel "
            f"{getattr(channel, 'id', 'unknown')} in {len(chunks)} message(s)."
        )

        for chunk in chunks:
            await channel.send(
                chunk,
                allowed_mentions=discord.AllowedMentions.none(),
            )

        logger.info("Routine sync summary sent successfully.")

    def _record_routine_email(self, email: dict):
        self.gmail.modify_email_state(email['message_id'], "read")
        self.db.add_or_update_email(
            email['message_id'],
            "routine",
            email.get('subject', ''),
            email.get('body', ''),
            email.get('sender', ''),
            email.get('recipient', ''),
            email.get('cc', ''),
        )

    async def _correct_routine_email(self, message_id: str, corrected: str) -> str:
        corrected = self._normalize_category(corrected)
        if corrected not in CORRECTABLE_ROUTINE_CATEGORIES:
            raise ValueError(f"Category must be one of: {', '.join(CORRECTABLE_ROUTINE_CATEGORIES)}")

        email = self.db.get_email(message_id)
        if not email:
            raise ValueError("I could not find that message ID in the local database.")
        if email.get('status') != "routine":
            raise ValueError(f"That email is currently tracked as `{email.get('status')}`, not `routine`.")

        snippet = self._truncate_text(email.get('body', ''), 200)
        self.db.add_correction(
            message_id,
            email.get('subject', ''),
            snippet,
            email.get('sender', ''),
            email.get('recipient', ''),
            email.get('cc', ''),
            "Routine",
            corrected,
        )

        if corrected == "Quick_Reply":
            draft_text = self._generate_quick_reply_draft(email)
            if draft_text:
                self.gmail.create_draft_reply(message_id, draft_text)
            self.db.update_email_status(message_id, "quick_reply")
            self.gmail.modify_email_state(message_id, "unread")
            return f"Corrected to `Quick_Reply`, marked unread, and created a draft: `{draft_text}`"

        self.db.update_email_status(message_id, corrected.lower())
        self.gmail.modify_email_state(message_id, "unread")
        return f"Corrected to `{corrected}` and marked unread."

    def classify_emails(self, emails: list) -> dict:
        if not emails:
            return {}

        emails_text = ""
        query_text = ""
        for email_data in emails:
            body_snippet = email_data['body'][:1000] if email_data['body'] else ""
            cc_str = f" | CC: {email_data.get('cc')}" if email_data.get('cc') else ""
            emails_text += f"Message ID: {email_data['message_id']}\n"
            emails_text += f"Subject: {email_data['subject']}\n"
            emails_text += f"From: {email_data['sender']}\n"
            emails_text += f"To: {email_data.get('recipient', 'Unknown')}{cc_str}\n"
            emails_text += f"Body: {body_snippet}\n"
            emails_text += "-" * 20 + "\n"
            
            # Short snippet for vector search query
            query_snippet = email_data['body'][:100] if email_data['body'] else ""
            query_text += f"Subject: {email_data['subject']} Snippet: {query_snippet} "

        # Get top 10 semantically relevant corrections
        recent_corrections = self.db.get_semantically_relevant_corrections(query_text=query_text, limit=10)
        
        corrections_text = ""
        for c in recent_corrections:
            cc_str = f" | CC: {c['cc']}" if c.get('cc') else ""
            corrections_text += f"- From: {c.get('sender', 'Unknown')} | To: {c.get('recipient', 'Unknown')}{cc_str}\n"
            corrections_text += f"  Subject: '{c['email_subject']}' | Body Snippet: '{c.get('email_snippet', '')}'\n"
            corrections_text += f"  Model Predicted: '{c['predicted_category']}' -> User Corrected To: '{c['corrected_category']}'\n\n"
        
        if not corrections_text:
            corrections_text = "No past corrections yet.\n"
        
        user_context = f"You are acting on behalf of {self.user_name} ({self.user_email})."
        
        custom_rules = ""
        try:
            rules_path = os.environ.get("RULES_PATH", "rules.txt")
            if os.path.exists(rules_path):
                with open(rules_path, "r") as f:
                    content = f.read().strip()
                    if content:
                        custom_rules = f"\nUser's Custom Rules:\n{content}\n"
        except Exception as e:
            logger.error(f"Failed to read rules.txt: {e}")
        
        prompt = f"""You are an AI assistant that triages a user's Gmail inbox. {user_context}
Classify the following emails into one of three categories:
1. Routine: Newsletters, automated alerts, marketing, generic updates where no reply is expected. Also use this for emails sent to mailing lists or where the user is merely CC'd and no direct action is needed from them.
2. Quick_Reply: ANY email (including those from recruiters, professors, or important contacts) where a reply is STRICTLY REQUIRED or expected, and it can be fully answered with a brief (< 3 sentences) reply. DO NOT use this for cold outreaches, automated advertising, or PR pitches.
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

    @discord.slash_command(name="correct-routine", description="Correct a routine email from a sync summary.")
    async def correct_routine(
        self,
        ctx: discord.ApplicationContext,
        message_id: discord.Option(str, "Gmail message ID from the routine summary."),
        category: discord.Option(str, "Corrected category.", choices=CORRECTABLE_ROUTINE_CATEGORIES),
    ):
        await ctx.defer(ephemeral=True)
        try:
            result = await self._correct_routine_email(message_id, category)
            await ctx.followup.send(result, ephemeral=True)
        except Exception as e:
            logger.error(f"Error correcting routine email {message_id}: {e}")
            await ctx.followup.send(f"Could not apply correction: {e}", ephemeral=True)

    @discord.slash_command(name="sync-now", description="Manually trigger an inbox sync.")
    async def sync_now(self, ctx: discord.ApplicationContext):
        await ctx.defer(ephemeral=True)
        try:
            await self._process_inbox_now()
            await ctx.followup.send("✅ Sync completed.", ephemeral=True)
        except Exception as e:
            logger.error(f"Manual sync failed: {e}")
            await ctx.followup.send(f"❌ Sync failed: {e}", ephemeral=True)



    @tasks.loop(seconds=INBOX_REFRESH_CHECK_INTERVAL_SECONDS)
    async def process_inbox(self):
        now = datetime.now(timezone.utc)
        if (
            self.last_inbox_refresh_at
            and now - self.last_inbox_refresh_at < INBOX_REFRESH_INTERVAL
        ):
            return

        self.last_inbox_refresh_at = now
        await self._process_inbox_now()

    async def _process_inbox_now(self):
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
                
            untracked_emails = [e for e in raw_emails if not self.db.get_email(e['message_id'])]
            
            if not untracked_emails:
                logger.info("No *unprocessed* new unread emails.")
                return

            routine_batch = []
            existing_unread_emails = [
                email for email in untracked_emails
                if self._is_preexisting_unread_email(email)
            ]
            emails = [
                email for email in untracked_emails
                if not self._is_preexisting_unread_email(email)
            ]

            if existing_unread_emails:
                logger.info(
                    f"Defaulting {len(existing_unread_emails)} pre-existing unread inbox emails to Routine."
                )
                for email in existing_unread_emails:
                    self._record_routine_email(email)
                    routine_batch.append(email)

            if not emails:
                await self._send_routine_summary(channel, routine_batch)
                return

            logger.info(f"Classifying {len(emails)} emails in batch...")
            classifications = self.classify_emails(emails)
            
            for email in emails:
                msg_id = email['message_id']
                
                classification = classifications.get(msg_id, {
                    "category": "Important",
                    "reasoning": "Missing from LLM batch output."
                })
                
                category = self._normalize_category(classification.get("category", "Important"))
                reasoning = classification.get("reasoning", "No reasoning provided.")
                
                snippet = email['body'][:100] + "..." if email['body'] else ""
                view = CorrectionView(msg_id, email['subject'], snippet, email['sender'], email['recipient'], email['cc'], category, self.db, self.gmail)
                
                if category == "Routine":
                    self._record_routine_email(email)
                    routine_batch.append(email)
                    logger.info(f"Marked {msg_id} as Routine.")
                    
                elif category == "Quick_Reply":
                    self.db.add_or_update_email(msg_id, "quick_reply", email['subject'], email['body'], email['sender'], email['recipient'], email['cc'])
                    
                    draft_needed = classification.get("draft_needed", False)
                    draft_text = self._limit_draft_sentences(classification.get("draft_text", ""))
                    
                    notification = f"**⚡ Quick Reply Detected:** `{email['subject']}`\n**From:** {email['sender']}\n**Reason:** {reasoning}\n"
                    
                    if draft_needed and draft_text:
                        try:
                            self.gmail.create_draft_reply(msg_id, draft_text)
                            notification += f"**Draft Created:** `{draft_text}`\n"
                            notification += f"[View Email/Draft](<https://mail.google.com/mail/u/0/#inbox/{msg_id}>)"
                        except Exception as e:
                            logger.error(f"Failed to create draft: {e}")
                            notification += f"\n*Failed to create draft: {e}*"
                    else:
                        notification += f"[View Email](<https://mail.google.com/mail/u/0/#inbox/{msg_id}>)"
                        
                    await channel.send(notification, view=view)
                    
                else: # Important or fallback
                    self.db.add_or_update_email(msg_id, "important", email['subject'], email['body'], email['sender'], email['recipient'], email['cc'])
                    
                    notification = f"**🚨 Important Email:** `{email['subject']}`\n**From:** {email['sender']}\n**Reason:** {reasoning}\n[View Email](<https://mail.google.com/mail/u/0/#inbox/{msg_id}>)"
                    await channel.send(notification, view=view)

            await self._send_routine_summary(channel, routine_batch)

        except Exception:
            logger.exception("Error during inbox processing.")

    @process_inbox.before_loop
    async def before_process_inbox(self):
        await self.bot.wait_until_ready()

def create_bot(db: Database, gmail: GmailClient, llm: GeminiClient, channel_id: int) -> discord.Bot:
    intents = discord.Intents.default()
    bot = discord.Bot(intents=intents)
    bot.add_cog(MailbotCog(bot, db, gmail, llm, channel_id))
    
    @bot.event
    async def on_ready():
        logger.info(f"Bot logged in as {bot.user}")
        
    return bot
