import os
from dotenv import load_dotenv
import google.generativeai as genai

# Load environment
load_dotenv()

api_key = os.getenv("GEMINI_API_KEY")
print(f"Testing API Key: {api_key[:20]}...")

try:
    # Configure Gemini
    genai.configure(api_key=api_key)
    
    # Test with a simple query
    model = genai.GenerativeModel('gemini-2.0-flash-exp')
    
    response = model.generate_content("Say 'API key works!' if you can read this")
    
    print("\n✅ SUCCESS! Gemini API Key is valid!")
    print(f"Response: {response.text}")
    
except Exception as e:
    print(f"\n❌ FAILED! API Key test failed:")
    print(f"Error: {e}")
