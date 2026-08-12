# -*- coding: utf-8 -*-
"""
外部API知识源接入层（10个免爬取官方API）
================================================
全部为REST API，返回结构化JSON数据，无反爬风险。

API清单：
  免Key（7个）：
    1. GitHub API          — 代码仓库搜索
    2. CoinGecko API       — 加密货币价格
    3. REST Countries      — 国家信息
    4. OpenStreetMap       — 地理编码
    5. PubMed              — 医学论文
    6. Arxiv               — 学术论文
    7. Hacker News         — 科技新闻

  需免费Key（3个）：
    8. 和风天气             — 天气预报（环境变量 QWEATHER_API_KEY）
    9. Tushare             — 股票金融（环境变量 TUSHARE_TOKEN）
   10. NewsAPI             — 全球新闻（环境变量 NEWS_API_KEY）

设计原则：
  - 统一接口：每个API返回 {"success": bool, "data": ..., "source": str, "error": str}
  - 无外部依赖：仅用标准库 subprocess + json（curl命令行）
  - 容错降级：Key未配置时返回明确提示，不崩溃
  - 超时控制：每个API调用最多15秒
"""
import os
import re
import json
import logging
import subprocess
import urllib.parse as urlparse
from typing import Dict, Any, Optional, List

logger = logging.getLogger("SCU3.external_api")

# 统一User-Agent
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
# SCU3项目标识（GitHub API建议附带联系方式）
GITHUB_UA = "SCU3-Agent/3.0 (+https://github.com/SCU3)"


def _curl_json(url: str, headers: Optional[Dict] = None, timeout: int = 20) -> Optional[Any]:
    """统一curl调用，返回解析后的JSON。失败返回None。带重试。"""
    h = headers or {}
    h.setdefault("User-Agent", UA)
    h.setdefault("Accept", "application/json")
    cmd = ["curl", "-s", "-L", "--max-time", str(timeout), "--connect-timeout", "10", "--retry", "2", "--retry-delay", "2"]
    for k, v in h.items():
        cmd.extend(["-H", f"{k}: {v}"])
    cmd.append(url)
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=timeout + 10)
        text = result.stdout.decode("utf-8", errors="ignore")
        if not text or len(text) < 5:
            return None
        return json.loads(text)
    except Exception as e:
        logger.debug(f"curl_json失败[{url[:60]}]: {e}")
        return None


def _curl_text(url: str, headers: Optional[Dict] = None, timeout: int = 20) -> Optional[str]:
    """统一curl调用，返回原始文本。失败返回None。带重试。"""
    h = headers or {}
    h.setdefault("User-Agent", UA)
    h.setdefault("Accept", "*/*")
    cmd = ["curl", "-s", "-L", "--max-time", str(timeout), "--connect-timeout", "10", "--retry", "2", "--retry-delay", "2"]
    for k, v in h.items():
        cmd.extend(["-H", f"{k}: {v}"])
    cmd.append(url)
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=timeout + 10)
        text = result.stdout.decode("utf-8", errors="ignore")
        return text if text and len(text) > 5 else None
    except Exception as e:
        logger.debug(f"curl_text失败[{url[:60]}]: {e}")
        return None


# ════════════════════════════════════════════════════════════
# 1. GitHub API（免Key，每小时60次匿名配额）
# ════════════════════════════════════════════════════════════
# GitHub返回URL允许的域名白名单（防止恶意仓库描述中的钓鱼链接）
GITHUB_URL_WHITELIST = (
    "github.com", "www.github.com", "raw.githubusercontent.com",
    "gist.github.com", "api.github.com",
)


def _is_safe_github_url(url: str) -> bool:
    """校验URL是否属于GitHub白名单域名（防钓鱼链接注入）"""
    if not url or not isinstance(url, str):
        return False
    try:
        from urllib.parse import urlparse as _urlparse
        parsed = _urlparse(url)
        host = (parsed.netloc or "").lower()
        if parsed.scheme not in ("https", "http"):
            return False
        return host in GITHUB_URL_WHITELIST
    except Exception:
        return False


