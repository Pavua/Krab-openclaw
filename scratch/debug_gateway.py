import requests
import json
import os

url = "http://localhost:18789/v1/chat/completions"
# url = "http://localhost:18789" # Test root
payload = {
    "model": "google/gemini-2.0-flash-exp",
    "messages": [{"role": "user", "content": "Hello"}]
}
headers = {
    # "Authorization": "Bearer sk-nexus-bridge", # Intentionally matching .env
    "Content-Type": "application/json"
}

print(f"--- POST {url} ---")
try:
    r = requests.post(url, json=payload, headers=headers)
    print(f"Status: {r.status_code}")
    print(f"Headers: {r.headers}")
    print(f"Body: {r.text}")
except Exception as e:
    print(f"Error: {e}")

# url_get = "http://localhost:18789/v1/chat/completions"
# print(f"\n--- GET {url_get} ---")
# try:
#     r = requests.get(url_get, headers=headers)
#     print(f"Status: {r.status_code}")
#     print(f"Body: {r.text}")
# except Exception as e:
#     print(f"Error: {e}")
