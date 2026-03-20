"""
App Trends Tracker - 크롤링 파이프라인 오케스트레이터
- Play Store, App Store, 뉴스 크롤링
- 항목 분류
- 중복 제거 및 병합
- feed.json 생성

GitHub Actions 및 로컬 실행 모두 호환
"""

import sys
import os
import json
from datetime import datetime
from pathlib import Path

# 크롤러 디렉토리를 Python path에 추가 (어디서 실행해도 import 가능)
CRAWLER_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(CRAWLER_DIR))

from config import APPS, DATA_POLICY
from play_store import crawl_play_store
from app_store import crawl_app_store
from news_crawler import crawl_news
from classifier import classify, deduplicate_items, generate_analysis


def format_date(date_str: str) -> str:
    """날짜를 index.html이 기대하는 YYYY.MM.DD 포맷으로 변환"""
    today = datetime.now()

    if not date_str:
        return today.strftime("%Y.%m.%d")
    if date_str == "오늘":
        return today.strftime("%Y.%m.%d")
    if date_str == "어제":
        from datetime import timedelta
        yesterday = today - timedelta(days=1)
        return yesterday.strftime("%Y.%m.%d")
    # MM.DD 형태 -> YYYY.MM.DD
    if len(date_str) <= 5 and '.' in date_str:
        return f"{today.year}.{date_str}"
    return date_str


def to_feature_item(item: dict) -> dict:
    """
    크롤러 출력을 index.html의 featureData 포맷으로 변환
    index.html 기대 포맷:
    { appId, title, desc, tags: [], date, analysis, src, ch }
    """
    return {
        "appId": item.get("app_id", ""),
        "title": item.get("title", ""),
        "desc": item.get("description", item.get("release_notes", "")),
        "tags": item.get("tags", []),
        "date": format_date(item.get("date", item.get("last_updated", ""))),
        "analysis": item.get("analysis", ""),
        "src": item.get("link", item.get("url", "")),
        "ch": item.get("channel", ""),
    }


def to_marketing_item(item: dict) -> dict:
    """
    크롤러 출력을 index.html의 mktData 포맷으로 변환
    index.html 기대 포맷:
    { appId, title, desc, type, tc, status, period, tags: [], analysis, src, ch }
    """
    return {
        "appId": item.get("app_id", ""),
        "title": item.get("title", ""),
        "desc": item.get("description", item.get("release_notes", "")),
        "type": item.get("type", "마케팅"),
        "tc": item.get("tc", "marketing"),
        "status": item.get("status", "live"),
        "period": item.get("period", ""),
        "tags": item.get("tags", []),
        "analysis": item.get("analysis", ""),
        "src": item.get("link", item.get("url", "")),
        "ch": item.get("channel", ""),
    }


def run_crawler_pipeline() -> None:
    """전체 크롤링 파이프라인 실행"""
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
    features = deduplicate_items(features)
    marketing = deduplicate_items(marketing)
    print(f"Features: {len(features)} 개")
    print(f"Marketing: {len(marketing)} 개")

    # index.html 호환 포맷으로 변환
    formatted_features = [to_feature_item(f) for f in features]
    formatted_marketing = [to_marketing_item(m) for m in marketing]

    # 결과가 없으면 기존 feed.json 유지 (빈 결과로 덮어쓰지 않음)
    repo_root = CRAWLER_DIR.parent
    feed_path = repo_root / "feed.json"

    if len(formatted_features) == 0 and len(formatted_marketing) == 0:
        print("\n⚠ 크롤링 결과가 없습니다. 기존 feed.json을 유지합니다.")
        print("파이프라인 완료 (변경 없음)")
        return

    # 기존 feed.json 로드 (있으면 병합)
    existing_features = []
    existing_marketing = []
    if feed_path.exists():
        try:
            with open(feed_path, 'r', encoding='utf-8') as f:
                existing = json.load(f)
                existing_features = existing.get("features", [])
                existing_marketing = existing.get("marketing", [])
        except (json.JSONDecodeError, KeyError):
            pass

    # 새 데이터 + 기존 데이터 병합 (새 데이터 우선, 제목 기준 중복 제거)
    def merge_items(new_items, existing_items):
        """새 항목을 기존 항목에 병합 (제목 기준 중복 제거)"""
        seen_titles = set()
        merged = []
        for item in new_items:
            title_key = item.get("title", "").strip().lower()
            if title_key and title_key not in seen_titles:
                seen_titles.add(title_key)
                merged.append(item)
        for item in existing_items:
            title_key = item.get("title", "").strip().lower()
            if title_key and title_key not in seen_titles:
                seen_titles.add(title_key)
                merged.append(item)
        return merged

    all_features = merge_items(formatted_features, existing_features)
    all_marketing = merge_items(formatted_marketing, existing_marketing)

    # 데이터 보관 정책 적용 (최대 아이템 수 제한)
    max_items = DATA_POLICY.get("max_items", 2000)
    all_features = all_features[:max_items]
    all_marketing = all_marketing[:max_items]

    # feed.json 생성
    feed = {
        "lastUpdated": datetime.now().isoformat(),
        "features": all_features,
        "marketing": all_marketing
    }

    # 레포 루트에 feed.json 저장
    with open(feed_path, 'w', encoding='utf-8') as f:
        json.dump(feed, f, ensure_ascii=False, indent=2)
    print(f"\n✓ 저장됨: {feed_path}")

    # 통계 출력
    print("\n" + "=" * 60)
    print("크롤링 완료 - 최종 통계")
    print("=" * 60)

    end_time = datetime.now()
    elapsed = (end_time - start_time).total_seconds()

    print(f"소요 시간: {elapsed:.1f}초")
    print(f"신규 Features: {len(formatted_features)}")
    print(f"신규 Marketing: {len(formatted_marketing)}")
    print(f"전체 Features: {len(all_features)}")
    print(f"전체 Marketing: {len(all_marketing)}")
    print(f"총 항목: {len(all_features) + len(all_marketing)}")
    print("\n파이프라인 완료!")


if __name__ == "__main__":
    run_crawler_pipeline()
