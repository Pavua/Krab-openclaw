---
name: crypto
description: Get current crypto prices and market data using free CoinGecko API.
metadata: { "openclaw": { "emoji": "🪙", "requires": { "bins": ["curl"] } } }
---

# Crypto Market Data

Free API (CoinGecko), no key required for basic use.

## Check Price (Detailed)
Returns current price, market cap, and 24h change for a specific coin.

```bash
# Usage: currency is optional, defaults to usd
# Examples: bitcoin, ethereum, solana, monero
ID="bitcoin"
curl -s "https://api.coingecko.com/api/v3/simple/price?ids=$ID&vs_currencies=usd,eur,rub&include_market_cap=true&include_24hr_change=true"
```

## Trending Coins
See what's hot right now.

```bash
curl -s "https://api.coingecko.com/api/v3/search/trending"
```

## Global Market Data
Total market cap and volume.

```bash
curl -s "https://api.coingecko.com/api/v3/global"
```
