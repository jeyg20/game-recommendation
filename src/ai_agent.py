import os
from http import client

from dotenv import load_dotenv
from google import genai
from urllib3 import response

load_dotenv()
gemini_api = os.getenv("GEMINI_API")


client = genai.Client(api_key=gemini_api)

response = client.models.generate_content(
    model="gemini-3.1-flash-lite",
    contents="Hello form my personal project",
)


print(response.text)
