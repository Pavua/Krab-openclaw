import os
import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()

api_key = os.getenv("GEMINI_API_KEY")
print(f"Checking Key: {api_key[:5]}...{api_key[-5:]}")

if not api_key:
    print("❌ No API Key found")
    exit(1)

genai.configure(api_key=api_key)
model = genai.GenerativeModel('gemini-pro')

try:
    response = model.generate_content("Hello, do you work?")
    print(f"✅ Success! Response: {response.text}")
except Exception as e:
    print(f"❌ Error: {e}")
