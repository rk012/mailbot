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

## Project Structure & State

* `bot.py`: **[TODO]** Main event loop and Discord UI components.
* `main.py`: **[TODO]** Entry point for the application, runs discord bot and gmail polling loop in background.
* `llm.py`: **[Complete]** Wrapper for Gemini API.
* `db.py`: **[Complete]** SQLite schema for email tracking (`inbox.db`) and few-shot user corrections.
* `gmail.py`: **[Complete]** Implements OAuth2 flow, message parsing, label manipulation, and drafting.
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

### 3. Discord Bot Wrapper (`bot.py`)

* Specific channel id can be hardcoded in env file
* Implement `/review-archive` command to batch-archive the `Routine` queue.
* Implement manual overrides via Discord buttons to "teach" the model when it misclassifies.


### 4. Error Handling & Robustness

* Implement token truncation for extremely long email threads before sending to Gemini.
* Add a retry mechanism for Gmail API rate limits.
* Ensure the `process_inbox` task handles "No new mail" states gracefully without spamming Discord.

### 5. Deployment Logic

* Configure the project to run as a background service or within a Docker container.
* Ensure SQLite database persistence.

## Guardrails & Constraints

* **Brief Responses Only:** The LLM must never suggest a draft longer than 3 sentences.
* **No Hardcoded Filters:** Do not use explicit allow/denylists or rules based filtering. The LLM must handle this semantically via the system prompt and few-shot examples.
