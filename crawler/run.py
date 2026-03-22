"""
App Trends Tracker - 크롤링 파이프라인 오케스트레이터
- Play Store, App Store, 뉴스 크롤링
- 항목 분류
- 중복 제거 및 병합
- feed.json 생성 (최신순 정렬, 현재 월 필터링)
- 노이즈 필터링 (퀴즈, 주가, 채용 등 무관 콘텐츠 제거)
- 동일 토픽 중복 제거 (같은 사건의 뉴스는 대표 1건만 유지)
- desc 출처명 정리

GitHub Actions 및 로컬 실행 모두 호환
"""

import sys
import os
import json
import re
from datetime import datetime, timedelta
from pathlib import Path

# 크롤러 디렉토리를 Python path에 추가 (어디서 실행해도 import 가능)
CRAWLER_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(CRAWLER_DIR))

from config import APPS, DATA_POLICY
from play_store import crawl_play_store
from app_store import crawl_app_store
from news_crawler import crawl_news
from classifier import classify, deduplicate_items, generate_analysis

# ── 노이즈 필터 (news_crawler.py와 동일 키워드, 병합 후에도 적용) ──
NOISE_KEYWORDS = [
    "퀴즈 정답", "행운퀴즈", "이벤트 정답", "오늘의 퀴즈",
    "파트너사 모집", "채용", "인턴", "공채", "합격",
    "주가", "시세", "주식 전망", "투자 의견", "목표가",
    "실적 발표", "영업이익", "순이익", "매출액",
    "대출 금리", "예금 금리", "적금 금리",
    "로봇", "웰니스", "AI 서밋", "AI 전략 공개",
    "ESG", "탄소", "사회공헌", "봉사",
    "일키", "최강 블루", "게임", "e스포츠",
    "갤럭시", "아이폰", "영상갤",
]


def is_noise_title(title: str) -> bool:
    """노이즈 제목 필터 (퀴즈, 주가, 채용 등 앱 서비스/UX와 무관한 콘텐츠)"""
    title_lower = title.lower()
    for noise in NOISE_KEYWORDS:
        if noise.lower() in title_lower:
            return True
    return False


def clean_desc_source(desc: str) -> str:
    """desc에서 출처명 잔여 텍스트 제거"""
    if not desc:
        return ""
    desc = re.sub(r'\s+[a-zA-Z0-9.-]+\.(co\.kr|com|net|kr|org)(\s|$)', ' ', desc)
    desc = re.sub(r'\s+[가-힣]{2,6}$', '', desc.strip())
    return desc.strip()


# ── 동일 토픽 중복 제거 (병합된 전체 데이터에 적용) ──

def _extract_topic_words(title: str) -> set:
    """제목에서 토픽 비교용 핵심 단어 집합 추출"""
    clean = re.sub(r"['\"\'\u2018\u2019\u201c\u201d\u2026\u00b7\-\?!,\.\(\)\[\]]", " ", title)
    words = set()
    for w in clean.split():
        w = w.strip()
        if len(w) >= 2:
            words.add(w)
    return words


def _is_same_topic(title_a: str, title_b: str) -> bool:
    """두 제목이 동일 토픽인지 판단 (핵심 단어 50% 이상 겹침)"""
    words_a = _extract_topic_words(title_a)
    words_b = _extract_topic_words(title_b)
    if not words_a or not words_b:
        return False
    overlap = len(words_a & words_b)
    shorter = min(len(words_a), len(words_b))
    if shorter == 0:
        return False
    return overlap / shorter >= 0.5


