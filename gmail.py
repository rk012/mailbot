import os
import base64
import time
from typing import List, Dict
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# If modifying these scopes, delete the file token.json.
SCOPES = [
    'https://www.googleapis.com/auth/gmail.modify',
    'https://www.googleapis.com/auth/userinfo.profile',
    'https://www.googleapis.com/auth/userinfo.email'
]

# Relax OAuth scope requirements since Google often appends 'openid' automatically
os.environ['OAUTHLIB_RELAX_TOKEN_SCOPE'] = '1'

def retry_on_error(max_retries=3, base_delay=1):
    def decorator(func):
        def wrapper(*args, **kwargs):
            retries = 0
            while True:
                try:
                    return func(*args, **kwargs)
                except HttpError as e:
                    if e.resp.status in [429, 500, 503] and retries < max_retries:
                        time.sleep(base_delay * (2 ** retries))
                        retries += 1
                    else:
                        raise e
                except Exception as e:
                    if retries < max_retries:
                        time.sleep(base_delay * (2 ** retries))
                        retries += 1
                    else:
                        raise e
        return wrapper
    return decorator


class GmailClient:
    def __init__(self, credentials_path="credentials.json", token_path="token.json"):
        self.credentials_path = credentials_path
        self.token_path = token_path
        self.creds = None
        self.service = None
        self._authenticate()

    def _authenticate(self):
        """Handles the OAuth2 flow and initializes the Gmail service."""
        if os.path.exists(self.token_path):
            self.creds = Credentials.from_authorized_user_file(self.token_path, SCOPES)
            
        # If there are no (valid) credentials available, let the user log in.
        if not self.creds or not self.creds.valid:
            if self.creds and self.creds.expired and self.creds.refresh_token:
                self.creds.refresh(Request())
            else:
                if not os.path.exists(self.credentials_path):
                    raise FileNotFoundError(f"Missing {self.credentials_path}. Please download it from GCP.")
                flow = InstalledAppFlow.from_client_secrets_file(self.credentials_path, SCOPES)
                # Open browser for authentication
                self.creds = flow.run_local_server(port=0)
            # Save the credentials for the next run
            token_parent = Path(self.token_path).parent
            if str(token_parent) != ".":
                token_parent.mkdir(parents=True, exist_ok=True)
            with open(self.token_path, 'w') as token:
                token.write(self.creds.to_json())
                
        self.service = build('gmail', 'v1', credentials=self.creds)

    def _get_header(self, headers: list, name: str) -> str:
        """Helper to extract a specific header from the Gmail API response."""
        for header in headers:
            if header['name'].lower() == name.lower():
                return header['value']
        return ""

    def _extract_body(self, payload: dict) -> str:
        """Recursively extracts the plain text body from the payload."""
        if 'parts' in payload:
            for part in payload['parts']:
                if part['mimeType'] == 'text/plain':
                    data = part['body'].get('data', '')
                    return base64.urlsafe_b64decode(data).decode('utf-8')
                elif 'parts' in part:
                    # Recursive call for nested parts
                    nested_body = self._extract_body(part)
                    if nested_body:
                        return nested_body
        else:
            # Simple, non-multipart email
            if payload.get('mimeType') == 'text/plain':
                data = payload['body'].get('data', '')
                return base64.urlsafe_b64decode(data).decode('utf-8')
            elif payload.get('mimeType') == 'text/html':
                # Fallback to HTML if no plain text is available (might have markup)
                data = payload['body'].get('data', '')
                return base64.urlsafe_b64decode(data).decode('utf-8')
        return ""

    @retry_on_error()
    def get_user_info(self) -> dict:
        """Fetches the authenticated user's email and name via the OAuth2 API."""
        oauth2_service = build('oauth2', 'v2', credentials=self.creds)
        user_info = oauth2_service.userinfo().get().execute()
        return {
            'email': user_info.get('email', ''),
            'name': user_info.get('name', 'User')
        }

    @retry_on_error()
    def get_inbox_emails(self, limit: int = 50, unread_only: bool = True) -> List[Dict]:
        """Fetches recent emails from the inbox."""
        label_ids = ['INBOX']
        if unread_only:
            label_ids.append('UNREAD')
        results = self.service.users().messages().list(userId='me', labelIds=label_ids, maxResults=limit).execute()
        messages = results.get('messages', [])
        
        email_data = []
        seen_threads = set()
        
        for msg in messages:
            msg_id = msg['id']
            full_msg = self.service.users().messages().get(userId='me', id=msg_id, format='full').execute()
            
            thread_id = full_msg.get('threadId')
            if thread_id in seen_threads:
                continue
            if thread_id:
                seen_threads.add(thread_id)
            
            payload = full_msg['payload']
            headers = payload.get('headers', [])
            
            subject = self._get_header(headers, 'Subject')
            sender = self._get_header(headers, 'From')
            recipient = self._get_header(headers, 'To')
            cc = self._get_header(headers, 'Cc')
            body = self._extract_body(payload)
            internal_date = None
            internal_date_ms = full_msg.get('internalDate')
            if internal_date_ms:
                internal_date = datetime.fromtimestamp(
                    int(internal_date_ms) / 1000,
                    tz=timezone.utc,
                )
            
            email_data.append({
                'message_id': msg_id,
                'thread_id': thread_id,
                'subject': subject,
                'sender': sender,
                'recipient': recipient,
                'cc': cc,
                'body': body,
                'internal_date': internal_date,
                'label_ids': full_msg.get('labelIds', []),
            })
            
        return email_data

    @retry_on_error()
    def modify_email_state(self, message_id: str, action: str):
        """Modifies the labels of a specific email."""
        action = action.lower()
        body_payload = {}
        
        if action == "read":
            body_payload = {'removeLabelIds': ['UNREAD']}
        elif action == "unread":
            body_payload = {'addLabelIds': ['UNREAD']}
        elif action == "archive":
            body_payload = {'removeLabelIds': ['INBOX']}
        else:
            raise ValueError(f"Invalid action '{action}'. Must be 'read', 'unread', or 'archive'.")
            
        self.service.users().messages().modify(userId='me', id=message_id, body=body_payload).execute()

    @retry_on_error()
    def create_draft_reply(self, message_id: str, draft_text: str):
        """Creates a perfectly threaded draft reply to a given email."""
        # Fetch the original email to get threading headers
        original_msg = self.service.users().messages().get(userId='me', id=message_id, format='metadata').execute()
        headers = original_msg['payload']['headers']
        
        orig_subject = self._get_header(headers, 'Subject')
        orig_from = self._get_header(headers, 'From')
        orig_msg_id = self._get_header(headers, 'Message-ID')
        orig_references = self._get_header(headers, 'References')
        
        # Build the reply message
        message = EmailMessage()
        message.set_content(draft_text)
        
        message['To'] = orig_from
        message['Subject'] = orig_subject if orig_subject.lower().startswith('re:') else f"Re: {orig_subject}"
        message['In-Reply-To'] = orig_msg_id
        
        references = orig_references + " " + orig_msg_id if orig_references else orig_msg_id
        message['References'] = references

        # Base64 encode the MIME message
        encoded_message = base64.urlsafe_b64encode(message.as_bytes()).decode()

        create_message = {
            'message': {
                'raw': encoded_message,
                'threadId': original_msg['threadId']
            }
        }

        self.service.users().drafts().create(userId='me', body=create_message).execute()


if __name__ == "__main__":
    # Small block to manually test authentication and fetching
    print("Initializing Gmail Client...")
    try:
        client = GmailClient()
        print("Authentication successful!")
        
        print("\nFetching top 3 emails from Inbox...")
        emails = client.get_inbox_emails(limit=3)
        for i, email in enumerate(emails, 1):
            # Print subject and a snippet of the body to verify parsing works
            clean_body = email['body'].replace('\r', '').replace('\n', ' ') if email['body'] else ""
            body_snippet = clean_body[:100] + "..." if clean_body else "[No plain text body found]"
            print(f"{i}. ID: {email['message_id']}")
            print(f"   From: {email['sender']}")
            print(f"   Subject: {email['subject']}")
            print(f"   Snippet: {body_snippet}\n")
            
    except Exception as e:
        print(f"Error during execution: {e}")
