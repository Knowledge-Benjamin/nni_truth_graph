import requests
import json
import base64
from io import BytesIO
from PIL import Image

def test_api():
    vision_url = "https://vision-server-qvem3ril2q-uc.a.run.app/embed_media"
    api_key = "eyekoidhnkldhlihijYYDhhshSycsHDhhUYykjskwehcjnh"
    
    # Generate an image to test (random noise image simulates an unclassified image, but let's test)
    img = Image.new('RGB', (224, 224), color='#f43f5e')
    buffered = BytesIO()
    img.save(buffered, format="JPEG")
    img_b64 = base64.b64encode(buffered.getvalue()).decode('utf-8')
    
    payload = {
        "image_base64": [img_b64]
    }
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }
    
    print("Pinging:", vision_url)
    resp = requests.post(vision_url, json=payload, headers=headers, timeout=120)
    print(f"Status: {resp.status_code}")
    print("Response JSON:")
    
    try:
        data = resp.json()
        print(json.dumps(data, indent=2))
    except Exception as e:
        print("Failed to decode JSON:", resp.text)

if __name__ == "__main__":
    test_api()
