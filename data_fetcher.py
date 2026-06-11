import threading
import time
import yfinance as yf
import requests
from datetime import datetime

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': '*/*',
    'Accept-Language': 'en-US,en;q=0.9',
    'Referer': 'https://finance.yahoo.com/',
}

# Yahoo Finance session / crumb cache (refreshed hourly)
_sess_lock = threading.Lock()
_sess_cache = {'session': None, 'crumb': None, 'ts': 0}

def _get_yahoo_session():
    with _sess_lock:
        if _sess_cache['crumb'] and time.time() - _sess_cache['ts'] < 3600:
            return _sess_cache['session'], _sess_cache['crumb']
        try:
            s = requests.Session()
            s.headers.update(HEADERS)
            s.get('https://finance.yahoo.com/', timeout=10)
            r = s.get('https://query2.finance.yahoo.com/v1/test/getcrumb', timeout=10)
            crumb = r.text.strip() if r.status_code == 200 else ''
            if crumb and crumb not in ('null', 'Unauthorized', ''):
                _sess_cache.update({'session': s, 'crumb': crumb, 'ts': time.time()})
                print(f'[data_fetcher] Yahoo session ready, crumb acquired')
            else:
                print(f'[data_fetcher] Could not get crumb: {r.status_code} {r.text[:50]}')
        except Exception as e:
            print(f'[data_fetcher] Session init failed: {e}')
        return _sess_cache['session'], _sess_cache['crumb']

REC_MAP = {
    'strong_buy': 'Strong Buy',
    'buy': 'Buy',
    'hold': 'Hold',
    'underperform': 'Underperform',
    'sell': 'Sell',
}


def _quotesummary_fallback(symbol):
    """
    Fetch full data via Yahoo Finance quoteSummary API directly.
    More reliable than ticker.info on cloud servers — returns all metrics
    including P/E, EPS, short interest, analyst consensus, etc.
    """
    try:
        session, crumb = _get_yahoo_session()
        modules = 'price,summaryDetail,defaultKeyStatistics,financialData,recommendationTrend,assetProfile,fundProfile'
        params = {'modules': modules}
        if crumb:
            params['crumb'] = crumb
        req = session if session else requests
        resp = req.get(
            f'https://query2.finance.yahoo.com/v11/finance/quoteSummary/{symbol}',
            params=params,
            headers=HEADERS,
            timeout=10,
        )
        resp.raise_for_status()
        result = resp.json().get('quoteSummary', {}).get('result', [])
        if not result:
            return None
        r = result[0]

        def v(section, key):
            d = r.get(section) or {}
            val = d.get(key)
            return val.get('raw') if isinstance(val, dict) else val

        price_data = r.get('price', {})
        summary    = r.get('summaryDetail', {})
        key_stats  = r.get('defaultKeyStatistics', {})
        fin_data   = r.get('financialData', {})
        profile    = r.get('assetProfile', {})

        rec_key   = (v('financialData', 'recommendationKey') or '').lower()
        consensus = REC_MAP.get(rec_key, rec_key.replace('_', ' ').title() if rec_key else None)

        price    = v('price', 'regularMarketPrice')
        prev     = v('summaryDetail', 'previousClose') or v('price', 'regularMarketPreviousClose')

        info = {
            'quoteType':               v('price', 'quoteType') or 'EQUITY',
            'symbol':                  symbol,
            'shortName':               v('price', 'shortName') or symbol,
            'longName':                v('price', 'longName') or v('price', 'shortName') or symbol,
            'currentPrice':            price,
            'previousClose':           prev,
            'currency':                v('price', 'currency') or 'USD',
            'marketCap':               v('price', 'marketCap') or v('summaryDetail', 'marketCap'),
            'trailingPE':              v('summaryDetail', 'trailingPE'),
            'forwardPE':               v('summaryDetail', 'forwardPE'),
            'pegRatio':                v('defaultKeyStatistics', 'pegRatio'),
            'trailingPegRatio':        v('defaultKeyStatistics', 'trailingPegRatio'),
            'targetMeanPrice':         v('financialData', 'targetMeanPrice'),
            'targetHighPrice':         v('financialData', 'targetHighPrice'),
            'targetLowPrice':          v('financialData', 'targetLowPrice'),
            'recommendationKey':       rec_key,
            '_consensus':              consensus,
            'numberOfAnalystOpinions': v('financialData', 'numberOfAnalystOpinions'),
            'sector':                  profile.get('sector', ''),
            'industry':                profile.get('industry', ''),
            'website':                 profile.get('website', ''),
            'country':                 profile.get('country', ''),
            'fullTimeEmployees':       profile.get('fullTimeEmployees'),
            'longBusinessSummary':     (profile.get('longBusinessSummary') or '')[:600],
            'volume':                  v('price', 'regularMarketVolume'),
            'averageVolume':           v('summaryDetail', 'averageVolume'),
            'fiftyTwoWeekHigh':        v('summaryDetail', 'fiftyTwoWeekHigh'),
            'fiftyTwoWeekLow':         v('summaryDetail', 'fiftyTwoWeekLow'),
            'dividendYield':           v('summaryDetail', 'dividendYield'),
            'beta':                    v('summaryDetail', 'beta'),
            'trailingEps':             v('defaultKeyStatistics', 'trailingEps'),
            'forwardEps':              v('defaultKeyStatistics', 'forwardEps'),
            'sharesShort':             v('defaultKeyStatistics', 'sharesShort'),
            'shortPercentOfFloat':     v('defaultKeyStatistics', 'shortPercentOfFloat'),
            'shortRatio':              v('defaultKeyStatistics', 'shortRatio'),
            # Mutual fund / ETF specific
            'annualReportExpenseRatio': v('fundProfile', 'annualReportExpenseRatio') or v('defaultKeyStatistics', 'annualReportExpenseRatio'),
            'ytdReturn':               v('summaryDetail', 'ytdReturn') or v('defaultKeyStatistics', 'ytdReturn'),
            'threeYearAverageReturn':  v('defaultKeyStatistics', 'threeYearAverageReturn'),
            'fiveYearAverageReturn':   v('defaultKeyStatistics', 'fiveYearAverageReturn'),
            'totalAssets':             v('summaryDetail', 'totalAssets'),
            'fundFamily':              v('defaultKeyStatistics', 'fundFamily') or (r.get('fundProfile') or {}).get('family'),
            'category':                (r.get('fundProfile') or {}).get('categoryName') or v('defaultKeyStatistics', 'category'),
            'yield':                   v('summaryDetail', 'yield'),
            'navPrice':                v('summaryDetail', 'navPrice'),
        }
        if not info['currentPrice'] and not info['navPrice']:
            return None
        return info
    except Exception as e:
        print(f"[data_fetcher] quoteSummary fallback failed for {symbol}: {e}")
        return None


def _fast_info_fallback(ticker, symbol):
    """Last-resort fallback using fast_info + history (price only, no fundamentals)."""
    try:
        fi    = ticker.fast_info
        price = getattr(fi, 'last_price', None)
        prev  = getattr(fi, 'previous_close', None)
        if not price:
            hist = ticker.history(period='2d')
            if hist.empty:
                return None
            price = float(hist['Close'].iloc[-1])
            prev  = float(hist['Close'].iloc[-2]) if len(hist) >= 2 else price
        return {
            'quoteType':        getattr(fi, 'quote_type', None) or 'EQUITY',
            'symbol':           symbol,
            'shortName':        symbol,
            'currentPrice':     price,
            'previousClose':    prev,
            'marketCap':        getattr(fi, 'market_cap', None),
            'fiftyTwoWeekHigh': getattr(fi, 'fifty_two_week_high', None),
            'fiftyTwoWeekLow':  getattr(fi, 'fifty_two_week_low', None),
            'currency':         getattr(fi, 'currency', 'USD') or 'USD',
            'volume':           getattr(fi, 'last_volume', None),
        }
    except Exception as e:
        print(f"[data_fetcher] fast_info fallback failed for {symbol}: {e}")
        return None


def _as_fraction(val):
    """Yahoo sometimes returns returns/ratios as percents (e.g. 11.2) instead of
    fractions (0.112). Normalize to a fraction so the UI can always multiply by 100."""
    if val is None:
        return None
    return val / 100 if abs(val) > 1 else val


def get_asset_data(symbol):
    try:
        ticker = yf.Ticker(symbol)

        # 1. Try yfinance ticker.info (best data, sometimes blocked on cloud)
        try:
            info = ticker.info or {}
        except Exception as e:
            print(f"[data_fetcher] ticker.info failed for {symbol}: {e}")
            info = {}

        has_price    = info.get('currentPrice') or info.get('regularMarketPrice') or info.get('previousClose')
        has_identity = info.get('quoteType') or info.get('symbol') or info.get('shortName')

        # 2. If ticker.info is empty, try direct quoteSummary API (full fundamentals)
        if not has_price and not has_identity:
            print(f"[data_fetcher] ticker.info empty for {symbol}, trying quoteSummary API")
            info = _quotesummary_fallback(symbol)
            if not info:
                # 3. Last resort: fast_info (price only)
                print(f"[data_fetcher] quoteSummary failed for {symbol}, trying fast_info")
                info = _fast_info_fallback(ticker, symbol)
            if not info:
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

        # _consensus is pre-computed by _quotesummary_fallback; otherwise derive from recommendationKey
        if info.get('_consensus'):
            analyst_consensus = info['_consensus']
        else:
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
            'shares_short': info.get('sharesShort'),
            'short_percent_float': info.get('shortPercentOfFloat'),
            'short_ratio': info.get('shortRatio'),
            # Mutual fund / ETF specific
            'expense_ratio': _as_fraction(info.get('annualReportExpenseRatio') or info.get('netExpenseRatio')),
            'ytd_return': _as_fraction(info.get('ytdReturn')),
            'three_year_return': _as_fraction(info.get('threeYearAverageReturn')),
            'five_year_return': _as_fraction(info.get('fiveYearAverageReturn')),
            'total_assets': info.get('totalAssets'),
            'fund_family': info.get('fundFamily'),
            'category': info.get('category'),
            'fund_yield': info.get('yield'),
            'nav_price': info.get('navPrice'),
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
            if symbol and quote_type != 'OPTION':
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