def github_search(query: str, max_results: int = 5) -> Dict[str, Any]:
    """GitHub仓库搜索（替代github_search mock）

    API: https://api.github.com/search/repositories?q={query}&sort=stars&per_page={n}
    配额: 匿名60次/小时，带Token 5000次/小时
    安全: URL白名单校验（仅允许github.com等官方域名），防钓鱼链接
    """
    encoded = urlparse.quote(query)
    token = os.environ.get("GITHUB_TOKEN", "")
    headers = {"User-Agent": GITHUB_UA, "Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    url = f"https://api.github.com/search/repositories?q={encoded}&sort=stars&order=desc&per_page={max_results}"

    # 用_curl_text获取原始响应体，便于检测配额耗尽（403+rate limit关键字）
    raw_text = _curl_text(url, headers, timeout=15)
    if not raw_text:
        return {
            "success": False,
            "error": "GitHub API无响应（网络超时或服务不可达）",
            "data": None, "source": "GitHub",
        }

    # 检测配额耗尽（HTTP 403响应体含"rate limit"关键字）
    if "rate limit" in raw_text.lower() or "API rate limit exceeded" in raw_text:
        if token:
            msg = "GitHub API配额耗尽（Token模式5000次/小时已用完）"
        else:
            msg = "GitHub API配额耗尽（匿名60次/小时已用完，配置GITHUB_TOKEN可提升至5000次/小时）"
        logger.warning(f"GitHub配额耗尽: {msg}")
        return {"success": False, "error": msg, "data": None, "source": "GitHub"}

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError:
        return {"success": False, "error": "GitHub API响应解析失败", "data": None, "source": "GitHub"}

    if "items" not in data:
        # 可能是403/422等错误响应
        err_msg = data.get("message", "GitHub API返回异常")
        return {"success": False, "error": f"GitHub API: {err_msg}", "data": None, "source": "GitHub"}

    repos = []
    unsafe_count = 0
    for item in data["items"][:max_results]:
        html_url = item.get("html_url", "")
        # URL白名单校验：非github.com官方域名的URL置空，防止钓鱼链接
        if html_url and not _is_safe_github_url(html_url):
            logger.warning(f"GitHub结果URL被白名单过滤(疑似钓鱼): {html_url}")
            html_url = ""
            unsafe_count += 1
        repos.append({
            "full_name": item.get("full_name", ""),
            "description": item.get("description", "") or "",
            "stars": item.get("stargazers_count", 0),
            "forks": item.get("forks_count", 0),
            "language": item.get("language", "") or "",
            "url": html_url,
            "open_issues": item.get("open_issues_count", 0),
            "updated_at": item.get("updated_at", ""),
            "topics": item.get("topics", []),
        })

    quota_info = "Token(5000/h)" if token else "匿名(60/h)"
    logger.info(f"GitHub搜索[{query[:30]}]: 命中{len(repos)}个仓库 (共{data.get('total_count', 0)}) [{quota_info}] 过滤{unsafe_count}个可疑URL")
    return {
        "success": True, "data": {"repos": repos, "total": data.get("total_count", 0)},
        "source": "GitHub API", "error": None,
    }


# ════════════════════════════════════════════════════════════
# 2. CoinGecko API（免Key，30次/分钟）
# ════════════════════════════════════════════════════════════
def crypto_price(symbols: List[str] = None) -> Dict[str, Any]:
    """加密货币价格查询（替代crypto_price mock）

    主API: OKX公共API（国内可达，无需Key）
    备用API: 币安公共API（国内可能超时）
    支持币种: bitcoin(btc), ethereum(eth), solana(sol), dogecoin(doge)等
    """
    if symbols is None:
        symbols = ["bitcoin", "ethereum"]

    # 符号映射（用户输入btc → 交易所符号）
    symbol_map = {
        "btc": "BTC-USDT", "bitcoin": "BTC-USDT",
        "eth": "ETH-USDT", "ethereum": "ETH-USDT",
        "sol": "SOL-USDT", "solana": "SOL-USDT",
        "doge": "DOGE-USDT", "dogecoin": "DOGE-USDT",
        "bnb": "BNB-USDT",
        "xrp": "XRP-USDT",
        "ada": "ADA-USDT",
        "dot": "DOT-USDT",
    }
    okx_symbols = [symbol_map.get(s.lower(), f"{s.upper()}-USDT") for s in symbols]

    # 方案1: OKX公共API（国内可达）
    # https://www.okx.com/api/v5/market/ticker?instId=BTC-USDT
    prices = {}
    success_count = 0
    for sym in okx_symbols:
        url = f"https://www.okx.com/api/v5/market/ticker?instId={sym}"
        data = _curl_json(url, timeout=15)
        if data and data.get("code") == "0" and data.get("data"):
            ticker = data["data"][0]
            usd_price = float(ticker.get("last", 0))
            prices[sym.replace("-USDT", "").lower()] = {
                "usd": usd_price,
                "cny": round(usd_price * 7.2, 2),
                "change_24h": float(ticker.get("last", 0)) / float(ticker.get("open24h", 1)) * 100 - 100
                              if ticker.get("open24h") else 0,
            }
            success_count += 1

    if success_count > 0:
        logger.info(f"OKX API查询: {success_count}/{len(symbols)}种货币成功")
        return {
            "success": True, "data": {"prices": prices},
            "source": "OKX Public API", "error": None,
        }

    # 方案2: 币安备用
    binance_symbols = [s.replace("-USDT", "USDT") for s in okx_symbols]
    for sym in binance_symbols:
        url = f"https://api.binance.com/api/v3/ticker/24hr?symbol={sym}"
        data = _curl_json(url, timeout=15)
        if data and "lastPrice" in data:
            usd_price = float(data.get("lastPrice", 0))
            prices[sym.replace("USDT", "").lower()] = {
                "usd": usd_price,
                "cny": round(usd_price * 7.2, 2),
                "change_24h": float(data.get("priceChangePercent", 0)),
            }
            success_count += 1

    if success_count > 0:
        return {
            "success": True, "data": {"prices": prices},
            "source": "Binance Public API", "error": None,
        }

    return {"success": False, "error": "加密货币API国内均不可达（OKX/币安/火币/CoinCap），请通过搜索引擎查询", "data": None, "source": "Crypto"}


# ════════════════════════════════════════════════════════════
# 3. REST Countries（免Key，无配额限制）
# ════════════════════════════════════════════════════════════
def country_info(name: str) -> Dict[str, Any]:
    """国家信息查询

    说明: REST Countries v1-v4已全部下线，v5需Key
    方案: 改用维基百科API获取国家信息（免Key，已在action.py接入）
    本函数作为统一入口，内部委托给维基百科查询
    """
    # 委托给维基百科API
    try:
        from w1_layer.action import ActionLayer
        action = ActionLayer()
        wiki_result = action._search_wikipedia(name + " 国家", max_results=2)
        if wiki_result and wiki_result.get("results"):
            top = wiki_result["results"][0]
            return {
                "success": True,
                "data": {
                    "name": name,
                    "summary": top.get("snippet", "")[:500],
                    "url": top.get("url", ""),
                    "source_api": "Wikipedia",
                },
                "source": "Wikipedia API (REST Countries已弃用)",
                "error": None,
            }
    except Exception as e:
        logger.debug(f"country_info委托Wikipedia失败: {e}")

    return {
        "success": False,
        "error": "REST Countries v1-v4已下线（v5需Key），Wikipedia查询也失败",
        "data": None, "source": "REST Countries",
    }


# ════════════════════════════════════════════════════════════
# 4. OpenStreetMap Nominatim（免Key，1次/秒）
# ════════════════════════════════════════════════════════════
def geocode(query: str, max_results: int = 3) -> Dict[str, Any]:
    """地理编码查询（地址→经纬度）

    主API: OpenStreetMap Nominatim（国内访问不稳定）
    备用API: 高德地图Web服务（免Key，需配AMAP_KEY；无Key时用IP定位粗略查询）
    用途：地名查询、经纬度获取、地理信息
    """
    encoded = urlparse.quote(query)

    # 方案1: OpenStreetMap（带重试）
    url = f"https://nominatim.openstreetmap.org/search?q={encoded}&format=json&limit={max_results}&addressdetails=1&accept-language=zh-CN"
    headers = {"User-Agent": GITHUB_UA}
    data = _curl_json(url, headers, timeout=25)

    if data and isinstance(data, list):
        places = []
        for item in data[:max_results]:
            places.append({
                "display_name": item.get("display_name", ""),
                "lat": item.get("lat", ""),
                "lon": item.get("lon", ""),
                "type": item.get("type", ""),
                "importance": item.get("importance", 0),
                "address": item.get("address", {}),
            })
        logger.info(f"地理编码[{query[:30]}]: OpenStreetMap命中{len(places)}条")
        return {
            "success": True, "data": {"places": places},
            "source": "OpenStreetMap API", "error": None,
        }

    # 方案2: 高德地图（需Key）
    amap_key = os.environ.get("AMAP_KEY", "")
    if amap_key:
        amap_url = f"https://restapi.amap.com/v3/geocode/geo?address={encoded}&key={amap_key}&output=json"
        amap_data = _curl_json(amap_url, timeout=15)
        if amap_data and amap_data.get("status") == "1":
            geocodes = amap_data.get("geocodes", [])
            places = []
            for g in geocodes[:max_results]:
                location = g.get("location", "").split(",")
                places.append({
                    "display_name": g.get("formatted_address", ""),
                    "lat": location[1] if len(location) > 1 else "",
                    "lon": location[0] if len(location) > 0 else "",
                    "type": g.get("level", ""),
                    "importance": 1.0,
                    "address": {"province": g.get("province", ""), "city": g.get("city", "")},
                })
            if places:
                logger.info(f"地理编码[{query[:30]}]: 高德命中{len(places)}条")
                return {
                    "success": True, "data": {"places": places},
                    "source": "高德地图 API", "error": None,
                }

    return {"success": False, "error": "地理编码API均无响应（OpenStreetMap超时+高德无Key）", "data": None, "source": "Geocode"}


# ════════════════════════════════════════════════════════════
# 5. PubMed E-utilities（免Key，3次/秒）
# ════════════════════════════════════════════════════════════
def pubmed_search(query: str, max_results: int = 5) -> Dict[str, Any]:
    """PubMed医学论文搜索

    API: https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi
    备用: http://eutils.ncbi.nlm.nih.gov（HTTP协议，避开SSL问题）
    流程: esearch获取PMID列表 → esummary获取摘要
    """
    encoded = urlparse.quote(query)
    # 步骤1: esearch获取PMID列表（HTTPS+HTTP双协议重试）
    search_url_https = (
        f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?"
        f"db=pubmed&term={encoded}&retmax={max_results}&retmode=json&sort=relevance"
    )
    search_url_http = search_url_https.replace("https://", "http://")

    data = _curl_json(search_url_https, timeout=25)
    if not data:
        data = _curl_json(search_url_http, timeout=25)  # HTTP备用
    if not data:
        return {"success": False, "error": "PubMed API无响应（HTTPS+HTTP均超时）", "data": None, "source": "PubMed"}

    id_list = data.get("esearchresult", {}).get("idlist", [])
    if not id_list:
        return {"success": False, "error": "PubMed未找到相关论文", "data": None, "source": "PubMed"}

    # 步骤2: esummary获取论文详情
    ids_str = ",".join(id_list)
    summary_url = (
        f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?"
        f"db=pubmed&id={ids_str}&retmode=json"
    )
    summary_data = _curl_json(summary_url, timeout=25)
    if not summary_data:
        summary_data = _curl_json(summary_url.replace("https://", "http://"), timeout=25)
    if not summary_data:
        return {"success": False, "error": "PubMed摘要获取失败", "data": None, "source": "PubMed"}

    papers = []
    result = summary_data.get("result", {})
    for pmid in id_list:
        paper = result.get(pmid, {})
        if paper:
            papers.append({
                "pmid": pmid,
                "title": paper.get("title", ""),
                "authors": [a.get("name", "") for a in paper.get("authors", [])[:5]],
                "journal": paper.get("fulljournalname", ""),
                "pubdate": paper.get("pubdate", ""),
                "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            })

    total = int(data.get("esearchresult", {}).get("count", 0))
    logger.info(f"PubMed搜索[{query[:30]}]: 命中{len(papers)}篇 (共{total})")
    return {
        "success": True, "data": {"papers": papers, "total": total},
        "source": "PubMed API", "error": None,
    }


# ════════════════════════════════════════════════════════════
# 6. Arxiv API（免Key，无配额限制）
# ════════════════════════════════════════════════════════════
def arxiv_search(query: str, max_results: int = 5) -> Dict[str, Any]:
    """Arxiv学术论文搜索

    API: http://export.arxiv.org/api/query?search_query=all:{q}&max_results={n}
    返回Atom XML，需正则解析
    """
    encoded = urlparse.quote(query)
    url = f"http://export.arxiv.org/api/query?search_query=all:{encoded}&max_results={max_results}&sortBy=relevance"
    text = _curl_text(url, timeout=15)
    if not text:
        return {"success": False, "error": "Arxiv API无响应", "data": None, "source": "Arxiv"}

    # 解析Atom XML（用正则，避免引入XML库）
    entries = re.findall(r'<entry>(.*?)</entry>', text, re.S)
    papers = []
    for entry in entries[:max_results]:
        title_m = re.search(r'<title>(.*?)</title>', entry, re.S)
        summary_m = re.search(r'<summary>(.*?)</summary>', entry, re.S)
        published_m = re.search(r'<published>(.*?)</published>', entry, re.S)
        link_m = re.search(r'<id>(.*?)</id>', entry, re.S)
        authors = re.findall(r'<name>(.*?)</name>', entry)

        title = re.sub(r'\s+', ' ', title_m.group(1)).strip() if title_m else ""
        summary = re.sub(r'\s+', ' ', summary_m.group(1)).strip()[:300] if summary_m else ""
        papers.append({
            "title": title,
            "authors": authors[:5],
            "published": published_m.group(1)[:10] if published_m else "",
            "summary": summary,
            "url": link_m.group(1).strip() if link_m else "",
        })

    if not papers:
        return {"success": False, "error": "Arxiv未找到相关论文", "data": None, "source": "Arxiv"}

    logger.info(f"Arxiv搜索[{query[:30]}]: 命中{len(papers)}篇")
    return {
        "success": True, "data": {"papers": papers, "total": len(papers)},
        "source": "Arxiv API", "error": None,
    }


# ════════════════════════════════════════════════════════════
# 7. Hacker News（免Key，无配额限制）
# ════════════════════════════════════════════════════════════
def hacker_news_top(max_results: int = 10) -> Dict[str, Any]:
    """Hacker News热门故事

    API: https://hacker-news.firebaseio.com/v0/topstories.json
    流程: 获取Top故事ID列表 → 批量获取详情
    """
    # 获取Top 100故事ID
    url = "https://hacker-news.firebaseio.com/v0/topstories.json"
    data = _curl_json(url, timeout=15)
    if not data or not isinstance(data, list):
        return {"success": False, "error": "Hacker News API无响应", "data": None, "source": "Hacker News"}

    # 取前N个故事详情
    stories = []
    for story_id in data[:max_results * 2]:  # 多取一些以备过滤
        if len(stories) >= max_results:
            break
        story_url = f"https://hacker-news.firebaseio.com/v0/item/{story_id}.json"
        story = _curl_json(story_url, timeout=10)
        if not story or story.get("type") != "story":
            continue
        stories.append({
            "title": story.get("title", ""),
            "url": story.get("url", ""),
            "score": story.get("score", 0),
            "by": story.get("by", ""),
            "time": story.get("time", 0),
            "descendants": story.get("descendants", 0),
            "hn_url": f"https://news.ycombinator.com/item?id={story_id}",
        })

    # 按分数排序
    stories.sort(key=lambda x: x["score"], reverse=True)
    logger.info(f"Hacker News: 获取{len(stories)}条热门")
    return {
        "success": True, "data": {"stories": stories[:max_results]},
        "source": "Hacker News API", "error": None,
    }


# ════════════════════════════════════════════════════════════
# 8. 和风天气（需Key，免费1000次/天）
# ════════════════════════════════════════════════════════════
def weather(city: str) -> Dict[str, Any]:
    """和风天气查询（替代weather mock）

    需环境变量: QWEATHER_API_KEY
    API: https://devapi.qweather.com/v7/weather/now?location={city_id}&key={key}
    流程: 城市名→City ID→实时天气
    """
    api_key = os.environ.get("QWEATHER_API_KEY", "")
    if not api_key:
        return {
            "success": False,
            "error": "和风天气API Key未配置（环境变量 QWEATHER_API_KEY）",
            "data": None, "source": "和风天气",
        }

    # 步骤1: 城市查询获取location_id
    encoded = urlparse.quote(city)
    lookup_url = f"https://geoapi.qweather.com/v2/city/lookup?location={encoded}&key={api_key}"
    lookup_data = _curl_json(lookup_url, timeout=10)
    if not lookup_data or lookup_data.get("code") != "200" or not lookup_data.get("location"):
        return {"success": False, "error": f"未找到城市: {city}", "data": None, "source": "和风天气"}

    location_id = lookup_data["location"][0]["id"]
    city_name = lookup_data["location"][0]["name"]

    # 步骤2: 获取实时天气
    weather_url = f"https://devapi.qweather.com/v7/weather/now?location={location_id}&key={api_key}"
    weather_data = _curl_json(weather_url, timeout=10)
    if not weather_data or weather_data.get("code") != "200":
        return {"success": False, "error": "天气数据获取失败", "data": None, "source": "和风天气"}

    now = weather_data.get("now", {})
    result = {
        "city": city_name,
        "temp": now.get("temp", "?") + "°C",
        "feels_like": now.get("feels", "?") + "°C",
        "desc": now.get("text", ""),
        "wind_dir": now.get("windDir", ""),
        "wind_scale": now.get("windScale", ""),
        "humidity": now.get("humidity", "?") + "%",
        "pressure": now.get("pressure", "?") + "hPa",
        "visibility": now.get("vis", "?") + "km",
    }
    logger.info(f"和风天气[{city}]: {result['temp']} {result['desc']}")
    return {
        "success": True, "data": result,
        "source": "和风天气 API", "error": None,
    }


# ════════════════════════════════════════════════════════════
# 9. Tushare（需Key，免费500次/天）
# ════════════════════════════════════════════════════════════
def stock_price(code: str) -> Dict[str, Any]:
    """股票价格查询（替代stock_price mock）

    需环境变量: TUSHARE_TOKEN
    API: https://api.tushare.pro (POST)
    支持A股代码（如000001、600519）
    """
    token = os.environ.get("TUSHARE_TOKEN", "")
    if not token:
        return {
            "success": False,
            "error": "Tushare Token未配置（环境变量 TUSHARE_TOKEN）",
            "data": None, "source": "Tushare",
        }

    # 判断市场前缀
    ts_code = code
    if code.startswith("6"):
        ts_code = f"{code}.SH"
    elif code.startswith(("0", "3")):
        ts_code = f"{code}.SZ"
    elif code.startswith(("4", "8")):
        ts_code = f"{code}.BJ"

    # Tushare用POST + JSON body
    body = json.dumps({
        "api_name": "daily",
        "token": token,
        "params": {"ts_code": ts_code},
        "fields": "ts_code,trade_date,open,high,low,close,vol,amount,pct_chg",
    })
    cmd = ["curl", "-s", "-L", "--max-time", "15",
           "-H", f"User-Agent: {UA}",
           "-H", "Content-Type: application/json",
           "-X", "POST", "-d", body,
           "https://api.tushare.pro"]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=18)
        text = result.stdout.decode("utf-8", errors="ignore")
        data = json.loads(text)
    except Exception as e:
        return {"success": False, "error": f"Tushare API调用失败: {e}", "data": None, "source": "Tushare"}

    if not data or data.get("code") != 0:
        return {"success": False, "error": data.get("msg", "Tushare查询失败"), "data": None, "source": "Tushare"}

    items = data.get("data", {}).get("items", [])
    fields = data.get("data", {}).get("fields", [])
    if not items:
        return {"success": False, "error": f"未找到股票: {code}", "data": None, "source": "Tushare"}

    latest = items[0]
    stock_data = dict(zip(fields, latest))
    result = {
        "code": stock_data.get("ts_code", code),
        "date": stock_data.get("trade_date", ""),
        "open": stock_data.get("open", 0),
        "high": stock_data.get("high", 0),
        "low": stock_data.get("low", 0),
        "close": stock_data.get("close", 0),
        "volume": stock_data.get("vol", 0),
        "change_pct": stock_data.get("pct_chg", 0),
    }
    logger.info(f"Tushare[{code}]: 收盘价{result['close']} 涨跌{result['change_pct']}%")
    return {
        "success": True, "data": result,
        "source": "Tushare API", "error": None,
    }


# ════════════════════════════════════════════════════════════
# 10. NewsAPI（需Key，免费100次/天）
# ════════════════════════════════════════════════════════════
def news_search(query: str = "", max_results: int = 10) -> Dict[str, Any]:
    """全球新闻搜索

    需环境变量: NEWS_API_KEY
    API: https://newsapi.org/v2/everything?q={query}&sortBy=publishedAt
    """
    api_key = os.environ.get("NEWS_API_KEY", "")
    if not api_key:
        return {
            "success": False,
            "error": "NewsAPI Key未配置（环境变量 NEWS_API_KEY）",
            "data": None, "source": "NewsAPI",
        }

    encoded = urlparse.quote(query) if query else "technology"
    url = f"https://newsapi.org/v2/everything?q={encoded}&sortBy=publishedAt&pageSize={max_results}&language=zh&apiKey={api_key}"
    data = _curl_json(url, timeout=15)
    if not data or data.get("status") != "ok":
        return {"success": False, "error": data.get("message", "NewsAPI查询失败"), "data": None, "source": "NewsAPI"}

    articles = []
    for art in data.get("articles", [])[:max_results]:
        articles.append({
            "title": art.get("title", ""),
            "source": art.get("source", {}).get("name", ""),
            "author": art.get("author", "") or "",
            "published_at": art.get("publishedAt", "")[:10],
            "description": (art.get("description", "") or "")[:200],
            "url": art.get("url", ""),
        })

    total = data.get("totalResults", 0)
    logger.info(f"NewsAPI[{query[:30]}]: 命中{len(articles)}条 (共{total})")
    return {
        "success": True, "data": {"articles": articles, "total": total},
        "source": "NewsAPI", "error": None,
    }


# ════════════════════════════════════════════════════════════
# 统一路由入口：根据查询内容选择最合适的API
# ════════════════════════════════════════════════════════════
def route_external_api(text: str) -> Optional[Dict[str, Any]]:
    """根据用户输入文本，自动路由到最合适的外部API

    路由规则（按优先级，免Key优先于需Key）：
      1. github/仓库 → GitHub API（免Key）
      2. btc/eth/加密货币 → OKX/Binance（免Key）
      3. 国家名 → Wikipedia（免Key，REST Countries已弃用）
      4. 股票代码 → Tushare（需Key）
      5. 天气 → 和风天气（需Key）
      6. 科技热点 → Hacker News（免Key，优先于NewsAPI避免"科技新闻"被吞）
      7. 新闻 → NewsAPI（需Key）
      8. 论文/医学/学术 → PubMed + Arxiv（免Key）
      9. 地理/地址 → OpenStreetMap（免Key）

    返回None表示无匹配API，由调用方走默认搜索流程。
    """
    text_lower = text.lower()

    # 1. GitHub仓库搜索
    m = re.search(r"(?:github|开源仓库|代码仓库|搜索仓库)\s*(.+)", text, re.I)
    if m:
        return github_search(m.group(1).strip())

    # 2. 加密货币价格
    m = re.search(r"(?:比特币|以太坊|bitcoin|btc|ethereum|eth|加密货币|crypto|币价)", text, re.I)
    if m:
        symbols = []
        if re.search(r"btc|比特币|bitcoin", text, re.I):
            symbols.append("bitcoin")
        if re.search(r"eth|以太坊|ethereum", text, re.I):
            symbols.append("ethereum")
        if not symbols:
            symbols = ["bitcoin", "ethereum"]
        return crypto_price(symbols)

    # 3. 国家信息
    m = re.search(r"(?:国家|首都|人口|面积|语言|货币|国旗)\s*[:：]?\s*([A-Za-z\u4e00-\u9fa5]+)", text)
    if m and not re.search(r"中国|国内", text):
        country_name = m.group(1).strip()
        return country_info(country_name)

    # 4. 股票价格（A股代码：6位数字）
    m = re.search(r"(?:股票|stock|A股)\s*(\d{6})", text, re.I)
    if m:
        return stock_price(m.group(1))

    # 5. 天气（需Key，Key未配置时返回提示）
    m = re.search(r"(?:天气|气温|weather)\s*([北京上海广州深圳成都杭州南京武汉西安重庆]+)", text)
    if m:
        return weather(m.group(1))

    # 6. 科技热点（Hacker News，免Key，优先于NewsAPI）
    # 提前到新闻之前：避免"科技新闻"被NewsAPI吞掉（NewsAPI需Key）
    if re.search(r"科技|tech|hacker|yc\b|startup|创业|硅谷|黑客新闻", text, re.I):
        return hacker_news_top(10)

    # 7. 新闻搜索（需Key）
    if re.search(r"新闻|news|头条|热点事件", text, re.I):
        # 提取查询词
        query_match = re.search(r"(?:新闻|news|头条|热点事件)\s*[:：]?\s*(.+)", text, re.I)
        news_q = query_match.group(1).strip() if query_match else ""
        return news_search(news_q)

    # 8. 学术论文（PubMed + Arxiv）
    if re.search(r"论文|paper|研究|study|医学文献|临床|academic|scholar", text, re.I):
        # 提取查询词
        query_match = re.search(r"(?:论文|paper|研究|study|医学文献|临床|academic|scholar)\s*[:：]?\s*(.+)", text, re.I)
        paper_q = query_match.group(1).strip() if query_match else text[:40]
        # 优先PubMed（医学类），Arxiv（技术类）
        if re.search(r"医学|临床|药物|疾病|治疗|患者|病例", text):
            return pubmed_search(paper_q)
        return arxiv_search(paper_q)

    # 9. 地理/地址查询
    if re.search(r"经纬度|地理|地址|在哪里|位置|地图", text, re.I):
        query_match = re.search(r"(?:经纬度|地理|地址|在哪里|位置|地图)\s*[:：]?\s*(.+)", text, re.I)
        geo_q = query_match.group(1).strip() if query_match else text[:40]
        return geocode(geo_q)

    return None  # 无匹配API


# ════════════════════════════════════════════════════════════
# 测试入口
# ════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("=" * 60)
    print("外部API知识源测试")
    print("=" * 60)

    tests = [
        ("GitHub", "github python web framework"),
        ("CoinGecko", "btc eth"),
        ("REST Countries", "日本"),
        ("OpenStreetMap", "北京天安门"),
        ("PubMed", "aspirin side effects"),
        ("Arxiv", "transformer attention mechanism"),
        ("Hacker News", ""),
    ]

    for name, query in tests:
        print(f"\n--- {name} ---")
        if name == "GitHub":
            r = github_search(query, max_results=3)
        elif name == "CoinGecko":
            r = crypto_price(query.split())
        elif name == "REST Countries":
            r = country_info(query)
        elif name == "OpenStreetMap":
            r = geocode(query)
        elif name == "PubMed":
            r = pubmed_search(query, max_results=3)
        elif name == "Arxiv":
            r = arxiv_search(query, max_results=3)
        elif name == "Hacker News":
            r = hacker_news_top(5)

        if r["success"]:
            print(f"✓ 成功 [{r['source']}]")
            data = r["data"]
            if isinstance(data, dict):
                for k, v in list(data.items())[:3]:
                    print(f"  {k}: {str(v)[:120]}")
        else:
            print(f"✗ 失败: {r['error']}")
