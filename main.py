from dotenv import load_dotenv

from llm import GeminiClient, LLMClient

if __name__ == "__main__":
    load_dotenv()

    llm: LLMClient = GeminiClient()
