#!/usr/bin/env python3
"""
Google Trends 热词筛选器
从 Google Trends RSS 抓取多地区趋势数据 → 去噪 → 分类 → 价值排序

用法:
  python3 trends_filter.py                   # 抓取并筛选全部地区
  python3 trends_filter.py --regions US,GB    # 指定地区
  python3 trends_filter.py --from-cache       # 从上次缓存分析(不重新抓取)
"""

import xml.etree.ElementTree as ET
import json
import os
import re
import sys
from datetime import datetime, timezone

# ── 配置 ──────────────────────────────────
CACHE_FILE = '/tmp/gt_all.json'
RSS_BASE = 'https://trends.google.com/trending/rss'
DEFAULT_REGIONS = ['US', 'GB', 'CA', 'AU', 'DE', 'IN', 'JP', 'KR']

# ── 噪音词表(全部小写,匹配关键词) ─────────
NOISE_KEYWORDS = {
    # 赌博/博彩
    'draftkings', 'betmgm', 'betfair', 'bet365', 'fanduel',
    'casino', 'poker', 'slot', 'lottery', 'lotto',
    # 投资平台/品牌导航搜索
    'motley fool', 'fool.com', 'themotleyfool', 'robinhood',
    'schwab', 'vanguard', 'fidelity',
    # 通用品牌(无事件驱动的纯导航搜索)
    'mastercard', 'visa', 'amex', 'paypal',
    'gmail', 'outlook', 'yahoo mail', 'facebook login',
    'instagram login', 'twitter login',
    # 彩票开奖
    'winning numbers',
    # 天气查询(常规)
    'wetter', 'weather', 'temperature',
    # 词典/翻译
    'translate', 'dictionary',
}

# ── 价值分类 ──────────────────────────────
CATEGORY_MAP = {
    'technology': {
        'keywords': ('intel', 'dlss', 'rtx', 'gpu', 'cpu', 'ai ', 'artificial intel',
                     'chatgpt', 'gpt', 'llm', 'openai', 'apple', 'iphone', 'samsung',
                     'google', 'microsoft', 'tesla', 'spacex', 'nvidia', 'amd',
                     'quantum', 'robot', 'software', 'algorithm', 'data', 'cyber',
                     'chip', 'semiconductor', '5g', 'blockchain', 'web3'),
        'weight': 1.2
    },
    'breaking_news': {
        'keywords': ('shooting', 'earthquake', 'storm', 'hurricane', 'tornado',
                     'flood', 'wildfire', 'crash', 'attack', 'war', 'explosion',
                     'hostage', 'arrest', 'indictment', 'verdict', 'trial',
                     'final results', 'missing', 'killed', 'death', 'died',
                     'resign', 'sanctions', 'lawsuit', 'protest'),
        'weight': 2.0  # 突发新闻权重最高
    },
    'science': {
        'keywords': ('study', 'research', 'discovery', 'vaccine', 'cure',
                     'treatment', 'disease', 'cancer', 'brain', 'dna', 'gene',
                     'climate', 'global warming', 'nasa', 'space', 'planet',
                     'mars', 'moon', 'asteroid', 'ocean'),
        'weight': 1.3
    },
    'entertainment': {
        'keywords': ('movie', 'film', 'tv', 'show', 'netflix', 'disney',
                     'hbo', 'music', 'album', 'concert', 'tour', 'actor',
                     'actress', 'singer', 'youtube', 'tiktok', 'premiere',
                     'season', 'episode'),
        'weight': 0.9
    },
    'politics': {
        'keywords': ('president', 'election', 'congress', 'senate', 'parliament',
                     'vote', 'policy', 'government', 'minister', 'ruling',
                     'supreme court', 'law', 'bill', 'tax', 'tariff',
                     'republican', 'democrat', 'party', 'kim jong', 'xi ',
                     'putin', 'trump', 'biden', 'modi', 'prime minister'),
        'weight': 1.4
    },
    'sports': {
        'keywords': ('nfl', 'nba', 'mlb', 'nhl', 'soccer', 'football',
                     'basketball', 'baseball', 'tennis', 'golf', 'f1',
                     'nascar', 'champions', 'playoff', 'final', 'race',
                     'grand prix', 'olympic', 'world cup', 'match', 'game',
                     'eels', 'sharks', 'warriors', 'nathan cleary', 'jacob saifiti',
                     'sanfl', 'nascar', 'ohl', 'ben shelton'),
        'weight': 0.8
    },
    'business': {
        'keywords': ('stock', 'market', 'bitcoin', 'crypto', 'ipo',
                     'acquisition', 'merger', 'revenue', 'profit', 'layoff',
                     'debt', 'bankrupt', 'inflation', 'recession', 'gdp',
                     'poor dad', 'rich dad'),
        'weight': 1.1
    }
}


def is_noise(keyword: str) -> bool:
    kw = keyword.lower().strip()
    # 纯品牌搜索: 单一名词匹配噪音集
    if kw in NOISE_KEYWORDS:
        return True
    for noise in NOISE_KEYWORDS:
        if len(noise) >= 4 and noise in kw:
            return True
    # 域名类搜索 (xxx.com / .org)
    if re.search(r'[a-z0-9]+\.[a-z]{2,}', kw):
        return True
    return False


