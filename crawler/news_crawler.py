"""
뉴스 크롤러 (Google News RSS 기반)
- 앱 관련 뉴스 검색
- Google News RSS 사용 (서버 환경에서 안정적)
- 네이버 뉴스 대신 Google News RSS를 사용하여 클라우드 IP 차단 문제 해결
"""

import requests
import time
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from urllib.parse import quote


HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
    'Accept': 'application/xml, text/xml, */*',
    'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7',
}


def _parse_rss_date(date_str):
    """RSS 날짜 문자열을 YYYY.MM.DD 형식으로 변환"""
    if not date_str:
        return datetime.now().strftime("%Y.%m.%d")
    try:
        # Google News RSS 날짜 형식: "Thu, 20 Mar 2026 03:00:00 GMT"
        dt = datetime.strptime(date_str.strip(), "%a, %d %b %Y %H:%M:%S %Z")
        return dt.strftime("%Y.%m.%d")
    except (ValueError, TypeError):
        pass
    try:
        dt = datetime.strptime(date_str.strip()[:25], "%a, %d %b %Y %H:%M:%S")
        return dt.strftime("%Y.%m.%d")
    except (ValueError, TypeError):
        pass
    return datetime.now().strftime("%Y.%m.%d")


def _clean_html(text):
    """HTML 태그 제거"""
    if not text:
        return ""
    clean = re.sub(r'<[^>]+>', '', text)
    clean = clean.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
    clean = clean.replace('&#39;', "'").replace('&quot;', '"')
    return clean.strip()


def _extract_source(title):
    """Google News RSS 제목에서 언론사명 추출 (제목 끝에 ' - 언론사' 형식)"""
    if ' - ' in title:
        parts = title.rsplit(' - ', 1)
        return parts[0].strip(), parts[1].strip()
    return title.strip(), ""


def crawl_google_news(keyword, max_items=5):
    """Google News RSS로 뉴스 검색"""
    results = []
    encoded_keyword = quote(keyword)
    url = f"https://news.google.com/rss/search?q={encoded_keyword}&hl=ko&gl=KR&ceid=KR:ko"

    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()

        root = ET.fromstring(resp.content)
        channel = root.find('channel')
        if channel is None:
            print(f"    -> RSS 채널 없음")
            return results

        items = channel.findall('item')
        count = 0
        for item in items:
            if count >= max_items:
                break

            raw_title = item.findtext('title', '')
            link = item.findtext('link', '')
            pub_date = item.findtext('pubDate', '')
            description = item.findtext('description', '')

            # 제목에서 언론사 분리
            title, source_name = _extract_source(raw_title)

            if not title or not link:
                continue

            # 날짜 파싱
            date_str = _parse_rss_date(pub_date)

            # 설명 정리
            desc = _clean_html(description)
            if len(desc) > 200:
                desc = desc[:200] + "..."

            results.append({
                "title": title,
                "link": link,
                "date": date_str,
                "description": desc,
                "source_name": source_name,
            })
            count += 1

    except requests.exceptions.RequestException as e:
        print(f"    [Error] Google News RSS: {type(e).__name__}: {e}")
    except ET.ParseError as e:
        print(f"    [Error] XML 파싱 실패: {e}")

    return results


def crawl(apps: dict, data_policy: dict) -> list:
    """
    앱별 뉴스 크롤링 메인 함수

    Parameters:
        apps: config.py의 APPS 딕셔너리
        data_policy: config.py의 DATA_POLICY 딕셔너리

    Returns:
        list: 크롤링된 뉴스 아이템 리스트
    """
    all_results = []
    max_per_keyword = data_policy.get("news", {}).get("max_per_keyword", 3)

    for app_id, app_info in apps.items():
        app_name = app_info.get("name", app_id)
        keywords = app_info.get("keywords", [])

        if not keywords:
            continue

        print(f"  [Naver News] {app_id}:")
        news_titles = []
        item_count = 0

        for keyword in keywords:
            print(f"    [Google News] {app_id}: '{keyword}'")

            try:
                items = crawl_google_news(keyword, max_items=max_per_keyword)

                for item in items:
                    title = item["title"]

                    # 중복 체크
                    if title in news_titles:
                        continue
                    news_titles.append(title)

                    all_results.append({
                        "app_id": app_id,
                        "title": title,
                        "description": item.get("description", ""),
                        "link": item["link"],
                        "date": item["date"],
                        "channel": "Google News (" + item.get("source_name", "") + ")",
                        "keyword": keyword,
                    })
                    item_count += 1

                print(f"      -> {len(items)}건 수집")

            except Exception as e:
                print(f"    [Error] {app_id} - '{keyword}': {type(e).__name__}: {e}")
                continue

            time.sleep(1)

        print(f"  [Naver News] {app_id}: 총 {item_count}건 수집")
        print()

    return all_results

# Alias for backward compatibility with run.py
crawl_news = crawl
