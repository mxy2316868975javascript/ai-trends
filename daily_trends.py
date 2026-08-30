#!/usr/bin/env python3
"""
每日热词自动抓取调度器
 - 抓取 Google Trends 多地区 RSS (去噪/分类/打分)
 - 抓取 5 个垂直领域: 开发者/GitHub、技术资讯/HN、AI研究/arXiv、加密/CoinGecko、产品/Product Hunt
 - 按日期存档到 data/trends/YYYY-MM-DD.json
 - 累积历史到 data/trends/history.json (热词网站核心资产)
 - 写运行日志 data/logs/trends.log

用法:
  python3 daily_trends.py             # 手动跑一次
  python3 daily_trends.py --regions US,GB,JP
  python3 daily_trends.py --report    # 只看最近7天报告,不抓取
"""

import json
import os
import sys
import time
import argparse
from datetime import datetime, timezone
from pathlib import Path

import trends_filter as tf
import vertical_trends as vt

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / 'data' / 'trends'
LOG_DIR = BASE_DIR / 'data' / 'logs'
LOG_FILE = LOG_DIR / 'trends.log'
HISTORY_FILE = DATA_DIR / 'history.json'


def log(msg: str):
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    line = f'[{ts}] {msg}'
    print(line, flush=True)
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, 'a') as f:
            f.write(line + '\n')
    except Exception:
        pass  # 日志失败不影响主流程


def load_history() -> dict:
    if HISTORY_FILE.exists():
        try:
            with open(HISTORY_FILE) as f:
                return json.load(f)
        except Exception as e:
            log(f'⚠ history.json 损坏,重建: {e}')
    return {'first_run': None, 'last_run': None, 'runs': 0, 'daily': {}}


def save_history(history: dict):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = HISTORY_FILE.with_suffix('.json.tmp')
    with open(tmp, 'w') as f:
        json.dump(history, f, indent=1, ensure_ascii=False)
    tmp.replace(HISTORY_FILE)


def run_once(regions=None, force=False) -> dict:
    regions = regions or tf.DEFAULT_REGIONS
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')

    history = load_history()

    # 当天已跑过且非 force,跳过
    if not force and today in history.get('daily', {}):
        log(f'⏭ {today} 已抓取,跳过 (--force 可重跑)')
        return {'skipped': True, 'date': today}

    t0 = time.time()
    log(f'🚀 开始抓取 {len(regions)} 个地区: {",".join(regions)}')

    # 1. 抓取
    items = []
    fail_geos = []
    for geo in regions:
        try:
            geo_items = tf.fetch_region(geo)
            items.extend(geo_items)
            log(f'   ✓ {geo}: {len(geo_items)} 条')
        except Exception as e:
            fail_geos.append(geo)
            log(f'   ✗ {geo} 抓取失败: {e}')
        time.sleep(0.5)  # 温和限速,降低被封概率

    # 2. 筛选打分
    filtered = tf.filter_and_score(items)
    elapsed = round(time.time() - t0, 1)

    # 3. 抓取垂直领域
    log('📡 抓取垂直领域...')
    verticals = vt.fetch_all(verbose=True)

    # 4. 存档当天
    day_record = {
        'date': today,
        'fetched_at': datetime.now(timezone.utc).isoformat(),
        'regions': regions,
        'total_raw': len(items),
        'noise_removed': len(items) - len(filtered),
        'total_filtered': len(filtered),
        'failed_geos': fail_geos,
        'items': filtered,
        'verticals': verticals,
    }
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(DATA_DIR / f'{today}.json', 'w') as f:
        json.dump(day_record, f, indent=1, ensure_ascii=False)

    # 5. 更新历史
    if history['first_run'] is None:
        history['first_run'] = today
    history['last_run'] = today
    history['runs'] = history.get('runs', 0) + 1
    vertical_summary = {}
    for vk, v_items in verticals.items():
        if v_items:
            vertical_summary[vk] = [i['keyword'] for i in v_items[:5]]
    history['daily'][today] = {
        'total_raw': len(items),
        'noise_removed': len(items) - len(filtered),
        'total_filtered': len(filtered),
        'top_keywords': [i['keyword'] for i in filtered[:10]],
        'verticals': vertical_summary,
    }
    save_history(history)

    log(f'✅ 完成: {len(items)} 原始 → {len(filtered)} 有价值热词,耗时 {elapsed}s,'
        f'存档 data/trends/{today}.json')
    return day_record


