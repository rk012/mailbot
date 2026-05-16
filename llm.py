import os
import logging
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
    def __init__(self):
        self.model_id = os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite")

        # The client automatically authenticates using os.environ["GEMINI_API_KEY"]
        try:
            self.client = genai.Client()
        except Exception as e:
            raise RuntimeError(f"Failed to initialize Gemini Client. Is GEMINI_API_KEY set? Error: {e}")

    def generate_response(self, prompt: str) -> str:
        """
        Sends the prompt to the Gemini API and extracts the text response.
        """
        logger.debug(f"--- SENDING PROMPT TO LLM ---\n{prompt}\n-----------------------------")
        try:
            response = self.client.models.generate_content(
                model=self.model_id,
                contents=prompt
            )

            # noinspection PyTypeChecker
            return response.text

        except Exception as e:
            return f"Error communicating with Gemini API: {e}"