def dedup_by_topic(items: list) -> list:
    """앱별로 동일 토픽 뉴스를 그룹핑하고 대표 1건만 남김"""
    app_groups = {}
    for item in items:
        app_id = item.get("appId", "")
        if app_id not in app_groups:
            app_groups[app_id] = []
        app_groups[app_id].append(item)

    result = []
    for app_id, group in app_groups.items():
        topic_groups = []
        for item in group:
            title = item.get("title", "")
            placed = False
            for tg in topic_groups:
                if _is_same_topic(title, tg[0].get("title", "")):
                    tg.append(item)
                    placed = True
                    break
            if not placed:
                topic_groups.append([item])
        for tg in topic_groups:
            # 대표 1건 선택: desc 있는 것 우선, 최신 날짜 우선
            best = tg[0]
            for item in tg[1:]:
                if item.get("desc", "") and not best.get("desc", ""):
                    best = item
                elif item.get("date", "") > best.get("date", ""):
                    best = item
            result.append(best)
    return result


def format_date(date_str: str) -> str:
    """날짜를 index.html이 기대하는 YYYY.MM.DD 포맷으로 변환"""
    today = datetime.now()
    if not date_str:
        return today.strftime("%Y.%m.%d")
    if date_str == "오늘":
        return today.strftime("%Y.%m.%d")
    if date_str == "어제":
        yesterday = today - timedelta(days=1)
        return yesterday.strftime("%Y.%m.%d")
    if len(date_str) == 10 and date_str[4] == '.' and date_str[7] == '.':
        return date_str
    for fmt in ["%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%b %d, %Y", "%d %b %Y"]:
        try:
            dt = datetime.strptime(date_str[:len(fmt.replace('%', 'X').replace('X', 'XX'))], fmt)
            return dt.strftime("%Y.%m.%d")
        except (ValueError, TypeError):
            continue
    return date_str if date_str else today.strftime("%Y.%m.%d")


def to_feature_item(item: dict) -> dict:
    desc = item.get("description", item.get("release_notes", ""))
    desc = desc.replace("&nbsp;", " ").replace("\u00a0", " ").strip()
    desc = " ".join(desc.split())
    return {
        "appId": item.get("app_id", ""),
        "title": item.get("title", ""),
        "desc": desc,
        "tags": item.get("tags", []),
        "date": format_date(item.get("date", item.get("last_updated", ""))),
        "analysis": item.get("analysis", ""),
        "src": item.get("link", item.get("url", "")),
        "ch": item.get("channel", ""),
    }


def to_marketing_item(item: dict) -> dict:
    desc = item.get("description", item.get("release_notes", ""))
    desc = desc.replace("&nbsp;", " ").replace("\u00a0", " ").strip()
    desc = " ".join(desc.split())
    return {
        "appId": item.get("app_id", ""),
        "title": item.get("title", ""),
        "desc": desc,
        "type": item.get("type", "마케팅"),
        "tc": item.get("target_customer", "전체"),
        "status": item.get("status", "진행중"),
        "period": item.get("period", ""),
        "tags": item.get("tags", []),
        "analysis": item.get("analysis", ""),
        "src": item.get("link", item.get("url", "")),
        "ch": item.get("channel", ""),
    }


def is_valid_item(item: dict) -> bool:
    title = item.get("title", "").strip()
    src = item.get("src", "").strip()
    if not title:
        return False
    junk_titles = ["관련도순", "전체", "옵션 초기화", "최신순", "정확도순"]
    if title in junk_titles:
        return False
    if src in ["#", "javascript:;", "javascript:void(0)", ""]:
        return False
    return True


def is_current_month(item: dict) -> bool:
    date_str = item.get("date", "")
    if not date_str:
        return False
    now = datetime.now()
    current_prefix = now.strftime("%Y.%m")
    return date_str.startswith(current_prefix)


def sort_by_date_desc(items: list) -> list:
    def date_key(item):
        d = item.get("date", "0000.00.00")
        return d
    return sorted(items, key=date_key, reverse=True)