def report(days: int = 7):
    """输出最近 N 天的热词趋势报告"""
    history = load_history()
    daily = history.get('daily', {})
    dates = sorted(daily.keys())[-days:]

    print('=' * 70)
    print(f'  热词历史报告 | 共运行 {history.get("runs", 0)} 天 | '
          f'从 {history.get("first_run")} 到 {history.get("last_run")}')
    print('=' * 70)

    if not dates:
        print('  尚无数据,先运行 python3 daily_trends.py')
        return

    # 统计跨天出现的关键词(持续性信号)
    keyword_days = {}
    for d in dates:
        f = DATA_DIR / f'{d}.json'
        if not f.exists():
            continue
        with open(f) as fh:
            rec = json.load(fh)
        for item in rec.get('items', []):
            kw = item['keyword']
            keyword_days.setdefault(kw, []).append({
                'date': d,
                'traffic': item['traffic_num'],
                'category': item['category'],
            })

    # 1. 最近一天榜单
    last = dates[-1]
    last_file = DATA_DIR / f'{last}.json'
    if last_file.exists():
        with open(last_file) as fh:
            rec = json.load(fh)
        print(f'\n▍{last} 当日 Top 热词 ({rec["total_filtered"]} 条)')
        print('  ' + '-' * 56)
        for i, item in enumerate(rec['items'][:10], 1):
            print(f'  {i:2d}. [{item["traffic_num"]:>5,}] {item["keyword"][:36]:<36s} '
                  f'[{item["geo"]}] {item["category"]}')

    # 2. 持续多天的关键词(真正的"热词",非一日爆款)
    persistent = {k: v for k, v in keyword_days.items() if len(v) >= 2}
    if persistent:
        print(f'\n▍持续热词 (≥2 天出现,趋势信号) — {len(persistent)} 个')
        print('  ' + '-' * 56)
        for kw, occ in sorted(persistent.items(),
                              key=lambda x: max(o['traffic'] for o in x[1]),
                              reverse=True)[:10]:
            days_str = ','.join(o['date'][5:] for o in occ)
            max_t = max(o['traffic'] for o in occ)
            cat = occ[0]['category']
            print(f'  {max_t:>5,}x {kw[:32]:<32s} [{cat}] 出现于: {days_str}')

    # 3. 新爆款(只出现1天且热度高)
    fresh = {k: v for k, v in keyword_days.items()
             if len(v) == 1 and v[0]['date'] == last and v[0]['traffic'] >= 1000}
    if fresh:
        print(f'\n▍新爆款 (仅当日出现且 ≥1000 热度) — {len(fresh)} 个')
        print('  ' + '-' * 56)
        for kw, occ in sorted(fresh.items(),
                              key=lambda x: x[1][0]['traffic'], reverse=True)[:8]:
            print(f'  {occ[0]["traffic"]:>5,}x {kw[:40]} [{occ[0]["category"]}]')

    # 4. 垂直领域热词
    if last_file.exists() and rec.get('verticals'):
        print('\n▍垂直领域热词日报')
        print('  ' + '=' * 60)
        for vk, vinfo in vt.VERTICALS.items():
            vitems = rec['verticals'].get(vk, [])
            if not vitems:
                continue
            print(f'\n  {vinfo["label"]}')
            print(f'  {"-" * 56}')
            for i, item in enumerate(vitems[:6], 1):
                score = item.get('score', 0)
                kw = item['keyword'][:42]
                if vk == 'github':
                    print(f'  {i:2d}. [{score:>4,}⭐] {kw}')
                elif vk == 'news':
                    print(f'  {i:2d}. [{score:>4}↑] {kw}')
                else:
                    print(f'  {i:2d}. {kw}')
            # 全部链接存 report 文件
        write_vertical_report(last, rec)


def write_vertical_report(date: str, rec: dict):
    """把当日垂直热词(含链接)写成 Markdown,方便浏览/转发"""
    from pathlib import Path as _P
    out = _P(__file__).parent / 'data' / 'reports' / f'{date}_vertical.md'
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [f'# 垂直领域热词日报 · {date}', '']
    for vk, vinfo in vt.VERTICALS.items():
        vitems = rec['verticals'].get(vk, [])
        if not vitems:
            continue
        lines.append(f'## {vinfo["label"]}')
        lines.append('')
        for item in vitems[:10]:
            score = f' [{item.get("score",0)}⭐]' if vk == 'github' else ''
            link = item.get('url', '')
            if link:
                lines.append(f'- {score} [{item["keyword"]}]({link})')
            else:
                lines.append(f'- {score} {item["keyword"]}')
            if item.get('desc'):
                lines.append(f'  - {item["desc"][:100]}')
        lines.append('')
    out.write_text('\n'.join(lines), encoding='utf-8')
    print(f'\n  💾 垂直日报已生成: data/reports/{date}_vertical.md')


def main():
    parser = argparse.ArgumentParser(description='每日热词自动抓取')
    parser.add_argument('--regions', default=None,
                        help='地区列表,逗号分隔,如 US,GB,JP (默认全部)')
    parser.add_argument('--force', action='store_true',
                        help='当天已抓过也重跑')
    parser.add_argument('--report', action='store_true',
                        help='只输出历史报告,不抓取')
    args = parser.parse_args()

    if args.report:
        report()
        return

    regions = args.regions.split(',') if args.regions else None
    run_once(regions=regions, force=args.force)


if __name__ == '__main__':
    main()