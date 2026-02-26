# Test MCP imports
try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    print("✅ MCP imports successful")
except ImportError as e:
    print(f"❌ MCP imports failed: {e}")
except Exception as e:
    print(f"❌ Unexpected error: {e}")
