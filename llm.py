import os
import logging
import time
from abc import ABC, abstractmethod
from google import genai

logger = logging.getLogger("MailBot")

class LLMClient(ABC):
    """
    Abstract base class for all LLM providers.
    Enforces a strict contract for generating responses.
    """
    @abstractmethod
    def generate_response(self, prompt: str) -> str:
        """
        Takes a prompt string and returns the LLM's string response.
        """
        pass


class GeminiClient(LLMClient):
    """
    Gemini implementation utilizing the modern `google-genai` SDK.
    Requires GEMINI_API_KEY in the environment variables.
    """
    def __init__(self, max_retries=3, backoff_factor=2.0):
        self.model_id = os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite")
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor

        # The client automatically authenticates using os.environ["GEMINI_API_KEY"]
        try:
            self.client = genai.Client()
        except Exception as e:
            raise RuntimeError(f"Failed to initialize Gemini Client. Is GEMINI_API_KEY set? Error: {e}")

    def generate_response(self, prompt: str) -> str:
        """
        Sends the prompt to the Gemini API and extracts the text response.
        Includes simple exponential backoff for transient API errors.
        """
        logger.debug(f"--- SENDING PROMPT TO LLM ---\n{prompt}\n-----------------------------")
        
        last_error = None
        for attempt in range(1, self.max_retries + 1):
            try:
                response = self.client.models.generate_content(
                    model=self.model_id,
                    contents=prompt
                )

                # noinspection PyTypeChecker
                return response.text

            except Exception as e:
                last_error = e
                if attempt < self.max_retries:
                    sleep_time = self.backoff_factor ** attempt
                    logger.warning(f"Gemini API error (attempt {attempt}/{self.max_retries}). Retrying in {sleep_time}s... Error: {e}")
                    time.sleep(sleep_time)
                else:
                    logger.error(f"Gemini API failed after {self.max_retries} attempts. Final error: {e}")
        
        raise RuntimeError(f"Failed to communicate with Gemini API after {self.max_retries} attempts: {last_error}")

    def generate_embeddings(self, texts: list[str]) -> list[list[float]]:
        """
        Sends texts to the Gemini API to generate embeddings.
        Includes simple exponential backoff for transient API errors.
        """
        if not texts:
            return []
            
        last_error = None
        for attempt in range(1, self.max_retries + 1):
            try:
                response = self.client.models.embed_content(
                    model="gemini-embedding-2",
                    contents=texts
                )
                # Google-genai response structure for embed_content has an embeddings property
                return [emb.values for emb in response.embeddings]

            except Exception as e:
                last_error = e
                if attempt < self.max_retries:
                    sleep_time = self.backoff_factor ** attempt
                    logger.warning(f"Gemini Embedding API error (attempt {attempt}/{self.max_retries}). Retrying in {sleep_time}s... Error: {e}")
                    time.sleep(sleep_time)
                else:
                    logger.error(f"Gemini Embedding API failed after {self.max_retries} attempts. Final error: {e}")
        
        raise RuntimeError(f"Failed to generate embeddings with Gemini API after {self.max_retries} attempts: {last_error}")
