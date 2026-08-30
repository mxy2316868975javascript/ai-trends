#!/usr/bin/env python3
"""
垂直领域热词抓取模块
采集 5 个垂直领域: 开发者/开源、AI 研究、加密货币、产品设计、技术资讯

每个源返回统一结构:
  [{'keyword': str, 'score': int, 'desc': str, 'url': str, 'source': str}, ...]
"""

import json
import re
import html
import urllib.request
import urllib.error

UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'


def _fetch(url: str, timeout: int = 20) -> bytes:
    req = urllib.request.Request(url, headers={
        'User-Agent': UA,
        'Accept': 'application/json,text/html,*/*',
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


# ── 1. 开发者/开源: GitHub Trending ─────────────────
def fetch_github(limit: int = 12) -> list:
    """GitHub Trending 今日仓库"""
    content = _fetch('https://github.com/trending').decode('utf-8', 'ignore')
    items = []
    for art in content.split('<article')[1:]:
        repo = re.search(r'href="/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)"', art)
        if not repo:
            continue
        desc = ''
        dm = re.search(r'<p[^>]*class="[^"]*col-9[^"]*"[^>]*>(.*?)</p>', art, re.DOTALL)
        if dm:
            desc = ' '.join(html.unescape(re.sub(r'<[^>]+>', '', dm.group(1))).split())[:120]
        stars = 0
        sm = re.search(r'([\d,.]+)\s*stars?\s*today', art)
        if sm:
            stars = int(sm.group(1).replace(',', ''))
        items.append({
            'keyword': repo.group(1),
            'score': stars,
            'desc': desc,
            'url': f'https://github.com/{repo.group(1)}',
            'source': 'github',
        })
    items.sort(key=lambda x: x['score'], reverse=True)
    return items[:limit]


# ── 2. 技术资讯: Hacker News ────────────────────────
def fetch_hackernews(limit: int = 12) -> list:
    """HN 首页热帖 (Algolia 官方 API)"""
    data = json.loads(_fetch(
        'https://hn.algolia.com/api/v1/search?tags=front_page&hitsPerPage=20'
    ).decode('utf-8'))
    items = []
    for hit in data.get('hits', []):
        title = hit.get('title') or ''
        if not title:
            continue
        items.append({
            'keyword': title,
            'score': hit.get('points', 0),
            'desc': f"by {hit.get('author', '?')} · {hit.get('num_comments', 0)} comments",
            'url': hit.get('url') or f"https://news.ycombinator.com/item?id={hit.get('objectID')}",
            'source': 'hackernews',
        })
    items.sort(key=lambda x: x['score'], reverse=True)
    return items[:limit]


# ── 3. AI 研究: arXiv ──────────────────────────────
def fetch_arxiv(limit: int = 12) -> list:
    """arXiv cs.AI 最新论文"""
    content = _fetch(
        'https://export.arxiv.org/api/query?'
        'search_query=cat:cs.AI&sortBy=submittedDate&sortOrder=descending&max_results=15'
    ).decode('utf-8', 'ignore')
    entries = re.findall(r'<entry>(.*?)</entry>', content, re.DOTALL)
    items = []
    for entry in entries:
        tm = re.search(r'<title>([^<]+)</title>', entry)
        im = re.search(r'<id>([^<]+)</id>', entry)
        sm = re.search(r'<summary>([^<]+)</summary>', entry)
        if not tm:
            continue
        title = ' '.join(html.unescape(tm.group(1)).split())
        desc = ''
        if sm:
            desc = ' '.join(html.unescape(sm.group(1)).split())[:100]
        items.append({
            'keyword': title,
            'score': 0,  # arXiv 无热度,用发布时间排序
            'desc': desc,
            'url': im.group(1) if im else '',
            'source': 'arxiv',
        })
    return items[:limit]


# ── 4. 加密货币: CoinGecko ──────────────────────────
def fetch_coingecko(limit: int = 12) -> list:
    """CoinGecko 趋势币种"""
    data = json.loads(_fetch(
        'https://api.coingecko.com/api/v3/search/trending'
    ).decode('utf-8'))
    items = []
    for coin in data.get('coins', []):
        item = coin.get('item', {})
        name = item.get('name', '')
        if not name:
            continue
        items.append({
            'keyword': f"{name} ({item.get('symbol', '')})",
            'score': item.get('market_cap_rank') or 0,
            'desc': f"market rank #{item.get('market_cap_rank', '?')}",
            'url': f"https://www.coingecko.com/en/coins/{item.get('id', '')}",
            'source': 'coingecko',
        })
    # 市值排名越小越热 → 取反做 score
    for i in items:
        i['score'] = 1000 - i['score']
    items.sort(key=lambda x: x['score'], reverse=True)
    return items[:limit]


# ── 5. 产品设计: Product Hunt ──────────────────────
def fetch_producthunt(limit: int = 12) -> list:
    """Product Hunt 今日热门产品 (RSS)"""
    content = _fetch('https://www.producthunt.com/feed').decode('utf-8', 'ignore')
    entries = re.findall(r'<entry>(.*?)</entry>', content, re.DOTALL)
    items = []
    for entry in entries:
        tm = re.search(r'<title[^>]*>([^<]+)</title>', entry)
        lm = re.search(r'<link[^>]*href="([^"]+)"', entry)
        if not tm:
            continue
        items.append({
            'keyword': html.unescape(tm.group(1)).strip(),
            'score': 0,
            'desc': '',
            'url': lm.group(1) if lm else 'https://www.producthunt.com/',
            'source': 'producthunt',
        })
    return items[:limit]


# ── 6. 金融/投资: Finviz + Yahoo Finance ────────────
def fetch_finance(limit: int = 12) -> list:
    """金融热词: Finviz 头条新闻 + Yahoo Finance 趋势 ticker"""
    items = []
    # Finviz 新闻头条
    try:
        content = _fetch('https://finviz.com/news.ashx').decode('utf-8', 'ignore')
        headlines = re.findall(r'data-boxover-text="([^"]+)"', content)
        for h in headlines[:limit]:
            items.append({
                'keyword': html.unescape(h)[:70],
                'score': 0,
                'desc': 'Finviz Financial News',
                'url': 'https://finviz.com/news.ashx',
                'source': 'finviz',
            })
    except Exception:
        pass
    # Yahoo Finance 趋势 ticker
    try:
        content = _fetch('https://finance.yahoo.com/trending-tickers/').decode('utf-8', 'ignore')
        # 定位 quotes 数组并整体解析
        start = content.find('"fullExchangeName"')
        arr_start = content.rfind('[', 0, start)
        if arr_start >= 0:
            depth = 0
            for i in range(arr_start, len(content)):
                if content[i] == '[':
                    depth += 1
                elif content[i] == ']':
                    depth -= 1
                    if depth == 0:
                        quotes = json.loads(content[arr_start:i+1])
                        for q in quotes[:limit]:
                            sym = q.get('symbol', '')
                            name = q.get('shortName') or q.get('longName') or ''
                            score = q.get('trendingScore', {}).get('raw', 0)
                            items.append({
                                'keyword': f'${sym} - {name}',
                                'score': round(score, 2),
                                'desc': 'Yahoo Finance Trending',
                                'url': f'https://finance.yahoo.com/quote/{sym}',
                                'source': 'yahoo',
                            })
                        break
    except Exception:
        pass
    items.sort(key=lambda x: x['score'], reverse=True)
    return items[:limit]


# ── 7. 营销/公关/舆情: Google News ─────────────────
def fetch_marketing(limit: int = 12) -> list:
    """营销舆情: Google News Business 头条"""
    content = _fetch(
        'https://news.google.com/rss/headlines/section/topic/BUSINESS?hl=en-US&gl=US&ceid=US:en'
    ).decode('utf-8', 'ignore')
    titles = re.findall(r'<title>([^<]+)</title>', content)
    items = []
    for t in titles[2:limit+2]:  # 跳过前2个 (feed 元信息)
        items.append({
            'keyword': html.unescape(t).strip()[:70],
            'score': 0,
            'desc': 'Google News Business',
            'url': 'https://news.google.com/',
            'source': 'googlenews',
        })
    return items


# ── 8. 电商/跨境选品: Google News Business + 电商关键词 ──
def fetch_ecommerce(limit: int = 12) -> list:
    """电商选品: Google News Business + 电商关键词过滤 + 品类词"""
    # 用 Google News Business + 电商/零售相关新闻
    content = _fetch(
        'https://news.google.com/rss/headlines/section/topic/BUSINESS?hl=en-US&gl=US&ceid=US:en'
    ).decode('utf-8', 'ignore')
    titles = re.findall(r'<title>([^<]+)</title>', content)
    items = []
    # 电商关键词过滤
    ecom_kw = ['retail', 'shop', 'sale', 'prime', 'deal', 'product', 'brand',
               'launch', 'amazon', 'walmart', 'target', 'etsy', 'ebay',
               'fashion', 'beauty', 'gadget', 'tech', 'wearable', 'toy',
               'top', 'trending', 'bestseller', 'popular']
    for t in titles[2:]:  # 跳过前2个(feed 元信息)
        t_clean = html.unescape(t).strip()
        t_lower = t_clean.lower()
        if any(kw in t_lower for kw in ecom_kw):
            items.append({
                'keyword': t_clean[:70],
                'score': 0,
                'desc': 'E-commerce / Retail News',
                'url': 'https://news.google.com/',
                'source': 'ecom',
            })
    # 如果过滤后不足,补通用商业新闻
    if len(items) < limit:
        for t in titles[2:limit+2]:
            t_clean = html.unescape(t).strip()[:70]
            if not any(i['keyword'] == t_clean for i in items):
                items.append({
                    'keyword': t_clean,
                    'score': 0,
                    'desc': 'Business News',
                    'url': 'https://news.google.com/',
                    'source': 'ecom',
                })
    return items[:limit]


# ── 9. 社媒监听/舆情: Reddit Popular ────────────────
import time as _time  # 模块内延迟,避免外部 import 冲突

def fetch_social(limit: int = 12) -> list:
    """社媒舆情: Reddit r/popular 热帖"""
    _time.sleep(2)  # 冷却,避免限流
    content = _fetch(
        'https://www.reddit.com/r/popular/hot/.rss?limit=15'
    ).decode('utf-8', 'ignore')
    titles = re.findall(r'<title>([^<]+)</title>', content)
    items = []
    for t in titles[2:limit+2]:  # 略过前2个(Reddit 元信息)
        td = html.unescape(t).strip()[:70]
        items.append({
            'keyword': td,
            'score': 0,
            'desc': 'Reddit Popular',
            'url': 'https://www.reddit.com/r/popular/',
            'source': 'reddit',
        })
    return items


# ── 10. 体育博彩/电竞: ESPN News ────────────────────
def fetch_sports(limit: int = 12) -> list:
    """体育热词: ESPN 新闻 + 联盟新闻"""
    items = []
    # ESPN 综合新闻
    try:
        content = _fetch('https://www.espn.com/espn/rss/news').decode('utf-8', 'ignore')
        titles = re.findall(r'<title>(?:<!\[CDATA\[)?([^\]<]+)', content)
        for t in titles[2:limit+2]:  # 略过前2个
            items.append({
                'keyword': html.unescape(t).strip()[:70],
                'score': 0,
                'desc': 'ESPN Sports News',
                'url': 'https://www.espn.com/',
                'source': 'espn',
            })
    except Exception:
        pass
    # 补充: 英超联赛新闻
    if len(items) < limit:
        try:
            content = _fetch(
                'https://site.web.api.espn.com/apis/site/v2/sports/soccer/eng.1/news'
            ).decode('utf-8', 'ignore')
            articles = json.loads(content).get('articles', [])
            for a in articles:
                items.append({
                    'keyword': a.get('headline', '')[:70],
                    'score': 0,
                    'desc': 'EPL News',
                    'url': f"https://www.espn.com/soccer/story/_/id/{a.get('id', '')}",
                    'source': 'espn',
                })
        except Exception:
            pass
    return items[:limit]


# ── 11. AI 模型趋势: HuggingFace ───────────────────
def fetch_huggingface(limit: int = 12) -> list:
    """HuggingFace 趋势模型 + 趋势数据集 (AI 圈最真实的热信号)"""
    items = []
    # 趋势模型
    try:
        data = json.loads(_fetch(
            'https://huggingface.co/api/models?sort=trendingScore&direction=-1&limit=15'
        ).decode('utf-8'))
        for m in data:
            mid = m.get('modelId', '')
            if not mid:
                continue
            items.append({
                'keyword': mid,
                'score': m.get('likes', 0),
                'desc': f"{m.get('downloads', 0):,} downloads · {m.get('likes', 0)} likes",
                'url': f"https://huggingface.co/{mid}",
                'source': 'huggingface',
            })
    except Exception:
        pass
    # 趋势数据集
    try:
        data = json.loads(_fetch(
            'https://huggingface.co/api/datasets?sort=trendingScore&direction=-1&limit=5'
        ).decode('utf-8'))
        for m in data:
            did = m.get('id', '')
            if not did:
                continue
            items.append({
                'keyword': f'📊 {did}',
                'score': m.get('likes', 0),
                'desc': 'Trending Dataset',
                'url': f"https://huggingface.co/datasets/{did}",
                'source': 'huggingface',
            })
    except Exception:
        pass
    items.sort(key=lambda x: x['score'], reverse=True)
    return items[:limit]


# ── 12. 具身机器人: arXiv cs.RO ────────────────────
def fetch_robotics(limit: int = 12) -> list:
    """具身机器人/机器人学论文 (arXiv cs.RO)"""
    content = _fetch(
        'https://export.arxiv.org/api/query?'
        'search_query=cat:cs.RO&sortBy=submittedDate&sortOrder=descending&max_results=20'
    ).decode('utf-8', 'ignore')
    entries = re.findall(r'<entry>(.*?)</entry>', content, re.DOTALL)
    items = []
    for entry in entries:
        tm = re.search(r'<title>([^<]+)</title>', entry)
        im = re.search(r'<id>([^<]+)</id>', entry)
        sm = re.search(r'<summary>([^<]+)</summary>', entry)
        if not tm:
            continue
        title = ' '.join(html.unescape(tm.group(1)).split())
        desc = ''
        if sm:
            desc = ' '.join(html.unescape(sm.group(1)).split())[:100]
        items.append({
            'keyword': title,
            'score': 0,
            'desc': desc,
            'url': im.group(1) if im else '',
            'source': 'robotics',
        })
    return items[:limit]


# ── 13. MCP 服务器: Smithery registry ─────────────
def fetch_mcp(limit: int = 20) -> list:
    """最火的 MCP 服务器 (Smithery registry, 按 useCount 排序, 分页合并)"""
    items = []
    for page in range(1, 4):  # 每页固定 10 条, 抓 3 页
        try:
            req = urllib.request.Request(
                f'https://registry.smithery.ai/servers?page={page}',
                headers={'User-Agent': UA, 'Accept': 'application/json'})
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode('utf-8'))
            for s in data.get('servers', []):
                qname = s.get('qualifiedName', '')
                if not qname:
                    continue
                display = s.get('displayName') or qname
                desc = ' '.join(re.sub(r'\[.*?\]\(.*?\)', '', s.get('description', '')).split())[:150]
                items.append({
                    'keyword': f'🔌 {display}',
                    'score': s.get('useCount', 0),
                    'desc': desc or s.get('homepage', ''),
                    'url': s.get('homepage') or f'https://smithery.ai/server/{qname}',
                    'source': 'mcp',
                })
        except Exception:
            break
    items.sort(key=lambda x: x['score'], reverse=True)
    return items[:limit]


# ── 14. Agent Skills: GitHub 搜索 ──────────────────
def fetch_skills(limit: int = 20) -> list:
    """最火的 Agent Skills (GitHub 搜索, 按 stars 排序, 只保留 skills 相关)"""
    items = []
    seen = set()
    try:
        req = urllib.request.Request(
            'https://api.github.com/search/repositories?'
            'q=agent+skills+OR+claude+skills+OR+ai+skills&sort=stars&order=desc&per_page=50',
            headers={'User-Agent': UA, 'Accept': 'application/vnd.github+json'})
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        for r in data.get('items', []):
            name = r.get('full_name', '')
            desc = (r.get('description') or '')
            low = (name + ' ' + desc).lower()
            # 只保留真正与 skills 相关的仓库
            if not any(k in low for k in ('skill', 'agent-skill', 'skills')):
                continue
            if not name or name in seen:
                continue
            seen.add(name)
            items.append({
                'keyword': f'⚡ {name}',
                'score': r.get('stargazers_count', 0),
                'desc': desc.strip()[:150] or f"★{r.get('stargazers_count', 0)} stars",
                'url': r.get('html_url', ''),
                'source': 'skills',
            })
    except Exception:
        pass
    items.sort(key=lambda x: x['score'], reverse=True)
    return items[:limit]


# ── 汇总 ───────────────────────────────────────────
VERTICALS = {
    'dev':       {'label': '🧑💻 开发者/开源',         'fetch': fetch_github},
    'news':      {'label': '📰 技术资讯',            'fetch': fetch_hackernews},
    'ai':        {'label': '🤖 AI 研究',             'fetch': fetch_arxiv},
    'ai_models': {'label': '🤖 AI 模型趋势',         'fetch': fetch_huggingface},
    'robotics':  {'label': '🦾 具身机器人',          'fetch': fetch_robotics},
    'crypto':    {'label': '💰 加密货币',            'fetch': fetch_coingecko},
    'product':   {'label': '🎨 产品设计',            'fetch': fetch_producthunt},
    'finance':   {'label': '🏦 金融/投资',           'fetch': fetch_finance},
    'marketing': {'label': '📣 营销/公关/舆情',       'fetch': fetch_marketing},
    'ecommerce': {'label': '🛒 电商/跨境选品',        'fetch': fetch_ecommerce},
    'social':    {'label': '💬 社媒监听/舆情',        'fetch': fetch_social},
    'sports':    {'label': '🏈 体育博彩/电竞',        'fetch': fetch_sports},
    'mcp':       {'label': '🔌 MCP 服务器',           'fetch': fetch_mcp},
    'skills':    {'label': '⚡ Agent Skills',         'fetch': fetch_skills},
}


def fetch_all(verbose: bool = False) -> dict:
    """抓取所有垂直领域,返回 {vertical_key: [items]}"""
    result = {}
    for key, v in VERTICALS.items():
        try:
            items = v['fetch']()
            result[key] = items
            if verbose:
                print(f"   ✓ {v['label']}: {len(items)} 条")
        except Exception as e:
            result[key] = []
            if verbose:
                print(f"   ✗ {v['label']} 失败: {e}")
    return result


if __name__ == '__main__':
    print('抓取垂直领域热词...')
    data = fetch_all(verbose=True)
    for key, items in data.items():
        print(f"\n{VERTICALS[key]['label']} ({len(items)})")
        for i, item in enumerate(items[:5], 1):
            print(f"  {i}. [{item['score']:>5}] {item['keyword'][:60]}")
