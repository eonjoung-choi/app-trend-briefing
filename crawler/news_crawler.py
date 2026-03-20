"""
뉴스 크롤러 (Google News RSS 기반)
- 앱 관련 뉴스 검색
- Google News RSS 사용 (서버 환경에서 안정적)
- 최근 1개월 뉴스만 수집
- &nbsp; 등 HTML 엔티티 정리
- 관련성 체크로 무관한 뉴스 필터링
"""

import requests
import time
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
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


def _is_recent(date_str, days=31):
    """날짜가 최근 N일 이내인지 확인"""
    try:
        dt = datetime.strptime(date_str, "%Y.%m.%d")
        cutoff = datetime.now() - timedelta(days=days)
        return dt >= cutoff
    except (ValueError, TypeError):
        return True


def _clean_html(text):
    """HTML 태그 및 엔티티 정리"""
    if not text:
        return ""
    clean = re.sub(r'<[^>]+>', '', text)
    # HTML 엔티티 변환
    clean = clean.replace('&nbsp;', ' ')
    clean = clean.replace('\u00a0', ' ')
    clean = clean.replace('\xa0', ' ')
    clean = clean.replace('&amp;', '&')
    clean = clean.replace('&lt;', '<')
    clean = clean.replace('&gt;', '>')
    clean = clean.replace('&#39;', "'")
    clean = clean.replace('&quot;', '"')
    # 연속 공백 정리
    clean = re.sub(r'\s+', ' ', clean)
    return clean.strip()


def _extract_source(title):
    """제목에서 소스명 분리 (Google News RSS 형식: '기사 제목 - 언론사')"""
    if ' - ' in title:
        parts = title.rsplit(' - ', 1)
        return parts[0].strip(), parts[1].strip()
    return title.strip(), ""


def _is_relevant(title, app_name, keywords):
    """기사 제목이 앱과 관련 있는지 확인"""
    title_lower = title.lower()
    # 앱 이름의 핵심 단어가 제목에 포함되어야 함
    name_parts = app_name.replace("은행", "").replace("카드", "").strip()
    if name_parts and name_parts.lower() in title_lower:
        return True
    # 키워드의 핵심 단어 체크
    for kw in keywords:
        # 키워드에서 일반적인 단어(업데이트, 신기능, 이벤트) 제거 후 핵심어 추출
        core = kw.replace("업데이트", "").replace("신기능", "").replace("이벤트", "").replace("앱", "").strip()
        if core and core.lower() in title_lower:
            return True
    return False


def _fetch_google_news(keyword, max_items=3):
    """Google News RSS에서 뉴스 검색 (최근 1개월)"""
    results = []
    # when:1m 으로 최근 1개월 뉴스만 검색
    search_query = f"{keyword} when:1m"
    encoded_keyword = quote(search_query)
    url = f"https://news.google.com/rss/search?q={encoded_keyword}&hl=ko&gl=KR&ceid=KR:ko"

    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()

        root = ET.fromstring(resp.content)
        channel = root.find('channel')
        if channel is None:
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

            title, source_name = _extract_source(raw_title)
            if not title or not link:
                continue

            date_str = _parse_rss_date(pub_date)

            # 최근 31일 이내 뉴스만 수집
            if not _is_recent(date_str, days=31):
                continue

            desc = _clean_html(description)
            if len(desc) > 200:
                desc = desc[:200] + "..."

            results.append({
                "title": title,
                "link": link,
                "date": date_str,
                "description": desc,
                "source": source_name,
                "channel": f"Google News ({source_name})" if source_name else "Google News",
            })
            count += 1

    except requests.exceptions.RequestException as e:
        print(f"    [Error] Google News RSS '{keyword}': {type(e).__name__}: {e}")
    except ET.ParseError as e:
        print(f"    [Error] XML parse '{keyword}': {e}")

    return results


def crawl_news(app_id: str, keywords: list) -> list:
    """
    앱별 뉴스 크롤링
    - app_id: 앱 식별자 (config.py의 키)
    - keywords: 뉴스 검색 키워드 리스트
    - returns: 뉴스 항목 리스트
    """
    all_results = []
    seen_titles = set()

    if not keywords:
        return all_results

    # config에서 앱 이름 가져오기
    from config import APPS
    app_config = APPS.get(app_id, {})
    app_name = app_config.get("name", "")

    print(f"  [News] {app_id} ({app_name}):")

    for keyword in keywords:
        print(f"    [Google News] '{keyword}'")
        try:
            items = _fetch_google_news(keyword, max_items=5)

            for item in items:
                title = item["title"]

                # 중복 체크
                if title in seen_titles:
                    continue

                # 관련성 체크: 제목에 앱 이름이나 핵심 키워드가 포함되어야 함
                if app_name and not _is_relevant(title, app_name, keywords):
                    print(f"      [Skip] 관련성 낮음: {title[:50]}...")
                    continue

                seen_titles.add(title)
                all_results.append({
                    "app_id": app_id,
                    "title": title,
                    "description": item.get("description", ""),
                    "link": item["link"],
                    "date": item["date"],
                    "channel": item.get("channel", "Google News"),
                    "keyword": keyword,
                })

            print(f"      -> {len(items)}건 검색, 관련 뉴스 필터 후 누적 {len(all_results)}건")

        except Exception as e:
            print(f"    [Error] {app_id} - '{keyword}': {type(e).__name__}: {e}")
            continue

        time.sleep(1)

    print(f"  [News] {app_id}: 총 {len(all_results)}건 수집\n")
    return all_results
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


def _fetch_google_news(keyword, max_items=3):
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

            title, source_name = _extract_source(raw_title)

            if not title or not link:
                continue

            date_str = _parse_rss_date(pub_date)
            desc = _clean_html(description)
            if len(desc) > 200:
                desc = desc[:200] + "..."

            results.append({
                "title": title,
                "link": link,
                "date": date_str,
                "description": desc,
                "channel": "Google News (" + source_name + ")",
            })
            count += 1

    except requests.exceptions.RequestException as e:
        print(f"    [Error] Google News RSS '{keyword}': {type(e).__name__}: {e}")
    except ET.ParseError as e:
        print(f"    [Error] XML parse '{keyword}': {e}")

    return results


def crawl_news(app_id: str, keywords: list) -> list:
    """
    앱별 뉴스 크롤링 메인 함수

    Parameters:
        app_id: 앱 식별자 (예: 'toss_bank')
        keywords: 검색 키워드 리스트 (예: ['토스 업데이트', '토스 신기능'])

    Returns:
        list: 크롤링된 뉴스 아이템 리스트
    """
    all_results = []
    seen_titles = set()

    if not keywords:
        return all_results

    print(f"  [Naver News] {app_id}:")

    for keyword in keywords:
        print(f"    [Google News] {app_id}: '{keyword}'")

        try:
            items = _fetch_google_news(keyword, max_items=3)

            for item in items:
                title = item["title"]
                if title in seen_titles:
                    continue
                seen_titles.add(title)

                all_results.append({
                    "app_id": app_id,
                    "title": title,
                    "description": item.get("description", ""),
                    "link": item["link"],
                    "date": item["date"],
                    "channel": item.get("channel", "Google News"),
                    "keyword": keyword,
                })

            print(f"      -> {len(items)}건 수집")

        except Exception as e:
            print(f"    [Error] {app_id} - '{keyword}': {type(e).__name__}: {e}")
            continue

        time.sleep(1)

    print(f"  [Naver News] {app_id}: 총 {len(all_results)}건 수집")
    print()

    return all_results
