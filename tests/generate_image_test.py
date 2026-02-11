import sys
import os
import asyncio

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.modules.image_gen import ImageGenerator

async def test_gen():
    print("🎨 Initializing ImageGenerator...")
    generator = ImageGenerator()
    
    print("📡 Checking ComfyUI Health...")
    if not await generator.client.check_health():
        print("❌ ComfyUI is offline! Start it first.")
        return

    print("✨ Generating image: 'A cyberpunk cat in neon city'...")
    image_data = await generator.generate("A cyberpunk cat in neon city")
    
    if image_data:
        output_path = "test_gen_result.png"
        with open(output_path, "wb") as f:
            f.write(image_data)
        print(f"✅ Success! Image saved to {output_path}")
        print(f"📦 Size: {len(image_data)} bytes")
    else:
        print("❌ Generation failed (returned None).")

if __name__ == "__main__":
    asyncio.run(test_gen())
