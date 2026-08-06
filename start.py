import httpx
from langchain_openai import ChatOpenAI

client = httpx.Client(verify=False)

llm = ChatOpenAI(
    base_url="https://genailab.tcs.in",
    model="azure/genailab-maas-gpt-4.1",
    api_key="sk-taPdt4_aNdzmFCX3nP0GiA",
    http_client=client
)

try:
    response = llm.invoke("Hi! Are you online?")
    print("CONNECTION SUCCESSFUL!")
    print("AI Response:", response.content)
except Exception as e:
    print("ERROR: Could not connect. Check your key or internet.")
    print(f"Details: {e}")