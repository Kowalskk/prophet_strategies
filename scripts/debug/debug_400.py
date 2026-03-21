import os
import requests
from dotenv import load_dotenv

load_dotenv()

api_key = os.getenv("DUNE_API_KEY")
url = "https://api.dune.com/api/v1/query"
headers = {"x-dune-api-key": api_key, "Content-Type": "application/json"}

# Test standard query creation
body = {
    "name": "prophet_test_binary",
    "query": "SELECT 1",
    "is_private": True
}

print(f"Testing Dune API with key length: {len(api_key) if api_key else 0}")
resp = requests.post(url, json=body, headers=headers)
print(f"Status: {resp.status_code}")
print(f"Response: {resp.text}")

if resp.status_code == 400:
    print("\nAttempting without 'is_private'...")
    body.pop("is_private")
    resp = requests.post(url, json=body, headers=headers)
    print(f"Status: {resp.status_code}")
    print(f"Response: {resp.text}")
