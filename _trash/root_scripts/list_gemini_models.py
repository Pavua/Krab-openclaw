import asyncio
import os
import google.generativeai as genai

async def main():
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("Error: GEMINI_API_KEY not found.")
        return

    genai.configure(api_key=api_key)
    
    print("Listing available models...")
    try:
        # Use simple list_models first
        models = list(genai.list_models())
        print(f"Found {len(models)} models:")
        for m in models:
            if 'generateContent' in m.supported_generation_methods:
                print(f"- {m.name}")
    except Exception as e:
        print(f"Error listing models: {e}")

if __name__ == "__main__":
    if "GEMINI_API_KEY" not in os.environ:
        # Try to load from .env if not present
        try:
            from dotenv import load_dotenv
            load_dotenv()
        except ImportError:
            pass
            
    asyncio.run(main())
