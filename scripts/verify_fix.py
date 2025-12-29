import requests
import json
import sys

API_URL = "http://localhost:3000/api/query/natural"
QUERY = "what did trump say about venezuela?"

def verify():
    print(f"Testing API: {API_URL}")
    print(f"Query: {QUERY}")
    
    try:
        response = requests.post(API_URL, json={"query": QUERY})
        
        if response.status_code != 200:
            print(f"❌ API Error: {response.status_code}")
            print(response.text)
            return
            
        data = response.json()
        
        print("\nChecking Response Structure...")
        
        has_analysis = "analysis" in data
        has_results = "results" in data
        
        if has_analysis:
            print("✅ 'analysis' field FOUND.")
            print(f"Analysis content: {data['analysis'][:100]}...")
        else:
            print("❌ 'analysis' field MISSING. (Server might need restart)")
            
        if has_results:
            results = data['results']
            print(f"✅ Found {len(results)} results.")
            
            # Check for HTML in first result
            if len(results) > 0:
                first_stmt = results[0].get('statement', '')
                if '<' in first_stmt and '>' in first_stmt:
                    print(f"⚠️ Warning: Potential HTML found in result: {first_stmt[:50]}...")
                else:
                    print("✅ First result appears clean (no obvious HTML tags).")
        
    except Exception as e:
        print(f"❌ Connection Failed: {e}")
        print("Ensure server is running on localhost:3000")

if __name__ == "__main__":
    verify()
