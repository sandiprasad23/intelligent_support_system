import os, requests
from dotenv import load_dotenv
load_dotenv()

response = requests.post(
    "https://openrouter.ai/api/v1/chat/completions",
    headers={"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}"},
    json={"model": os.environ["MODEL_NAME"],
          "messages": [{"role": "user", "content": "Reply with the word ready."}]},
    timeout=30,
)
print(response.status_code)
print(response.json())