import os
import requests
import json
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '../.env'))

SUNBIRD_API_KEY = os.getenv("SUNBIRD_API_KEY")
SUNBIRD_BASE_URL = "https://api.sunbird.ai"

class SunbirdClient:
    """Enterprise REST client for Sunbird AI Multilingual API."""

    @staticmethod
    def get_headers():
        return {
            "Authorization": f"Bearer {SUNBIRD_API_KEY}",
            "Content-Type": "application/json"
        }

    @staticmethod
    def translate_to_english(text: str, source_language: str = "") -> str:
        """
        Translates text from a local African language to English.
        If source_language is omitted, Sunbird might attempt auto-detection.
        Falls back to original text if API fails or key is missing.
        """
        if not SUNBIRD_API_KEY:
            print("      [SUNBIRD WARNING] SUNBIRD_API_KEY not set. Skipping translation.")
            return text

        try:
            payload = {
                "text": text,
                "target_language": "eng"
            }
            if source_language:
                payload["source_language"] = source_language

            response = requests.post(
                f"{SUNBIRD_BASE_URL}/tasks/translate",
                headers=SunbirdClient.get_headers(),
                json=payload,
                timeout=15
            )
            
            if response.status_code == 200:
                data = response.json()
                # Depending on actual API spec, adjust key extraction
                return data.get("text", data.get("translated_text", text))
            else:
                print(f"      [SUNBIRD ERROR] Translation failed: {response.status_code} - {response.text}")
                return text
        except Exception as e:
            print(f"      [SUNBIRD ERROR] Translation Exception: {e}")
            return text

    @staticmethod
    def transcribe_audio(file_path: str, language: str = "") -> str:
        """
        Transcribes audio from local African dialects to text (STT) via Sunbird.
        """
        if not SUNBIRD_API_KEY:
            raise ValueError("SUNBIRD_API_KEY is not set.")

        try:
            # For STT, send multipart/form-data
            headers = {"Authorization": f"Bearer {SUNBIRD_API_KEY}"}
            
            data = {}
            if language:
                data["language"] = language

            with open(file_path, 'rb') as f:
                files = {'file': (os.path.basename(file_path), f, 'audio/mpeg')}
                response = requests.post(
                    f"{SUNBIRD_BASE_URL}/tasks/stt",
                    headers=headers,
                    files=files,
                    data=data,
                    timeout=60
                )
            
            if response.status_code == 200:
                resp_json = response.json()
                return resp_json.get("text", "")
            else:
                raise Exception(f"API Error {response.status_code}: {response.text}")
        except Exception as e:
            raise Exception(f"Sunbird STT Exception: {e}")
