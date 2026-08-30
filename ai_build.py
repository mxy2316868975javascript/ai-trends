#!/usr/bin/env python3
"""
AI 热词网站数据构建器
 - 从当日快照提取 AI 相关数据(模型/论文/产品/讨论)
 - 独立抓取 HuggingFace 趋势模型
 - 生成 data/ai/ai_daily.json (网站读取)
 - 生成 ai_site/index.html (静态网站,无需服务器)

用法:
  python3 ai_build.py            # 构建 AI 数据 + 网站
  python3 ai_build.py --serve    # 构建后提示本地预览
"""

import json
import re
import sys
import argparse
from datetime import datetime, timezone
from pathlib import Path

import vertical_trends as vt

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / 'data' / 'trends'
AI_DIR = BASE_DIR / 'data' / 'ai'
SITE_DIR = BASE_DIR  # index.html 输出到仓库根 (兼容 GitHub Actions 部署)


# AI 相关关键词(用于从通用源过滤)
AI_KEYWORDS = [
    'ai', 'llm', 'gpt', 'openai', 'anthropic', 'claude', 'gemini', 'qwen',
    'deepseek', 'llama', 'mistral', 'agent', 'model', 'neural', 'machine learning',
    'deep learning', 'transformer', 'diffusion', 'token', 'inference', 'fine-tun',
    'prompt', 'rag', 'embedding', 'gpu', 'cuda', 'robot', 'computer vision',
    'nlp', 'speech', 'whisper', 'stable diffusion', 'chatbot', 'copilot',
    'generative', 'multimodal', 'instruct', 'pretrain', 'huggingface',
    # 隐含 AI 的产品/工具词
    'analyzer', 'parse', 'craft', 'copilot', 'assistant', 'intelligence',
    'automation', 'workflow', 'pipeline', 'sdk', 'llm', 'chat', 'vision',
]


def fetch_hn_ai(limit: int = 10) -> list:
    """HN AI 相关高分帖 (Algolia 搜索)"""
    try:
        data = json.loads(vt._fetch(
            'https://hn.algolia.com/api/v1/search?query=AI&tags=story'
            '&numericFilters=points%3E100&hitsPerPage=20'
        ).decode('utf-8'))
        items = []
        for hit in data.get('hits', []):
            title = hit.get('title') or ''
            if not title:
                continue
            items.append({
                'keyword': title,
                'score': hit.get('points', 0),
                'desc': f"{hit.get('points', 0)} pts · {hit.get('num_comments', 0)} comments",
                'url': hit.get('url') or f"https://news.ycombinator.com/item?id={hit.get('objectID')}",
                'source': 'hn_ai',
            })
        items.sort(key=lambda x: x['score'], reverse=True)
        return items[:limit]
    except Exception:
        return []


def is_ai_relevant(text: str) -> bool:
    tl = (text or '').lower()
    return any(kw in tl for kw in AI_KEYWORDS)


def load_latest_snapshot() -> dict:
    """读取最新一天的快照"""
    files = sorted(DATA_DIR.glob('*.json'))
    history = [f for f in files if f.name != 'history.json']
    if not history:
        return {}
    with open(history[-1]) as f:
        return json.load(f)


def build_ai_data() -> dict:
    snap = load_latest_snapshot()
    date = snap.get('date', datetime.now(timezone.utc).strftime('%Y-%m-%d'))
    verticals = snap.get('verticals', {})

    result = {
        'date': date,
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'models': [],       # HF 趋势模型
        'datasets': [],     # HF 趋势数据集
        'papers': [],       # arXiv AI 论文
        'robotics': [],     # arXiv 机器人论文
        'products': [],     # Product Hunt AI 产品
        'discussions': [],  # HN AI 讨论
        'repos': [],        # GitHub AI 仓库
        'mcp': [],          # MCP 服务器 (Smithery)
        'skills': [],       # Agent Skills (GitHub)
    }

    # 1. HuggingFace 模型
    try:
        hf = vt.fetch_huggingface(limit=15)
        for item in hf:
            if '📊' in item['keyword']:
                result['datasets'].append(item)
            else:
                result['models'].append(item)
    except Exception:
        pass

    # 2. arXiv 论文 (全部是 AI)
    for item in verticals.get('ai', []):
        result['papers'].append(item)

    # 2b. 具身机器人论文 (arXiv cs.RO)
    for item in verticals.get('robotics', []):
        result['robotics'].append(item)

    # 3. Product Hunt AI 相关 (宽松: 名字含隐含AI词, 或 URL 路径含 ai)
    for item in verticals.get('product', []):
        kw = item['keyword'].lower()
        url = (item.get('url') or '').lower()
        if is_ai_relevant(kw) or 'ai' in url or '/ai-' in url:
            result['products'].append(item)

    # 4. HN AI 讨论 (专用搜索, 质量高)
    result['discussions'] = fetch_hn_ai()

    # 5. GitHub AI 仓库
    for item in verticals.get('dev', []):
        if is_ai_relevant(item['keyword']) or is_ai_relevant(item.get('desc', '')):
            result['repos'].append(item)

    # 5b. MCP 服务器 (Smithery 最火, 至少 18 条)
    try:
        result['mcp'] = vt.fetch_mcp(limit=20)
    except Exception:
        result['mcp'] = []

    # 5c. Agent Skills (GitHub 最火, 至少 18 条)
    try:
        result['skills'] = vt.fetch_skills(limit=20)
    except Exception:
        result['skills'] = []

    # 提取 AI 热词 (从所有标题里抽关键词)
    result['keywords'] = extract_ai_keywords(result)
    return result


