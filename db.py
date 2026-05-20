import sqlite3
import os
import json
from typing import List, Dict, Optional

try:
    import chromadb
    from chromadb.api.types import EmbeddingFunction, Documents, Embeddings
    CHROMA_AVAILABLE = True
except ImportError:
    CHROMA_AVAILABLE = False
    EmbeddingFunction = object  # dummy

class GeminiEmbeddingFunction(EmbeddingFunction):
    def __init__(self, llm_client):
        self.llm_client = llm_client

    def __call__(self, input: "Documents") -> "Embeddings":
        if not self.llm_client:
            return []
        return self.llm_client.generate_embeddings(input)

class Database:
    def __init__(self, db_path="inbox.db", llm_client=None):
        self.db_path = db_path
        self._initialize_db()
        
        self.chroma_client = None
        self.corrections_collection = None
        if CHROMA_AVAILABLE and llm_client:
            chroma_dir = os.path.join(os.path.dirname(os.path.abspath(db_path)) or ".", "chroma")
            self.chroma_client = chromadb.PersistentClient(path=chroma_dir)
            self.corrections_collection = self.chroma_client.get_or_create_collection(
                name="corrections",
                embedding_function=GeminiEmbeddingFunction(llm_client)
            )

    def _get_connection(self):
        return sqlite3.connect(self.db_path)

    def _initialize_db(self):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # Table for tracking emails currently in the inbox
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS emails (
                    message_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    subject TEXT,
                    body TEXT,
                    sender TEXT,
                    recipient TEXT,
                    cc TEXT,
                    added_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Migrations for existing databases
            for col in ['sender', 'recipient', 'cc']:
                try:
                    cursor.execute(f'ALTER TABLE emails ADD COLUMN {col} TEXT')
                except sqlite3.OperationalError:
                    pass
            
            # Table for few-shot learning corrections
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS corrections (
                    message_id TEXT PRIMARY KEY,
                    email_subject TEXT,
                    email_snippet TEXT,
                    sender TEXT,
                    recipient TEXT,
                    cc TEXT,
                    predicted_category TEXT,
                    corrected_category TEXT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Migrations for existing databases
            for col in ['sender', 'recipient', 'cc']:
                try:
                    cursor.execute(f'ALTER TABLE corrections ADD COLUMN {col} TEXT')
                except sqlite3.OperationalError:
                    pass
            
            conn.commit()

    def add_or_update_email(self, message_id: str, status: str, subject: str = "", body: str = "", sender: str = "", recipient: str = "", cc: str = ""):
        """Adds a new email to track or updates its status if it already exists."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO emails (message_id, status, subject, body, sender, recipient, cc, added_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(message_id) DO UPDATE SET
                    status=excluded.status,
                    subject=excluded.subject,
                    body=excluded.body,
                    sender=excluded.sender,
                    recipient=excluded.recipient,
                    cc=excluded.cc,
                    added_at=CURRENT_TIMESTAMP
            ''', (message_id, status, subject, body, sender, recipient, cc))
            conn.commit()

    def update_email_status(self, message_id: str, status: str):
        """Updates just the status of an existing email."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE emails SET status = ?, added_at = CURRENT_TIMESTAMP
                WHERE message_id = ?
            ''', (status, message_id))
            conn.commit()

    def get_email(self, message_id: str) -> Optional[Dict]:
        """Fetches a specific email's details."""
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM emails WHERE message_id = ?', (message_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_emails_by_status(self, status: str) -> List[Dict]:
        """Fetches all emails matching a specific status."""
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM emails WHERE status = ? ORDER BY added_at DESC', (status,))
            return [dict(row) for row in cursor.fetchall()]

    def remove_email(self, message_id: str):
        """Removes an email from the tracking table once it's archived."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM emails WHERE message_id = ?', (message_id,))
            conn.commit()

    def get_all_email_ids(self) -> set:
        """Returns the set of all tracked message IDs."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT message_id FROM emails')
            return {row[0] for row in cursor.fetchall()}

    def add_correction(self, message_id: str, subject: str, snippet: str, sender: str, recipient: str, cc: str, predicted: str, corrected: str):
        """Logs a manual user correction. Uses ON CONFLICT to retain only the latest correction per email."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO corrections (message_id, email_subject, email_snippet, sender, recipient, cc, predicted_category, corrected_category, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(message_id) DO UPDATE SET
                    email_subject=excluded.email_subject,
                    email_snippet=excluded.email_snippet,
                    sender=excluded.sender,
                    recipient=excluded.recipient,
                    cc=excluded.cc,
                    predicted_category=excluded.predicted_category,
                    corrected_category=excluded.corrected_category,
                    timestamp=CURRENT_TIMESTAMP
            ''', (message_id, subject, snippet, sender, recipient, cc, predicted, corrected))
            conn.commit()
            
        if self.corrections_collection:
            doc_text = f"Subject: {subject}\nFrom: {sender}\nTo: {recipient}\nSnippet: {snippet}"
            metadata = {
                "message_id": message_id,
                "email_subject": subject,
                "email_snippet": snippet,
                "sender": sender,
                "recipient": recipient,
                "cc": cc,
                "predicted_category": predicted,
                "corrected_category": corrected
            }
            try:
                self.corrections_collection.upsert(
                    documents=[doc_text],
                    metadatas=[metadata],
                    ids=[message_id]
                )
            except Exception as e:
                import logging
                logging.getLogger("MailBot").error(f"Failed to upsert to ChromaDB: {e}")

    def get_recent_corrections(self, limit: int = 5) -> List[Dict]:
        """Fetches the most recent corrections to inject into the LLM system prompt."""
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM corrections ORDER BY timestamp DESC LIMIT ?', (limit,))
            return [dict(row) for row in cursor.fetchall()]

    def get_semantically_relevant_corrections(self, query_text: str, limit: int = 10) -> List[Dict]:
        """Fetches the most semantically relevant corrections from ChromaDB. Falls back to recent corrections."""
        if not self.corrections_collection:
            return self.get_recent_corrections(limit)
            
        try:
            # Query the collection
            # n_results cannot exceed the number of items in the collection, so we count first
            count = self.corrections_collection.count()
            if count == 0:
                return []
                
            actual_limit = min(limit, count)
            results = self.corrections_collection.query(
                query_texts=[query_text],
                n_results=actual_limit
            )
            
            if not results["metadatas"] or len(results["metadatas"]) == 0 or not results["metadatas"][0]:
                return []
                
            return results["metadatas"][0]
        except Exception as e:
            import logging
            logging.getLogger("MailBot").error(f"Failed to query ChromaDB: {e}")
            return self.get_recent_corrections(limit)


if __name__ == "__main__":
    # Small block to manually test functionality
    test_db_path = "test_inbox.db"
    
    # Ensure clean slate
    if os.path.exists(test_db_path):
        os.remove(test_db_path)
        
    db = Database(test_db_path)
    print(f"Initialized database at {test_db_path}")
    
    # Test adding an email
    db.add_or_update_email("msg_123", "suggested_routine", "Newsletter", "Here is your weekly news...", "sender@test.com", "me@test.com", "")
    print(f"Added email. Query: {db.get_email('msg_123')}")
    
    # Test updating status
    db.update_email_status("msg_123", "keep")
    print(f"Updated status. Query: {db.get_email('msg_123')}")
    
    # Test querying by status
    db.add_or_update_email("msg_456", "suggested_routine", "Spam", "Buy this product!", "spammer@spam.com", "me@test.com", "")
    emails = db.get_emails_by_status("suggested_routine")
    print(f"Emails with 'suggested_routine' status: {[e['message_id'] for e in emails]}")
    
    # Test removing email
    db.remove_email("msg_123")
    print(f"Removed email msg_123. Query: {db.get_email('msg_123')}")
    
    # Test adding correction
    db.add_correction("msg_789", "Job Offer", "We want to hire...", "rec@test.com", "me@test.com", "", "Routine", "Important")
    db.add_correction("msg_789", "Job Offer", "We want to hire...", "rec@test.com", "me@test.com", "", "Routine", "Quick_Reply") # Overwrite test
    
    corrections = db.get_recent_corrections(5)
    print(f"Recent corrections: {corrections}")
    
    # Cleanup test DB
    if os.path.exists(test_db_path):
        os.remove(test_db_path)
    print("Tests completed successfully.")
