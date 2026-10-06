import os
from dotenv import load_dotenv

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

print("IRIS configuration loaded")
print("OpenAI API key:", "Configured" if OPENAI_API_KEY else "Not configured")