def run_crawler_pipeline():
    print("=" * 60)
    print("App Trends Tracker - 크롤링 시작")
    print(f"시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    start_time = datetime.now()
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

    print("\n" + "=" * 60)
    print("중복 제거 중...")
    features = deduplicate_items(features)
    marketing = deduplicate_items(marketing)
    print(f"Features: {len(features)} 개")
    print(f"Marketing: {len(marketing)} 개")

    formatted_features = [to_feature_item(f) for f in features]
    formatted_marketing = [to_marketing_item(m) for m in marketing]

    repo_root = CRAWLER_DIR.parent
    feed_path = repo_root / "feed.json"

    if len(formatted_features) == 0 and len(formatted_marketing) == 0:
        print("\n⚠ 크롤링 결과가 없습니다. 기존 feed.json을 유지합니다.")
        print("파이프라인 완료 (변경 없음)")
        return

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

    def merge_items(new_items, existing_items):
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

    # 1. 쓰레기 데이터 필터링
    all_features = [item for item in all_features if is_valid_item(item)]
    all_marketing = [item for item in all_marketing if is_valid_item(item)]
    print(f"\n쓰레기 데이터 필터 후 - Features: {len(all_features)}, Marketing: {len(all_marketing)}")

    # 2. 노이즈 필터링
    before_f = len(all_features)
    before_m = len(all_marketing)
    all_features = [item for item in all_features if not is_noise_title(item.get("title", ""))]
    all_marketing = [item for item in all_marketing if not is_noise_title(item.get("title", ""))]
    noise_removed = (before_f - len(all_features)) + (before_m - len(all_marketing))
    print(f"노이즈 필터 후 - Features: {len(all_features)}, Marketing: {len(all_marketing)} (제거: {noise_removed}건)")

    # 3. desc 출처명 정리
    for item in all_features:
        item["desc"] = clean_desc_source(item.get("desc", ""))
    for item in all_marketing:
        item["desc"] = clean_desc_source(item.get("desc", ""))

    # 4. 현재 월 데이터만 유지
    all_features = [item for item in all_features if is_current_month(item)]
    all_marketing = [item for item in all_marketing if is_current_month(item)]
    print(f"현재 월 필터 후 - Features: {len(all_features)}, Marketing: {len(all_marketing)}")

    # 5. ★ 동일 토픽 중복 제거 (같은 사건의 뉴스는 앱별 대표 1건만 유지)
    before_topic_f = len(all_features)
    before_topic_m = len(all_marketing)
    all_features = dedup_by_topic(all_features)
    all_marketing = dedup_by_topic(all_marketing)
    topic_removed = (before_topic_f - len(all_features)) + (before_topic_m - len(all_marketing))
    print(f"토픽 중복 제거 후 - Features: {len(all_features)}, Marketing: {len(all_marketing)} (제거: {topic_removed}건)")

    # 6. 최신순 정렬
    all_features = sort_by_date_desc(all_features)
    all_marketing = sort_by_date_desc(all_marketing)

    max_items = DATA_POLICY.get("max_items", 2000)
    all_features = all_features[:max_items]
    all_marketing = all_marketing[:max_items]

    feed = {
        "lastUpdated": datetime.now().isoformat(),
        "features": all_features,
        "marketing": all_marketing
    }

    with open(feed_path, 'w', encoding='utf-8') as f:
        json.dump(feed, f, ensure_ascii=False, indent=2)

    print(f"\n✓ 저장됨: {feed_path}")

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
    print(f"노이즈 제거: {noise_removed}건")
    print(f"토픽 중복 제거: {topic_removed}건")
    print("\n파이프라인 완료!")


if __name__ == "__main__":
    run_crawler_pipeline()
"""
App Trends Tracker - 크롤링 파이프라인 오케스트레이터
- Play Store, App Store, 뉴스 크롤링
- 항목 분류
- 중복 제거 및 병합
- feed.json 생성 (최신순 정렬, 현재 월 필터링)
- 노이즈 필터링 (퀴즈, 주가, 채용 등 무관 콘텐츠 제거)
- desc 출처명 정리

GitHub Actions 및 로컬 실행 모두 호환
"""

import sys
import os
import json
import re
from datetime import datetime, timedelta
from pathlib import Path

# 크롤러 디렉토리를 Python path에 추가 (어디서 실행해도 import 가능)
CRAWLER_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(CRAWLER_DIR))

