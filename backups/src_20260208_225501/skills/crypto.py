"""
Crypto Skill - Получение курсов криптовалют
"""
import httpx

async def get_crypto_price(symbol: str = "bitcoin") -> str:
    """Получает текущую цену криптовалюты"""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"https://api.coingecko.com/api/v3/simple/price?ids={symbol}&vs_currencies=usd"
            )
            data = response.json()
            if symbol in data:
                price = data[symbol]['usd']
                return f"💰 {symbol.upper()}: ${price}"
            return f"❌ Не нашел {symbol}"
    except Exception as e:
        return f"Error: {str(e)}"