def extract_ai_keywords(data: dict) -> list:
    """
    跨源关联: 实体级热词检测
    - 模型/仓库名: 保持完整实体,去量化后缀噪声
    - 论文/讨论: 提取专有名词(大写开头词组)
    - 跨生态计数: 同一实体出现在不同平台生态 = 真全网热点
      (生态: HF / arXiv / HN / GitHub / ProductHunt)
    - 停用词过滤: 排除通用英文词和形容词
    """
    STOPWORDS = {
        'with', 'under', 'over', 'into', 'from', 'their', 'about', 'across',
        'after', 'before', 'between', 'through', 'during', 'without', 'while',
        'model', 'models', 'data', 'system', 'using', 'based', 'method',
        'approach', 'task', 'paper', 'work', 'result', 'proposed', 'new',
        'large', 'first', 'efficient', 'fast', 'via', 'toward', 'towards',
        'learning', 'deep', 'machine', 'language', 'optimization', 'prediction',
        'state', 'time', 'control', 'performance', 'real', 'world', 'cross',
        'making', 'generation', 'design', 'action', 'frame', 'weight',
        'multiple', 'spatial', 'top', 'zero', 'shot', 'one', 'point',
        'future', 'set', 'problem', 'requires', 'possible', 'evaluation',
        'planning', 'detection', 'recognition', 'segmentation', 'generative',
        'multi', 'single', 'scale', 'vision', 'processing', 'engineering',
        'against', 'after', 'still', 'also', 'even', 'already', 'much',
        'just', 'don', 'very', 'though', 'like', 'well', 'now', 'really',
        'break', 'step', 'hands', 'back', 'line', 'cat', 'human', 'show',
        # 形容词/副词(高频污染源)
        'robust', 'efficient', 'physical', 'video', 'simulators', 'fine',
        'static', 'dynamic', 'better', 'fewer', 'persistent', 'realistic',
        'continuous', 'effective', 'strong', 'general', 'local', 'global',
        'online', 'automatic', 'adaptive', 'scalable', 'reliable',
    }

    # 量化/修饰后缀 — 去掉后做实体归一化
    SUFFIXES = [
        '-GGUF', '-FP8', '-MLX', '-BF16', '-FP16', '-INT8', '-AWQ', '-GPTQ',
        '-OBLITERATED', '-Uncensored', '-HauhauCS', '-Aggressive', '-MTP',
        '-Next', '-Flash', '-GGUF', '-FP8',
    ]

    # 平台生态映射: 细分源 → 生态
    ECO_LABEL = {
        'models': 'HF', 'datasets': 'HF', 'repos': 'GitHub',
        'papers': 'arXiv', 'robotics': 'arXiv',
        'discussions': 'HN', 'products': 'PH',
    }

    def normalize_model(name: str) -> str:
        """模型/仓库名归一化: 去量化后缀、去版本号"""
        for suf in SUFFIXES:
            if name.endswith(suf):
                name = name[:-len(suf)]
        name = re.sub(r'-V\d+$', '', name)
        if len(name) > 25:
            parts = re.split(r'[-_ ]', name)
            if len(parts) >= 2:
                name = f'{parts[0]}-{parts[1]}'
        return name[:30]

    def extract_entities(text: str) -> list:
        """从文本提取专有名词词组"""
        entities = []
        # 模式1: 大写开头连续词组
        for m in re.finditer(r'([A-Z][a-z0-9]+(?:[\s-][A-Z][a-z0-9]+)*)', text):
            ent = m.group(0).strip()
            if len(ent) >= 4 and ent.lower() not in STOPWORDS:
                # 首词必须是真专有名词(过滤 From Static/Better Performance)
                first = ent.split()[0].split('-')[0]
                if first.lower() not in STOPWORDS and first not in (
                        'From', 'Beyond', 'Under', 'Against', 'Making'):
                    entities.append(ent)
        # 模式2: 全大写缩写
        for m in re.finditer(r'\b([A-Z]{2,8})\b', text):
            ent = m.group(0)
            if len(ent) >= 3 and ent not in ('AI', 'US', 'UK'):
                entities.append(ent)
        return entities

    # 1. 收集各源实体,记录生态
    eco_map = {}  # {实体: {生态集合}}
    for item in data['models']:
        ent = normalize_model(item['keyword'].split('/')[-1])
        if ent:
            eco_map.setdefault(ent, set()).add('HF')
    for item in data['datasets']:
        ent = item['keyword'].replace('📊 ', '').split('/')[-1]
        if ent:
            eco_map.setdefault(ent, set()).add('HF')
    for item in data['repos']:
        ent = item['keyword'].split('/')[-1]
        if ent:
            eco_map.setdefault(ent, set()).add('GitHub')
    for item in data['papers'] + data['robotics']:
        for ent in extract_entities(item['keyword']):
            eco_map.setdefault(ent, set()).add('arXiv')
    for item in data['discussions']:
        for ent in extract_entities(item['keyword']):
            eco_map.setdefault(ent, set()).add('HN')
    for item in data['products']:
        eco_map.setdefault(item['keyword'], set()).add('PH')

    # 2. 跨生态统计: 出现在≥2个生态的实体 = 全网热点
    cross = {k: v for k, v in eco_map.items() if len(v) >= 2}
    singles = {k: v for k, v in eco_map.items() if len(v) == 1}

    # 3. 排序: 跨生态优先 → 热度(模型likes)加权
    result = []
    for ent, ecos in cross.items():
        score = len(ecos) * 10
        for m in data['models']:
            if normalize_model(m['keyword'].split('/')[-1]) == ent:
                score += 1 + min(round(m['score'] / 100), 20)
                break
        for r in data['repos']:
            if r['keyword'].split('/')[-1] == ent:
                score += 1 + min(round(r['score'] / 100), 10)
                break
        result.append({'word': ent, 'count': len(ecos),
                       'sources': sorted(ecos), 'cross': True, 'score': score})

    # 补充: 各生态 Top 词(当跨生态词不足时)
    if len(result) < 8:
        top_per_eco = {'HF': [], 'arXiv': [], 'HN': [], 'GitHub': [], 'PH': []}
        for ent, ecos in singles.items():
            eco = list(ecos)[0]
            if eco in top_per_eco:
                score = 0
                for m in data['models']:
                    if normalize_model(m['keyword'].split('/')[-1]) == ent:
                        score = 5 + min(round(m['score'] / 100), 15)
                        break
                top_per_eco[eco].append((ent, score))
        for eco, items in top_per_eco.items():
            items.sort(key=lambda x: x[1], reverse=True)
            for ent, score in items[:3]:
                if not any(r['word'] == ent for r in result):
                    result.append({'word': ent, 'count': 1,
                                   'sources': [eco], 'cross': False, 'score': score})

    result.sort(key=lambda x: x['score'], reverse=True)
    return result[:20]

    result.sort(key=lambda x: x['score'], reverse=True)
    return result[:20]


