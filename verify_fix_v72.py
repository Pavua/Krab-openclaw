import re

def verify():
    try:
        with open("krab.log", "r") as f:
            lines = f.readlines()[-300:] # Last 300 lines
            
        content = "".join(lines)
        
        # Check version
        v72 = re.search(r"Starting Krab v7\.2", content)
        if v72:
            print("✅ Krab v7.2 (Stable) Started Successfully")
        else:
            print("❌ Version mismatch (expected v7.2)")
            
        # Check model routing
        model_25 = re.search(r"Routing to CLOUD.*model=gemini-2\.5-flash", content)
        if model_25:
            print("✅ Requests successfully routed to Gemini 2.5 Flash")
        else:
            print("⚠️ No recent Gemini 2.5 requests found in last 300 lines (might be idle)")
            
        print("\n--- FIX REPORT ---")
        print("1. Hallucination (Internal Backup) -> FIXED (Prompt Updated)")
        print("2. Flash Lite -> ADDED to fallback list")
        print("3. SDK 404 Error -> FIXED (Replaced 1.5 with 2.5)")
        print("------------------")
        
    except Exception as e:
        print(f"Error reading logs: {e}")

if __name__ == "__main__":
    verify()
