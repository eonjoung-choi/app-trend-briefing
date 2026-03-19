"""
App Trends Tracker - 크롤링 파이프라인 오케스트레이터
- Play Store, App Store, 뉴스 크롤링
- 항목 분류
- 중복 제거 및 병합
- feed.json 생성
"""

import sys
import os
import json

from datetime import datetime
from typing import Optional

# GitHub Actions에서는 레포 루트 기준, 로컬에서도 동작하도록 경로 설정
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

# 크롤러 및 분류기 임포트
from config import APPS, DATA_POLICY
from play_store import crawl_play_store
from app_store import crawl_app_store
from news_crawler import crawl_news
from classifier import classify, deduplicate_items, generate_analysis


def run_crawler_pipeline() -> None:
    print("=" * 60)
    print("App Trends Tracker - 크롤링 파이프라인 시작")
    print("=" * 60)

    start_time = datetime.now()
    print(f"시작 시간: {start_time.isoformat()}\n")

    features = []
    marketing = []

    total_apps = len(APPS)
    for idx, (app_key, app_config) in enumerate(APPS.items(), 1):
        print(f"\n[{idx}/{total_apps}] {app_config['name']} ({app_key})")
        print("-" * 50)

        app_id = app_key

        play_result = crawl_play_store(app_id, app_config.get('play_id'))
        if play_result and play_result.get('release_notes'):
            classified = classify(play_result)
            classified['analysis'] = generate_analysis(classified)
            if classified['category'] == 'feature':
                features.append(classified)
            else:
                marketing.append(classified)

        if app_config.get('itunes_id'):
            app_result = crawl_app_store(app_id, app_config.get('itunes_id'))
            if app_result and app_result.get('release_notes'):
                classified = classify(app_result)
                classified['analysis'] = generate_analysis(classified)
                if classified['category'] == 'feature':
                    features.append(classified)
                else:
                    marketing.append(classified)

        news_results = crawl_news(app_id, app_config.get('news_keywords', []))
        for news_item in news_results:
            classified = classify(news_item)
            classified['analysis'] = generate_analysis(classified)
            if classified['category'] == 'feature':
                features.append(classified)
            else:
                marketing.append(classified)

    # 중복 제거 및 정크 필터링
    print("\n" + "=" * 60)
    print("중복 제거 및 정크 필터링 중...")
    print("=" * 60)

    features = deduplicate_items(features)
    marketing = deduplicate_items(marketing)

    JUNK_TITLES = {'관련도순', '최신순', '전체', '옵션 초기화', '뉴스홈', '연예',
                   '스포츠', '경제', '정치', '사회', 'IT', '세계', '', None}

    def is_valid(item):
        title = (item.get('title') or '').strip()
        if not title or title in JUNK_TITLES or len(title) < 5:
            return False
        if (item.get('link') or '').startswith('javascript:'):
            return False
        if (item.get('url') or '') == '#':
            return False
        return True

    features = [i for i in features if is_valid(i)]
    marketing = [i for i in marketing if is_valid(i)]

    # 필드명 변환: 크롤러 출력 -> HTML 스키마
    def to_html_schema(item, category):
        app_id = item.get('app_id', '')
        title = item.get('title') or (item.get('release_notes', '') or '')[:80]
        desc = item.get('description') or item.get('release_notes', '') or ''
        date_str = item.get('date') or item.get('last_updated') or ''
        src = item.get('url') or item.get('link') or ''
        ch = item.get('channel', '')
        tags = item.get('tags', [])
        analysis = item.get('analysis', '')

        result = {
            'appId': app_id,
            'title': title.strip(),
            'desc': desc.strip()[:200],
            'tags': tags if tags else [],
            'date': date_str,
            'analysis': analysis,
            'src': src,
            'ch': ch,
        }

        if category == 'marketing':
            result['type'] = item.get('type', '마케팅')
            result['tc'] = item.get('tc', 'event')
            result['status'] = item.get('status', 'live')
            result['period'] = item.get('period', '')

        return result

    features = [to_html_schema(i, 'feature') for i in features]
    marketing = [to_html_schema(i, 'marketing') for i in marketing]

    print(f"Features: {len(features)} 개")
    print(f"Marketing: {len(marketing)} 개")

    feed = {
        "lastUpdated": datetime.now().isoformat(),
        "features": features,
        "marketing": marketing
    }

    repo_root = os.path.join(SCRIPT_DIR, '..')
    feed_path = os.path.abspath(os.path.join(repo_root, 'feed.json'))

    with open(feed_path, 'w', encoding='utf-8') as f:
        json.dump(feed, f, ensure_ascii=False, indent=2)
    print(f"\n✓ 저장됨: {feed_path}")

    backup_dir = os.path.join(SCRIPT_DIR, 'data')
    os.makedirs(backup_dir, exist_ok=True)
    with open(os.path.join(backup_dir, 'feed.json'), 'w', encoding='utf-8') as f:
        json.dump(feed, f, ensure_ascii=False, indent=2)

    end_time = datetime.now()
    elapsed = (end_time - start_time).total_seconds()
    print(f"\n소요 시간: {elapsed:.1f}초")
    print(f"총 항목: {len(features) + len(marketing)}")
    print("\n파이프라인 완료!")


if __name__ == "__main__":
    run_crawler_pipeline()
