# MailTriage

Autonomous Gmail triage for a private Discord server. MailTriage polls Gmail, classifies unread inbox messages with Gemini Flash-Lite, drafts short replies when appropriate, and lets you teach the classifier from Discord buttons.

## What it does

- Polls Gmail every 30 minutes for unread inbox mail.
- Classifies messages as `Routine`, `Quick_Reply`, or `Important`.
- Marks `Routine` mail as read and queues it for `/review-archive`.
- Creates Gmail draft replies for `Quick_Reply` messages, keeping drafts under 3 sentences.
- Leaves `Important` messages unread and sends Discord notifications.
- Stores user corrections in SQLite and injects them as few-shot examples for future classifications.

## Requirements

- Docker Desktop, or Python 3.10+ with `uv`
- A Discord bot token with access to your private server
- A Google Cloud OAuth client credentials file for Gmail API access
- A Gemini API key

## Local setup

1. Copy the environment template:

   ```bash
   cp .env.example .env
   ```

2. Fill in `.env`:

   ```dotenv
   DISCORD_API_KEY=...
   DISCORD_CHANNEL_ID=...
   GEMINI_API_KEY=...
   GEMINI_MODEL=gemini-3.1-flash-lite
   USER_NAME=...
   ```

3. Put your Google OAuth desktop/client credentials at:

   ```text
   credentials.json
   ```

4. Create an optional `rules.txt` file for semantic preferences you want injected into the triage prompt.

5. Run once locally to complete Gmail OAuth if you do not already have `token.json`:

   ```bash
   uv sync
   uv run python main.py
   ```

   A browser-based OAuth flow will create `token.json`. Keep both `credentials.json` and `token.json` private.

## Run with Docker Compose

After `.env`, `credentials.json`, and `token.json` exist:

```bash
docker compose up -d --build
```

The Compose service persists runtime state with these mounts:

- `./data:/app/data` for SQLite (`/app/data/inbox.db`)
- `./credentials.json:/app/secrets/credentials.json:ro`
- `./token.json:/app/secrets/token.json`
- `./config:/app/config:ro` for optional Docker-only rules at `./config/rules.txt`

Useful commands:

```bash
docker compose logs -f
docker compose restart
docker compose down
```

For Docker, put custom semantic triage rules in `./config/rules.txt`. For local non-Docker runs, put them in `./rules.txt` or set `RULES_PATH`.

## Discord usage

Use `/review-archive` in the configured Discord channel to review routine messages and archive the queued batch. Notification messages include correction buttons so you can mark misclassified mail as `Routine`, `Quick_Reply`, or `Important`; those corrections are saved in SQLite for future prompts.

## Environment variables

| Variable | Required | Default | Description |
| --- | --- | --- | --- |
| `DISCORD_API_KEY` | Yes | none | Discord bot token. |
| `DISCORD_CHANNEL_ID` | Yes | `0` | Channel where notifications are sent. |
| `GEMINI_API_KEY` | Yes | none | Gemini API key used by `google-genai`. |
| `GEMINI_MODEL` | No | `gemini-3.1-flash-lite` | Gemini model id. |
| `USER_NAME` | No | `User` | Fallback display name if Google profile lookup fails. |
| `DB_PATH` | No | `inbox.db` | SQLite database path. Compose sets this to `/app/data/inbox.db`. |
| `GMAIL_CREDENTIALS_PATH` | No | `credentials.json` | Google OAuth client credentials path. |
| `GMAIL_TOKEN_PATH` | No | `token.json` | Gmail OAuth token path. |
| `RULES_PATH` | No | `rules.txt` | Optional custom semantic triage rules path. |