def write_json(data: dict):
    AI_DIR.mkdir(parents=True, exist_ok=True)
    with open(AI_DIR / 'ai_daily.json', 'w') as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
    print(f'💾 AI 数据已生成: {AI_DIR}/ai_daily.json')
    print(f'   模型 {len(data["models"])} · 论文 {len(data["papers"])} · '
          f'产品 {len(data["products"])} · 讨论 {len(data["discussions"])} · '
          f'仓库 {len(data["repos"])}')


def write_site(data: dict):
    """生成静态 HTML 网站 (基于 template.html, 替换数据占位符)"""
    # 读取用户设计的浅色模板
    template_path = Path(__file__).parent / 'template.html'
    with open(template_path, 'r', encoding='utf-8') as f:
        template = f.read()

    # 序列化 JSON 嵌入页面
    json_str = json.dumps(data, ensure_ascii=False)
    html = template.replace('__DATA_JSON__', json_str)

    with open(SITE_DIR / 'index.html', 'w', encoding='utf-8') as f:
        f.write(html)
    print(f'🌐 网站已生成: {SITE_DIR}/index.html')


def main():
    parser = argparse.ArgumentParser(description='AI 热词网站构建器')
    parser.add_argument('--serve', action='store_true', help='生成后提示本地预览')
    args = parser.parse_args()

    data = build_ai_data()
    write_json(data)
    write_site(data)

    if args.serve:
        print('\n本地预览: cd ai_site && python3 -m http.server 8000')
        print('然后浏览器打开 http://localhost:8000')


if __name__ == '__main__':
    main()