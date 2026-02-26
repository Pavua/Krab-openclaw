
import os
import sys
import asyncio
import logging
from unittest.mock import MagicMock, patch

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("VerifyVision")

# Add src to path
sys.path.append(os.getcwd())

# Mock RAGEngine to avoid ChromaDB connection
sys.modules["src.core.rag_engine"] = MagicMock()

async def test_local_health_check_probing():
    print("\n--- Testing ModelRouter.check_local_health Probing ---")
    from src.core.model_manager import ModelRouter
    
    config = {
        "LM_STUDIO_URL": "http://localhost:1234",  # Intentionally missing /v1
        "GEMINI_API_KEY": "fake_key"
    }
    
    router = ModelRouter(config)
    
    # Mock aiohttp.ClientSession
    with patch("aiohttp.ClientSession") as mock_session_cls:
        # Session context manager
        mock_session = MagicMock()
        mock_session_cls.return_value.__aenter__.return_value = mock_session
        
        # Get context manager
        mock_get = MagicMock()
        mock_session.get.return_value = mock_get
        
        # Response object
        mock_response = MagicMock()
        mock_response.status = 200
        # make json() awaitable
        f = asyncio.Future()
        f.set_result({"data": [{"id": "local-model-v1"}]})
        mock_response.json.return_value = f
        
        # Enter response context
        mock_get.__aenter__.return_value = mock_response

        # Test probing logic
        # Expectation: It should try http://localhost:1234/v1 and succeed
        success = await router.check_local_health(force=True)
        
        if success and router.lm_studio_url == "http://localhost:1234/v1":
            print("✅ check_local_health correctly probed and updated URL to http://localhost:1234/v1")
        else:
            print(f"❌ Failed to probe/update URL. Current: {router.lm_studio_url}, Success: {success}")

async def test_perceptor_vision_migration():
    print("\n--- Testing Perceptor Vision (google-genai SDK) ---")
    from src.modules.perceptor import Perceptor
    
    config = {
        "GEMINI_API_KEY": "fake_key",
        "WHISPER_MODEL": "fake_whisper"
    }
    
    perceptor = Perceptor(config)
    
    # Mock google.genai
    with patch("src.modules.perceptor.genai") as mock_genai:
        if mock_genai is None:
            print("⚠️ google.genai not imported in Perceptor (ImportError?)")
            return

        mock_client = mock_genai.Client.return_value
        mock_response = MagicMock()
        mock_response.text = "Vision Analysis Result"
        mock_client.models.generate_content.return_value = mock_response
        
        # Create a dummy image file
        with open("test_image.jpg", "wb") as f:
            f.write(b"fake image content")
            
        try:
            # Mock Image.open to avoid actually opening the fake file
            with patch("PIL.Image.open") as mock_img_open:
                mock_img_open.return_value = MagicMock()
                
                # Test analyze_visual
                router_mock = MagicMock()
                router_mock.gemini_key = "fake_key"
                
                result = await perceptor.analyze_visual("test_image.jpg", "Describe this")
                
                if result == "Vision Analysis Result":
                    print("✅ analyze_visual successfully called genai.Client.models.generate_content")
                else:
                    print(f"❌ analyze_visual returned unexpected result: {result}")
                    
                # Test analyze_image (wrapper)
                result_img = await perceptor.analyze_image("test_image.jpg", router_mock, "Describe this")
                if result_img == "Vision Analysis Result":
                    print("✅ analyze_image successfully called genai.Client.models.generate_content")
                else:
                    print(f"❌ analyze_image returned unexpected result: {result_img}")

        finally:
            if os.path.exists("test_image.jpg"):
                os.remove("test_image.jpg")

async def main():
    await test_local_health_check_probing()
    await test_perceptor_vision_migration()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except ImportError as e:
        print(f"❌ Import Error: {e}. Make sure dependencies are installed.")
    except Exception as e:
        print(f"❌ Unexpected Error: {e}")
