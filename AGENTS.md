# Project: Autonomous Gmail Triage & Discord Assistant

## Project Overview

An automated, zero-cost email management system that uses **Gemini 3.1 Flash-Lite** to triage a Gmail inbox. The application runs as a background process and interfaces with the user exclusively through a **private Discord server**.

The goal is "Inbox Zero" via semantic understanding rather than hardcoded rules.

## Core Requirements

* **Zero-Cost Infrastructure:** Uses Gemini 3.1 Flash-Lite (500 RPD free tier) and local SQLite.
* **Polling Frequency:** Fetches new mail every 20–30 minutes.
* **Classification Categories:**
* `Routine`: (Canvas updates, newsletters, marketing) Marked as read immediately, queued for daily batch archive.
* `Quick_Reply`: (Brief inquiries) Creates a Gmail draft (< 3 sentences) and pings Discord with a link to the draft.
* `Important`: (Recruiters, Professors, Interviews) Leaves unread and sends a priority notification to Discord.
* **Adaptive Learning:** Implements a dynamic few-shot "teaching loop." User corrections in Discord are stored in SQLite and injected into the LLM system prompt for future classifications.
* **User Experience:**
* `/review-archive` command to batch-archive the `Routine` queue.
* Manual overrides via Discord buttons to "teach" the model when it misclassifies.



## Technology Stack

* **Language:** Python
* **Discord Library:** `Pycord`
* **LLM:** `google-genai` (Gemini 3.1 Flash-Lite)
* **Email:** `google-api-python-client` (Gmail API v1)
* **Database:** `sqlite3`

## Project Maintenance

* Update this `AGENTS.md` file whenever a feature is implemented or materially changed, so future agents can trust the project state and task list.

## Project Structure & State

* `bot.py`: **[Complete]** Main event loop, timestamp-based inbox refresh, Discord UI components, routine sync summaries, and routine correction slash command.
* `main.py`: **[Complete]** Entry point for the application, runs discord bot and gmail polling loop in background.
* `llm.py`: **[Complete]** Wrapper for Gemini API.
* `db.py`: **[Complete]** SQLite schema for email tracking (`inbox.db`) and few-shot user corrections.
* `gmail.py`: **[Complete]** Implements OAuth2 flow, message parsing, Gmail internal date extraction, label manipulation, and drafting.
* `Dockerfile`: **[Complete]** Builds the Python/uv runtime image for background service deployment.
* `docker-compose.yml`: **[Complete]** Runs the bot as a restartable service with persisted SQLite and mounted Gmail credentials/token files.
* `README.md`: **[Complete]** Setup, Docker Compose, environment variable, and Discord usage documentation.
* `.env`:  Contains API keys and Discord IDs.

---

## Task List for Agents

### 1. Database Implementation (`db.py`) [Complete]

* Initialize SQLite database (`inbox.db`)
* Add email with specific state / change state of specific email. Store body and subject locally.
* Query emails that are read and in inbox (excluding ones user marked to keep)
* Remove emails from db once they are archived
* Maintain a list of user corrections for email sorting (few-shot learning)
* Wrap all database operations

### 2. Gmail API Implementation (`gmail.py`) [Complete]

* Implement OAuth2 flow
* Get all emails in inbox
* Mark specific email as [read, unread, archive]
* Add draft reply to a particular email

### 3. Discord Bot Wrapper (`bot.py`) [Complete]

* Specific channel id can be hardcoded in env file
* Implement `/review-archive` command to batch-archive the `Routine` queue.
* Implement manual overrides via Discord buttons to "teach" the model when it misclassifies.


### 4. Error Handling & Robustness [Complete]

* Implement token truncation for extremely long email threads before sending to Gemini.
* Add a retry mechanism for Gmail API rate limits.
* Ensure the `process_inbox` task handles "No new mail" states gracefully without spamming Discord.

### 5. Deployment & Polish [Complete]

* Configure the project to run as a background service via Docker Compose (`docker-compose.yml` and `Dockerfile`).
* Ensure SQLite database persistence and proper volume mapping for credentials.
* Add a polished `README.md` with setup and usage instructions.
* Added `.env.example` and `.dockerignore` to document required environment values and keep secrets/runtime state out of image builds.
* Added env-configurable paths:
* `DB_PATH` for SQLite (`/app/data/inbox.db` in Docker).
* `GMAIL_CREDENTIALS_PATH` for Gmail OAuth client credentials.
* `GMAIL_TOKEN_PATH` for the persisted Gmail OAuth token.
* `RULES_PATH` for optional semantic triage rules (`/app/config/rules.txt` in Docker).
* Verified `docker compose build` succeeds and `docker compose up -d` starts the `mailtriage` service.
* Inbox refresh now uses a timestamp-based scheduler that wakes frequently and triggers when the 30-minute wall-clock interval has elapsed, so laptop sleep time counts toward the interval.

### 6. Proactive Auto-Archive & Inbox Management

* Create a 24hr background loop to review all read emails in the inbox.
* Use LLM prompting to decide if they should be archived (i.e., no follow-up expected).
* Skip/ignore emails the user previously marked to keep via a `/review-archive` interaction.
* Periodically ping the user to run `/review-archive` when the inbox reaches a high capacity.

### 7. Enhanced Review-Archive UI

* Add options for "Archive All" or "Save Choices" to the `/review-archive` Discord UI.
* For "Save Choices", read the current states directly from Gmail to sync the user's manual archive/keep actions back into the database preferences.

### 8. Vector DB for Few-Shot Learning

* Integrate a local vector database.
* When classifying emails, retrieve the top 10 most semantically relevant user corrections to inject into the LLM prompt for highly targeted context.

### 9. Inbox Synchronization & Summaries [Complete]

* Process unread emails that are already in the inbox (but not in the database) and default them to `Routine`.
* After every sync, send a single consolidated summary message to Discord containing a 1-line overview (Subject + truncated body) for all `Routine` emails processed in that batch.
* Provide `/correct-routine` so the user can correct a misclassified `Routine` email back to `Important` or `Quick_Reply`.
* Quick reply corrections create a Gmail draft, mark the message unread, update SQLite status, and save the correction for future prompts.
* Routine summary delivery is logged and chunked under Discord's message length limit.

### 10. Two-Tiered "Important" Categories

* Implement two levels of importance: `Important` and `Urgent`.
* `Important`: Keeps email unread and sends a normal Discord notification.
* `Urgent`: Keeps email unread, sends a Discord notification, AND explicitly pings a specific Discord user ID.

## Guardrails & Constraints

* **Brief Responses Only:** The LLM must never suggest a draft longer than 3 sentences.
* **No Hardcoded Filters:** Do not use explicit allow/denylists or rules based filtering. The LLM must handle this semantically via the system prompt and few-shot examples.