def classify_keyword(keyword: str) -> tuple:
    """返回 (category, weight)"""
    kw = keyword.lower().strip()
    best_cat = 'uncategorized'
    best_weight = 0.5

    for cat, info in CATEGORY_MAP.items():
        for kw_pattern in info['keywords']:
            if kw_pattern in kw:
                if info['weight'] > best_weight:
                    best_cat = cat
                    best_weight = info['weight']
                break

    return best_cat, best_weight


def parse_traffic(traffic_str: str) -> int:
    """'5000+' → 5000, '200+' → 200"""
    if not traffic_str:
        return 0
    return int(traffic_str.replace('+', '').replace(',', '').strip())


def fetch_region(geo: str) -> list:
    import urllib.request
    url = f'{RSS_BASE}?geo={geo}'
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()

    tree = ET.ElementTree(ET.fromstring(raw))
    root = tree.getroot()
    ns = {'ht': 'https://trends.google.com/trending/rss'}
    items = []
    for item in root.findall('.//item'):
        title = item.find('title')
        traffic = item.find('ht:approx_traffic', ns)
        if title is None or traffic is None:
            continue

        news_item = item.find('ht:news_item', ns)
        news_title = ''
        news_source = ''
        if news_item is not None:
            nt = news_item.find('ht:news_item_title', ns)
            if nt is not None and nt.text:
                news_title = nt.text[:120]
            ns_el = news_item.find('ht:news_item_source', ns)
            if ns_el is not None and ns_el.text:
                news_source = ns_el.text

        items.append({
            'keyword': title.text,
            'traffic_raw': traffic.text,
            'traffic_num': parse_traffic(traffic.text),
            'news_title': news_title,
            'news_source': news_source,
            'geo': geo
        })
    return items


def filter_and_score(items: list) -> list:
    """去噪 → 分类 → 打分 → 排序"""
    scored = []
    for item in items:
        # 去噪
        if is_noise(item['keyword']):
            continue

        cat, cat_weight = classify_keyword(item['keyword'])
        # 如果是 uncategorized,还要看 traffic 是否够高才保留
        if cat == 'uncategorized' and item['traffic_num'] < 500:
            continue  # 低热度无分类 → 舍弃

        # 综合评分: traffic × 分类权重
        score = item['traffic_num'] * cat_weight

        # 同地区同关键词去重
        scored.append({
            **item,
            'category': cat,
            'score': round(score, 1)
        })

    # 按评分降序
    scored.sort(key=lambda x: x['score'], reverse=True)
    return scored


# ── 主流程 ────────────────────────────────
def main():
    regions = DEFAULT_REGIONS
    use_cache = False
    from_cache = False

    for arg in sys.argv[1:]:
        if arg.startswith('--regions='):
            regions = arg.split('=', 1)[1].split(',')
        elif arg == '--from-cache':
            from_cache = True

    # 1. 获取数据
    if from_cache:
        with open(CACHE_FILE) as f:
            all_data = json.load(f)
        items = []
        for geo, geo_items in all_data.items():
            for gi in geo_items:
                gi['geo'] = geo
                gi['traffic_num'] = parse_traffic(gi.get('traffic_raw', gi.get('traffic', '0')))
                items.append(gi)
    else:
        items = []
        for geo in regions:
            region_items = fetch_region(geo)
            items.extend(region_items)
            print(f"  ✓ {geo}: {len(region_items)} items", file=sys.stderr)

    # 2. 筛选 & 打分
    filtered = filter_and_score(items)

    # 3. 输出
    print("\n" + "=" * 72)
    print(f"  Google Trends 热词筛选报告")
    print(f"  扫描: {len(items)} 条原始数据 → {len(filtered)} 条有价值热词")
    print(f"  噪音过滤率: {(1 - len(filtered)/len(items))*100:.0f}%  (剔除赌博/品牌导航搜索)")
    print(f"  生成时间: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 72)

    # 按类别分组输出
    categories_order = ['breaking_news', 'technology', 'science', 'business',
                        'politics', 'entertainment', 'sports', 'uncategorized']

    for cat in categories_order:
        cat_items = [i for i in filtered if i['category'] == cat]
        if not cat_items:
            continue
        cat_label = cat.replace('_', ' ').title()
        print(f"\n  ▎{cat_label} ({len(cat_items)})")
        print(f"  {'─' * 60}")
        for i, item in enumerate(cat_items[:8], 1):
            traffic_str = f"{item['traffic_num']:>5,}"
            keyword = item['keyword'][:40]
            geo_str = f"[{item['geo']}]"
            score_str = f"score={item['score']}"
            print(f"  {i:2d}. {traffic_str}  {keyword:<40s} {geo_str:>6s} {score_str:>12s}")
            if item['news_source']:
                src = item['news_source'][:50]
                print(f"       └─ {src}")

    # 4. 保存
    output = {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'total_raw': len(items),
        'total_filtered': len(filtered),
        'noise_removed': len(items) - len(filtered),
        'items': filtered
    }
    with open('/tmp/gt_filtered.json', 'w') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"\n  💾 完整结果已保存: /tmp/gt_filtered.json ({len(filtered)} 条)")
    print(f"  💾 排序热词列表: /tmp/gt_filtered_simple.txt")


if __name__ == '__main__':
    main()