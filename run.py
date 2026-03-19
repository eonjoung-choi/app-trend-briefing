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
    """
    전체 크롤링 파이프라인 실행
    """
    print("=" * 60)
    print("App Trends Tracker - 크롤링 파이프라인 시작")
    print("=" * 60)

    start_time = datetime.now()
    print(f"시작 시간: {start_time.isoformat()}\n")

    # 결과 저장소
    features = []
    marketing = []

    # 각 앱별로 크롤링 실행
    total_apps = len(APPS)
    for idx, (app_key, app_config) in enumerate(APPS.items(), 1):
        print(f"\n[{idx}/{total_apps}] {app_config['name']} ({app_key})")
        print("-" * 50)

        app_id = app_key

        # 1. Play Store 크롤링
        play_result = crawl_play_store(app_id, app_config.get('play_id'))
        if play_result and play_result.get('release_notes'):
            classified = classify(play_result)
            classified['analysis'] = generate_analysis(classified)
            if classified['category'] == 'feature':
                features.append(classified)
            else:
                marketing.append(classified)

        # 2. App Store 크롤링 (iOS 있는 앱만)
        if app_config.get('itunes_id'):
            app_result = crawl_app_store(app_id, app_config.get('itunes_id'))
            if app_result and app_result.get('release_notes'):
                classified = classify(app_result)
                classified['analysis'] = generate_analysis(classified)
                if classified['category'] == 'feature':
                    features.append(classified)
                else:
                    marketing.append(classified)

        # 3. 뉴스 크롤링
        news_results = crawl_news(
            app_id,
            app_config.get('news_keywords', [])
        )
        for news_item in news_results:
            classified = classify(news_item)
            classified['analysis'] = generate_analysis(classified)
            if classified['category'] == 'feature':
                features.append(classified)
            else:
                marketing.append(classified)

    # 중복 제거
    print("\n" + "=" * 60)
    print("중복 제거 중...")
    print("=" * 60)

    features = deduplicate_items(features)
    marketing = deduplicate_items(marketing)

    print(f"Features: {len(features)} 개")
    print(f"Marketing: {len(marketing)} 개")

    # feed.json 구성
    feed = {
        "lastUpdated": datetime.now().isoformat(),
        "features": features,
        "marketing": marketing
    }

    # 출력 경로: 레포 루트의 feed.json (GitHub Actions & Netlify 호환)
    repo_root = os.path.join(SCRIPT_DIR, '..')
    feed_path = os.path.abspath(os.path.join(repo_root, 'feed.json'))

    with open(feed_path, 'w', encoding='utf-8') as f:
        json.dump(feed, f, ensure_ascii=False, indent=2)
    print(f"\n✓ 저장됨: {feed_path}")

    # crawler/data에도 백업 저장
    backup_dir = os.path.join(SCRIPT_DIR, 'data')
    os.makedirs(backup_dir, exist_ok=True)
    with open(os.path.join(backup_dir, 'feed.json'), 'w', encoding='utf-8') as f:
        json.dump(feed, f, ensure_ascii=False, indent=2)

    # 통계 출력
    print("\n" + "=" * 60)
    print("크롤링 완료 - 최종 통계")
    print("=" * 60)

    end_time = datetime.now()
    elapsed = (end_time - start_time).total_seconds()

    print(f"소요 시간: {elapsed:.1f}초")
    print(f"Features (업데이트): {len(features)}")
    print(f"Marketing (마케팅): {len(marketing)}")
    print(f"총 항목: {len(features) + len(marketing)}")

    # 카테고리별 분류
    feature_by_app = {}
    marketing_by_app = {}

    for item in features:
        app_id = item.get('app_id')
        if app_id not in feature_by_app:
            feature_by_app[app_id] = 0
        feature_by_app[app_id] += 1

    for item in marketing:
        app_id = item.get('app_id')
        if app_id not in marketing_by_app:
            marketing_by_app[app_id] = 0
        marketing_by_app[app_id] += 1

    print("\n앱별 아이템 수:")
    for app_key in sorted(set(list(feature_by_app.keys()) + list(marketing_by_app.keys()))):
        features_count = feature_by_app.get(app_key, 0)
        marketing_count = marketing_by_app.get(app_key, 0)
        app_name = APPS.get(app_key, {}).get('name', app_key)
        print(f"  {app_name}: 업데이트 {features_count}, 마케팅 {marketing_count}")

    print("\n" + "=" * 60)
    print("파이프라인 완료!")
    print("=" * 60)


if __name__ == "__main__":
    run_crawler_pipeline()
