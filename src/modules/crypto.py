# -*- coding: utf-8 -*-
"""
Crypto Intelligence Module (Phase 9.4).
Provides real-time crypto data via CoinGecko API (Free Tier).
"""

import httpx
import structlog
from typing import Dict, Any, Optional

logger = structlog.get_logger("CryptoIntel")

class CryptoIntel:
    BASE_URL = "https://api.coingecko.com/api/v3"
    
    def __init__(self):
        self.client = httpx.AsyncClient(timeout=10.0)
        self._cache = {}

    async def get_price(self, coin_id: str, currency: str = "usd") -> Dict[str, Any]:
        """
        Get current price for a coin.
        Usage: get_price("bitcoin", "usd")
        """
        try:
            url = f"{self.BASE_URL}/simple/price"
            params = {
                "ids": coin_id,
                "vs_currencies": currency,
                "include_24hr_change": "true",
                "include_last_updated_at": "true"
            }
            resp = await self.client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            
            if coin_id not in data:
                return {"error": f"Coin '{coin_id}' not found"}
                
            return data[coin_id]
        except Exception as e:
            logger.error(f"Crypto API Error: {e}")
            return {"error": str(e)}

    async def get_coin_info(self, coin_id: str) -> Dict[str, Any]:
        """Get detailed info (market cap, rank, description)."""
        try:
            url = f"{self.BASE_URL}/coins/{coin_id}"
            params = {
                "localization": "false",
                "tickers": "false",
                "market_data": "true",
                "community_data": "false",
                "developer_data": "false"
            }
            resp = await self.client.get(url, params=params)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"Crypto Info Error: {e}")
            return {"error": str(e)}

    async def search(self, query: str) -> list:
        """Search for a coin by name/symbol."""
        try:
            url = f"{self.BASE_URL}/search"
            params = {"query": query}
            resp = await self.client.get(url, params=params)
            resp.raise_for_status()
            return resp.json().get("coins", [])
        except Exception as e:
            logger.error(f"Crypto Search Error: {e}")
            return []

    async def close(self):
        await self.client.aclose()
