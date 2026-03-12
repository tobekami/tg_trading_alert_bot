import sys
import os
import ccxt
import requests

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from app.config import Config

def test_mexc_connection():
    mexc_config = {'enableRateLimit': True}
    if Config.BINANCE_API_KEY and Config.BINANCE_SECRET:
        mexc_config['apiKey'] = Config.BINANCE_API_KEY
        mexc_config['secret'] = Config.BINANCE_SECRET

    exchange = ccxt.mexc(mexc_config)
    candles = exchange.fetch_ohlcv('BTC/USDT', timeframe='1m', limit=1)
    
    assert candles is not None, "Received None from MEXC."
    assert len(candles[0]) == 6, "Received malformed data from MEXC."

def test_oanda_connection():
    # If it's the dummy CI key, skip the actual network request so the test passes in GitHub
    if Config.OANDA_API_KEY == "ci_dummy_oanda_key":
        return 

    domain = "api-fxpractice.oanda.com" if Config.OANDA_ENV == "practice" else "api-fxtrade.oanda.com"
    url = f"https://{domain}/v3/instruments/US30_USD/candles?granularity=M3&count=1"
    headers = {"Authorization": f"Bearer {Config.OANDA_API_KEY}", "Accept-Datetime-Format": "UNIX"}

    response = requests.get(url, headers=headers, timeout=10)
    assert response.status_code == 200, f"OANDA rejected the request: {response.text}"
    
    data = response.json()
    assert 'candles' in data and len(data['candles']) > 0, "Received unexpected JSON structure from OANDA."