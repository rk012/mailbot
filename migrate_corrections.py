"""One-shot migration script: copies all corrections from SQLite into ChromaDB.

Usage:
    python3 migrate_corrections.py [db_path]

Defaults to data/inbox.db (the Docker-mounted production path).
Requires GEMINI_API_KEY in the environment for embedding generation.
"""
import os
import sys
import sqlite3
import logging

from dotenv import load_dotenv

load_dotenv()

from llm import GeminiClient
from db import Database, CHROMA_AVAILABLE

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("migrate")


def main():
    db_path = sys.argv[1] if len(sys.argv) > 1 else "data/inbox.db"

    if not os.path.exists(db_path):
        logger.error(f"Database file not found: {db_path}")
        sys.exit(1)

    if not CHROMA_AVAILABLE:
        logger.error("chromadb is not installed. Install it first: pip install chromadb")
        sys.exit(1)

    # Read corrections straight from SQLite (no Database wrapper needed)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM corrections ORDER BY timestamp").fetchall()
    corrections = [dict(r) for r in rows]
    conn.close()

    if not corrections:
        logger.info("No corrections found in SQLite — nothing to migrate.")
        return

    logger.info(f"Found {len(corrections)} correction(s) in {db_path}.")

    # Initialize the Database class which sets up ChromaDB
    llm = GeminiClient()
    db = Database(db_path, llm_client=llm)

    if not db.corrections_collection:
        logger.error("ChromaDB corrections collection could not be initialised.")
        sys.exit(1)

    # Check what's already in ChromaDB
    existing_count = db.corrections_collection.count()
    logger.info(f"ChromaDB currently has {existing_count} correction(s).")

    migrated = 0
    skipped = 0
    for c in corrections:
        msg_id = c["message_id"]

        # Check if already present
        try:
            existing = db.corrections_collection.get(ids=[msg_id])
            if existing and existing["ids"]:
                logger.info(f"  Skipping {msg_id} (already in ChromaDB).")
                skipped += 1
                continue
        except Exception:
            pass  # Not found — proceed to upsert

        doc_text = (
            f"Subject: {c.get('email_subject', '')}\n"
            f"From: {c.get('sender', '')}\n"
            f"To: {c.get('recipient', '')}\n"
            f"Snippet: {c.get('email_snippet', '')}"
        )
        metadata = {
            "message_id": msg_id,
            "email_subject": c.get("email_subject", ""),
            "email_snippet": c.get("email_snippet", ""),
            "sender": c.get("sender", ""),
            "recipient": c.get("recipient", ""),
            "cc": c.get("cc", ""),
            "predicted_category": c.get("predicted_category", ""),
            "corrected_category": c.get("corrected_category", ""),
        }

        try:
            db.corrections_collection.upsert(
                documents=[doc_text],
                metadatas=[metadata],
                ids=[msg_id],
            )
            migrated += 1
            logger.info(f"  Migrated {msg_id}: {c.get('email_subject', '?')}")
        except Exception as e:
            logger.error(f"  Failed to migrate {msg_id}: {e}")

    final_count = db.corrections_collection.count()
    logger.info(
        f"Migration complete. Migrated: {migrated}, Skipped: {skipped}. "
        f"ChromaDB now has {final_count} correction(s)."
    )


if __name__ == "__main__":
    main()
