import aiohttp
import json
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

class OpenClawClient:
    """
    Client for interacting with the OpenClaw Gateway.
    Acts as a bridge between Krab (Userbot) and OpenClaw (Engine).
    """
    def __init__(self, base_url: str = "http://localhost:18789", api_key: Optional[str] = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.headers = {
            "Content-Type": "application/json",
            "User-Agent": "Krab/6.0 (OpenClaw-Client)"
        }
        if self.api_key:
            self.headers["Authorization"] = f"Bearer {self.api_key}"

    async def health_check(self) -> bool:
        """Checks if OpenClaw Gateway is reachable."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self.base_url}/health", headers=self.headers, timeout=2) as resp:
                    return resp.status == 200
        except Exception as e:
            logger.warning(f"OpenClaw Health Check failed: {e}")
            return False

    async def invoke_tool(self, tool_name: str, args: dict) -> dict:
        """
        Invokes a tool on OpenClaw Gateway.
        
        Args:
            tool_name: The name of the tool (e.g., 'web_search').
            args: The arguments for the tool.
            
        Returns:
            The tool's output as a dictionary.
        """
        payload = {
            "tool": tool_name,
            "args": args
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.base_url}/tools/invoke", 
                    json=payload, 
                    headers=self.headers,
                    timeout=30
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data.get("ok"):
                            return data.get("result", {})
                        else:
                            error = data.get("error", "Unknown Error")
                            logger.error(f"OpenClaw Tool Error: {error}")
                            return {"error": str(error)}
                    else:
                        error_text = await resp.text()
                        logger.error(f"OpenClaw Tool HTTP Error ({resp.status}): {error_text}")
                        return {"error": f"HTTP {resp.status}: {error_text}"}
                        
        except Exception as e:
            logger.error(f"Failed to invoke tool {tool_name}: {e}")
            return {"error": str(e)}

    async def chat_completions(self, messages: list, model: str = "google/gemini-2.0-flash-exp") -> str:
        """
        Sends a chat completion request to OpenClaw Gateway.
        """
        payload = {
            "model": model,
            "messages": messages,
            "stream": False
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.base_url}/v1/chat/completions",
                    json=payload,
                    headers=self.headers,
                    timeout=60
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data['choices'][0]['message']['content']
                    else:
                        error_text = await resp.text()
                        logger.error(f"OpenClaw Chat Error ({resp.status}): {error_text}")
                        return f"⚠️ OpenClaw Error: {resp.status}"

        except Exception as e:
            logger.error(f"Failed to get chat completion: {e}")
            return f"⚠️ Connection Error: {e}"

    async def execute_agent_task(self, query: str, agent_id: str = "researcher") -> str:
        """
        Orchestrates a research task using functionality from OpenClaw.
        Current implementation uses 'web_search' tool + LLM synthesis.
        
        Args:
            query: The user's prompt/task.
            agent_id: 'research_fast' or 'research_deep' (affects search count/depth).
            
        Returns:
            The agent's text response.
        """
        # 1. Search Param Config
        count = 5 if agent_id == "research_fast" else 10
        
        # 2. Invoke Web Search Tool
        logger.info(f"OpenClawClient: Searching for '{query}'...")
        search_results = await self.invoke_tool("web_search", {
            "query": query, 
            "count": count
        })
        
        if "error" in search_results:
            return f"⚠️ Search Failed: {search_results['error']}"
            
        # 3. Format Results for LLM
        # OpenClaw tools return structure: {'content': [...], 'details': {'results': [...]}}
        results_data = search_results.get("details", {}).get("results", [])
        
        # Fallback if details is missing but content has JSON
        if not results_data and "content" in search_results:
             try:
                 import json
                 text = search_results["content"][0]["text"]
                 parsed = json.loads(text)
                 results_data = parsed.get("results", [])
             except:
                 pass

        if not results_data:
            return "⚠️ No search results found."
            
        context = "Search Results:\n"
        for i, res in enumerate(results_data, 1):
             # Ensure dict access is safe
            if isinstance(res, dict):
                title = res.get('title', 'No Title').replace("<<<EXTERNAL_UNTRUSTED_CONTENT>>>", "").replace("<<<END_EXTERNAL_UNTRUSTED_CONTENT>>>", "").replace("Source: Web Search", "").replace("---", "").strip()
                url = res.get('url', '#')
                description = res.get('description', 'No description').replace("<<<EXTERNAL_UNTRUSTED_CONTENT>>>", "").replace("<<<END_EXTERNAL_UNTRUSTED_CONTENT>>>", "").replace("Source: Web Search", "").replace("---", "").strip()
                context += f"{i}. [{title}]({url})\n"
                context += f"   {description}\n\n"
            else:
                 context += f"{i}. {str(res)}\n"

        # 4. Synthesize Answer
        prompt = (
            f"User Query: {query}\n\n"
            f"{context}\n\n"
            "Based on the search results above, answer the user query comprehensively. "
            "Cite sources naturally using [Title](URL) format. If results are insufficient, say so."
        )
        
        messages = [
            {"role": "system", "content": "You are a helpful research assistant."},
            {"role": "user", "content": prompt}
        ]
        
        return await self.chat_completions(messages)


    async def search(self, query: str) -> str:
        """Shortcut for the research agent."""
        return await self.execute_agent_task(query, agent_id="research")
