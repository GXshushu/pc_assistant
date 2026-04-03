import openai
from loguru import logger
import os

class AIEngine:
    def __init__(self, api_key=None, base_url=None):
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.base_url = base_url or os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
        self.client = openai.OpenAI(api_key=self.api_key, base_url=self.base_url)

    def chat_with_ai(self, user_query, model="gpt-4o"):
        """Sends a query to the AI and returns the response."""
        try:
            response = self.client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "You are a helpful PC assistant. Help users with system tasks, file cleaning, and technical advice."},
                    {"role": "user", "content": user_query}
                ]
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"Error chatting with AI: {e}")
            return f"Error: {e}"

if __name__ == "__main__":
    # Example usage:
    # ai = AIEngine()
    # print(ai.chat_with_ai("How can I clean my computer?"))
    pass