from config import APPS, DATA_POLICY
from play_store import crawl_play_store
from app_store import crawl_app_store
from news_crawler import crawl_news
from classifier import classify, deduplicate_items, generate_analysis

# ── 노이즈 필터 (news_crawler.py와 동일 키워드, 병합 후에도 적용) ──
NOISE_KEYWORDS = [
    "퀴즈 정답", "행운퀴즈", "이벤트 정답", "오늘의 퀴즈",
    "파트너사 모집", "채용", "인턴", "공채", "합격",
    "주가", "시세", "주식 전망", "투자 의견", "목표가",
    "실적 발표", "영업이익", "순이익", "매출액",
    "대출 금리", "예금 금리", "적금 금리",
    "로봇", "웰니스", "AI 서밋", "AI 전략 공개",
    "ESG", "탄소", "사회공헌", "봉사",
    "일키", "최강 블루", "게임", "e스포츠",
]


def is_noise_title(title: str) -> bool:
    """노이즈 제목 필터 (퀴즈, 주가, 채용 등 앱 서비스/UX와 무관한 콘텐츠)"""
    title_lower = title.lower()
    for noise in NOISE_KEYWORDS:
        if noise.lower() in title_lower:
            return True
    return False


def clean_desc_source(desc: str) -> str:
    """desc에서 출처명 잔여 텍스트 제거 (예: 'bntnews.co.kr', '게임톡' 등)"""
    if not desc:
        return ""
    # URL 패턴 제거 (xxx.co.kr, xxx.com 등)
    desc = re.sub(r'\s+[a-zA-Z0-9.-]+\.(co\.kr|com|net|kr|org)(\s|$)', ' ', desc)
    # 마지막에 붙은 짧은 출처명 제거 (2~6자 한글로 끝나는 패턴)
    desc = re.sub(r'\s+[가-힣]{2,6}$', '', desc.strip())
    return desc.strip()


def format_date(date_str: str) -> str:
    """날짜를 index.html이 기대하는 YYYY.MM.DD 포맷으로 변환"""
    today = datetime.now()
    if not date_str:
        return today.strftime("%Y.%m.%d")
    if date_str == "오늘":
        return today.strftime("%Y.%m.%d")
    if date_str == "어제":
        yesterday = today - timedelta(days=1)
        return yesterday.strftime("%Y.%m.%d")
    # 이미 올바른 형식이면 그대로
    if len(date_str) == 10 and date_str[4] == '.' and date_str[7] == '.':
        return date_str
    # ISO 형식 처리
    for fmt in ["%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%b %d, %Y", "%d %b %Y"]:
        try:
            dt = datetime.strptime(date_str[:len(fmt.replace('%', 'X').replace('X', 'XX'))], fmt)
            return dt.strftime("%Y.%m.%d")
        except (ValueError, TypeError):
            continue
    # 변환 실패 시 원본 반환
    return date_str if date_str else today.strftime("%Y.%m.%d")


def to_feature_item(item: dict) -> dict:
    """
    크롤러 출력을 index.html의 featureData 포맷으로 변환
    index.html 기대 포맷: { appId, title, desc, tags: [], date, analysis, src, ch }
    """
    desc = item.get("description", item.get("release_notes", ""))
    # &nbsp; 잔여 정리
    desc = desc.replace("&nbsp;", " ").replace("\u00a0", " ").strip()
    desc = " ".join(desc.split())  # 연속 공백 정리
    return {
        "appId": item.get("app_id", ""),
        "title": item.get("title", ""),
        "desc": desc,
        "tags": item.get("tags", []),
        "date": format_date(item.get("date", item.get("last_updated", ""))),
        "analysis": item.get("analysis", ""),
        "src": item.get("link", item.get("url", "")),
        "ch": item.get("channel", ""),
    }


