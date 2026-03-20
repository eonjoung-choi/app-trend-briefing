"""
네이버 뉴스 크롤러
- 앱 관련 뉴스 검색 (날짜 기준 정렬)
- Naver News Search 사용
- 2025-2026 네이버 뉴스 HTML 구조 대응
"""

import requests
from bs4 import BeautifulSoup
import time
import re
from datetime import datetime
from urllib.parse import quote_plus

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7',
    'Referer': 'https://www.naver.com/',
}


def is_valid_news_link(link: str) -> bool:
    """유효한 뉴스 기사 링크인지 확인"""
    if not link:
        return False
    if not link.startswith('http'):
        return False
    invalid_patterns = [
        'javascript:', '#', 'search.naver.com',
        'help.naver.com', 'policy.naver.com',
    ]
    for pattern in invalid_patterns:
        if pattern in link:
            return False
    return True


def is_valid_title(title: str) -> bool:
    """유효한 뉴스 제목인지 확인"""
    if not title or len(title) < 5:
        return False
    ui_elements = [
        '관련도순', '최신순', '전체', '옵션 초기화',
        '뉴스검색', '더보기', '이전', '다음',
        '언론사 선택', '직접입력', '날짜선택',
    ]
    for elem in ui_elements:
        if title.strip() == elem:
            return False
    return True


def _is_date_text(text: str) -> bool:
    """날짜 관련 텍스트인지 확인"""
    date_indicators = ['전', '분', '시간', '일', '어제', '오늘', '월', '초']
    return any(indicator in text for indicator in date_indicators)


def _parse_date(text: str) -> str:
    """날짜 텍스트를 MM.DD 형태로 변환"""
    text = text.strip()
    match = re.search(r'(\d{1,2})월\s*(\d{1,2})일', text)
    if match:
        month, day = match.groups()
        return f"{int(month):02d}.{int(day):02d}"
    match = re.search(r'(\d{4})\.(\d{2})\.(\d{2})', text)
    if match:
        _, month, day = match.groups()
        return f"{month}.{day}"
    if '어제' in text:
        return "어제"
    if '오늘' in text:
        return "오늘"
    if '전' in text:
        return "오늘"
    return text[:10] if text else ""


def crawl_news(app_id: str, keywords: list[str]) -> list[dict]:
    """
    네이버 뉴스에서 앱 관련 뉴스 검색

    Args:
        app_id: 앱 ID (config의 키)
        keywords: 검색 키워드 리스트

    Returns:
        [
            {
                "app_id": str,
                "title": str,
                "description": str,
                "link": str,
                "date": str,
                "channel": "네이버 뉴스",
                "keyword": str
            },
            ...
        ]
    """
    results = []
    seen_links = set()
    seen_titles = set()

    for keyword in keywords:
        try:
            print(f"  [Naver News] {app_id}: '{keyword}'")

            encoded_kw = quote_plus(keyword)
            url = f"https://search.naver.com/search.naver?where=news&query={encoded_kw}&sort=1&sm=tab_smr&nso=so:dd,p:1w,a:all"

            response = requests.get(url, headers=HEADERS, timeout=15)
            response.raise_for_status()

            soup = BeautifulSoup(response.text, 'html.parser')

            # 방법 1: a.news_tit (네이버 뉴스 제목 전용 클래스)
            news_titles = soup.select('a.news_tit')

            if news_titles:
                print(f"    -> a.news_tit 셀렉터로 {len(news_titles)}건 발견")
                item_count = 0
                for title_elem in news_titles:
                    if item_count >= 5:
                        break

                    title = title_elem.get_text(strip=True)
                    link = title_elem.get('href', '')

                    if not is_valid_title(title) or not is_valid_news_link(link):
                        continue

                    if link in seen_links or title.lower() in seen_titles:
                        continue

                    seen_links.add(link)
                    seen_titles.add(title.lower())

                    parent = title_elem.find_parent('div', class_='news_area')
                    if not parent:
                        parent = title_elem.find_parent('div')

                    description = ""
                    date_str = ""

                    if parent:
                        desc_elem = parent.select_one('a.api_txt_lines.dsc_txt_wrap')
                        if not desc_elem:
                            desc_elem = parent.select_one('div.news_dsc')
                        if desc_elem:
                            description = desc_elem.get_text(strip=True)[:200]

                        info_group = parent.select_one('div.info_group')
                        if info_group:
                            info_spans = info_group.select('span.info')
                            for info_span in info_spans:
                                text = info_span.get_text(strip=True)
                                if _is_date_text(text):
                                    date_str = _parse_date(text)
                                    break

                    results.append({
                        "app_id": app_id,
                        "title": title,
                        "description": description,
                        "link": link,
                        "date": date_str,
                        "channel": "네이버 뉴스",
                        "keyword": keyword
                    })
                    item_count += 1

            # 방법 2: 폴백 - div.news_wrap 기반
            if not news_titles:
                news_wraps = soup.select('div.news_wrap')
                if not news_wraps:
                    news_wraps = soup.select('ul.list_news > li')

                print(f"    -> 폴백 셀렉터로 {len(news_wraps)}건 발견")

                item_count = 0
                for item in news_wraps:
                    if item_count >= 5:
                        break

                    try:
                        all_links = item.find_all('a', href=True)
                        title_elem = None
                        for a in all_links:
                            href = a.get('href', '')
                            text = a.get_text(strip=True)
                            if is_valid_news_link(href) and is_valid_title(text):
                                title_elem = a
                                break

                        if not title_elem:
                            continue

                        title = title_elem.get_text(strip=True)
                        link = title_elem.get('href', '')

                        if link in seen_links or title.lower() in seen_titles:
                            continue

                        seen_links.add(link)
                        seen_titles.add(title.lower())

                        desc_elem = item.find('div', class_=re.compile('.*dsc.*'))
                        description = ""
                        if desc_elem:
                            description = desc_elem.get_text(strip=True)[:200]

                        date_str = ""
                        date_elem = item.find('span', class_=re.compile('.*(info|date|time).*'))
                        if date_elem:
                            date_str = _parse_date(date_elem.get_text(strip=True))

                        results.append({
                            "app_id": app_id,
                            "title": title,
                            "description": description,
                            "link": link,
                            "date": date_str,
                            "channel": "네이버 뉴스",
                            "keyword": keyword
                        })
                        item_count += 1

                    except Exception:
                        continue

            if not news_titles:
                print(f"    -> 뉴스 결과 없음")

            time.sleep(1.5)

        except requests.exceptions.RequestException as e:
            print(f"  [Error] {app_id} - '{keyword}': {type(e).__name__}")
            continue
        except Exception as e:
            print(f"  [Error] {app_id} - '{keyword}': {type(e).__name__}: {e}")
            continue

    print(f"  [Naver News] {app_id}: 총 {len(results)}건 수집")
    return results
