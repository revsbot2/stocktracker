import yfinance as yf
import requests
from datetime import datetime

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
}

REC_MAP = {
    'strong_buy': 'Strong Buy',
    'buy': 'Buy',
    'hold': 'Hold',
    'underperform': 'Underperform',
    'sell': 'Sell',
}


def get_asset_data(symbol):
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.info

        # Bail early if Yahoo returned a truly empty/error response
        has_price = info.get('currentPrice') or info.get('regularMarketPrice') or info.get('previousClose')
        has_identity = info.get('quoteType') or info.get('symbol') or info.get('shortName')
        if not info or (not has_price and not has_identity):
            return None

        asset_type = info.get('quoteType') or 'EQUITY'

        price = (
            info.get('currentPrice')
            or info.get('regularMarketPrice')
            or info.get('navPrice')
            or info.get('previousClose')
        )

        # Calculate change % reliably from prev close
        prev_close = info.get('previousClose')
        if price and prev_close and prev_close != 0:
            change_pct = ((price - prev_close) / prev_close) * 100
        else:
            change_pct = None

        rec_key = (info.get('recommendationKey') or '').lower()
        analyst_consensus = REC_MAP.get(rec_key, rec_key.replace('_', ' ').title() if rec_key else None)

        data = {
            'symbol': symbol.upper(),
            'name': info.get('longName') or info.get('shortName', symbol.upper()),
            'asset_type': asset_type,
            'price': price,
            'currency': info.get('currency', 'USD'),
            'change_pct': change_pct,
            'market_cap': info.get('marketCap'),
            'pe_ratio': info.get('trailingPE') or info.get('forwardPE'),
            'peg_ratio': info.get('pegRatio') or info.get('trailingPegRatio'),
            'target_price': info.get('targetMeanPrice'),
            'target_high': info.get('targetHighPrice'),
            'target_low': info.get('targetLowPrice'),
            'analyst_consensus': analyst_consensus,
            'num_analysts': info.get('numberOfAnalystOpinions'),
            'sector': info.get('sector'),
            'industry': info.get('industry'),
            'volume': info.get('volume') or info.get('regularMarketVolume'),
            'avg_volume': info.get('averageVolume'),
            'fifty_two_week_high': info.get('fiftyTwoWeekHigh'),
            'fifty_two_week_low': info.get('fiftyTwoWeekLow'),
            'dividend_yield': info.get('dividendYield'),
            'beta': info.get('beta'),
            'eps': info.get('trailingEps'),
            'forward_eps': info.get('forwardEps'),
            'description': (info.get('longBusinessSummary') or '')[:600],
            'website': info.get('website', ''),
            'country': info.get('country', ''),
            'employees': info.get('fullTimeEmployees'),
            'last_updated': datetime.now().isoformat(),
            'news': [],
        }

        # News
        try:
            news_items = ticker.news or []
            data['news'] = [
                {
                    'title': item.get('title', ''),
                    'publisher': item.get('publisher', ''),
                    'link': item.get('link', ''),
                    'published': item.get('providerPublishTime', 0),
                }
                for item in news_items[:8]
                if item.get('title')
            ]
        except Exception:
            pass

        return data

    except Exception as e:
        print(f"[data_fetcher] Error fetching {symbol}: {e}")
        return None


def search_tickers(query):
    """Search Yahoo Finance for tickers matching a company name or symbol."""
    try:
        url = 'https://query1.finance.yahoo.com/v1/finance/search'
        params = {
            'q': query,
            'lang': 'en-US',
            'region': 'US',
            'quotesCount': 8,
            'newsCount': 0,
            'enableFuzzyQuery': True,
            'enableNavLinks': False,
        }
        resp = requests.get(url, params=params, headers=HEADERS, timeout=8)
        resp.raise_for_status()
        quotes = resp.json().get('quotes', [])
        results = []
        for q in quotes:
            symbol = q.get('symbol', '')
            name = q.get('longname') or q.get('shortname') or symbol
            quote_type = q.get('quoteType', 'EQUITY')
            exchange = q.get('exchange', '')
            if symbol and quote_type not in ('OPTION', 'MUTUALFUND'):
                results.append({
                    'symbol': symbol,
                    'name': name,
                    'type': quote_type,
                    'exchange': exchange,
                })
        return results
    except Exception as e:
        print(f"[data_fetcher] Search error for '{query}': {e}")
        return []


def get_competitors(symbol):
    """
    Uses Yahoo Finance's free recommendation-by-symbol endpoint to find
    peer/similar tickers. No API key required.
    """
    try:
        url = f"https://query2.finance.yahoo.com/v6/finance/recommendationsbysymbol/{symbol}"
        resp = requests.get(url, headers=HEADERS, timeout=10)
        resp.raise_for_status()
        result_list = resp.json().get('finance', {}).get('result', [])
        if not result_list:
            return []

        peer_symbols = [
            r['symbol'] for r in result_list[0].get('recommendedSymbols', [])[:8]
            if r.get('symbol')
        ]

        competitors = []
        for sym in peer_symbols:
            try:
                t = yf.Ticker(sym)
                i = t.info
                competitors.append({
                    'symbol': sym,
                    'name': i.get('longName') or i.get('shortName', sym),
                    'sector': i.get('sector', ''),
                    'industry': i.get('industry', ''),
                    'market_cap': i.get('marketCap'),
                    'price': i.get('currentPrice') or i.get('regularMarketPrice'),
                    'currency': i.get('currency', 'USD'),
                })
            except Exception:
                competitors.append({
                    'symbol': sym, 'name': sym,
                    'sector': '', 'industry': '',
                    'market_cap': None, 'price': None, 'currency': 'USD',
                })

        return competitors

    except Exception as e:
        print(f"[data_fetcher] Error fetching competitors for {symbol}: {e}")
        return []