def to_marketing_item(item: dict) -> dict:
    """
    크롤러 출력을 index.html의 mktData 포맷으로 변환
    index.html 기대 포맷: { appId, title, desc, type, tc, status, period, tags: [], analysis, src, ch }
    """
    desc = item.get("description", item.get("release_notes", ""))
    desc = desc.replace("&nbsp;", " ").replace("\u00a0", " ").strip()
    desc = " ".join(desc.split())
    return {
        "appId": item.get("app_id", ""),
        "title": item.get("title", ""),
        "desc": desc,
        "type": item.get("type", "마케팅"),
        "tc": item.get("target_customer", "전체"),
        "status": item.get("status", "진행중"),
        "period": item.get("period", ""),
        "tags": item.get("tags", []),
        "analysis": item.get("analysis", ""),
        "src": item.get("link", item.get("url", "")),
        "ch": item.get("channel", ""),
    }


def is_valid_item(item: dict) -> bool:
    """유효한 항목인지 확인 (쓰레기 데이터 필터링)"""
    title = item.get("title", "").strip()
    src = item.get("src", "").strip()

    # 빈 제목 필터
    if not title:
        return False

    # 쓰레기 데이터 패턴 필터
    junk_titles = ["관련도순", "전체", "옵션 초기화", "최신순", "정확도순"]
    if title in junk_titles:
        return False

    # 무효한 링크 필터
    if src in ["#", "javascript:;", "javascript:void(0)", ""]:
        return False

    return True


def is_current_month(item: dict) -> bool:
    """현재 월의 항목인지 확인"""
    date_str = item.get("date", "")
    if not date_str:
        return False
    now = datetime.now()
    current_prefix = now.strftime("%Y.%m")
    return date_str.startswith(current_prefix)


def sort_by_date_desc(items: list) -> list:
    """날짜 기준 최신순 정렬"""
    def date_key(item):
        d = item.get("date", "0000.00.00")
        return d
    return sorted(items, key=date_key, reverse=True)


def run_crawler_pipeline():
    """메인 크롤링 파이프라인"""
    print("=" * 60)
    print("App Trends Tracker - 크롤링 시작")
    print(f"시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    start_time = datetime.now()
    features = []
    marketing = []

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
        news_results = crawl_news(app_id, app_config.get('news_keywords', []))
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

    # 1. 쓰레기 데이터 필터링 (무효 항목 제거)
    all_features = [item for item in all_features if is_valid_item(item)]
    all_marketing = [item for item in all_marketing if is_valid_item(item)]
    print(f"\n쓰레기 데이터 필터 후 - Features: {len(all_features)}, Marketing: {len(all_marketing)}")

    # 2. 노이즈 필터링 (퀴즈, 주가, 채용 등 앱 서비스/UX와 무관한 콘텐츠 제거)
    before_f = len(all_features)
    before_m = len(all_marketing)
    all_features = [item for item in all_features if not is_noise_title(item.get("title", ""))]
    all_marketing = [item for item in all_marketing if not is_noise_title(item.get("title", ""))]
    noise_removed = (before_f - len(all_features)) + (before_m - len(all_marketing))
    print(f"노이즈 필터 후 - Features: {len(all_features)}, Marketing: {len(all_marketing)} (제거: {noise_removed}건)")

    # 3. desc 출처명 정리
    for item in all_features:
        item["desc"] = clean_desc_source(item.get("desc", ""))
    for item in all_marketing:
        item["desc"] = clean_desc_source(item.get("desc", ""))

    # 4. 현재 월 데이터만 유지
    all_features = [item for item in all_features if is_current_month(item)]
    all_marketing = [item for item in all_marketing if is_current_month(item)]
    print(f"현재 월 필터 후 - Features: {len(all_features)}, Marketing: {len(all_marketing)}")

    # 5. 최신순 정렬
    all_features = sort_by_date_desc(all_features)
    all_marketing = sort_by_date_desc(all_marketing)

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
    print(f"노이즈 제거: {noise_removed}건")
    print("\n파이프라인 완료!")


if __name__ == "__main__":
    run_crawler_pipeline()
