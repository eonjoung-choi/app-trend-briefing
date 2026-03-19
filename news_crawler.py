"""
네이버 뉴스 크롤러
- 앱 관련 뉴스 검색 (날짜 기준 정렬)
- Naver News Search 사용
"""

import requests
from bs4 import BeautifulSoup
import time
import re
from datetime import datetime

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
}


def crawl_news(app_id: str, keywords: list[str]) -> list[dict]:
    """
    네이버 뉴스에서 앱 관련 뉴스 검색

    Args:
        app_id: 앱 ID (config의 키)
        keywords: 검색 키워드 리스트 (예: ["토스 앱 업데이트", "토스뱅크 신기능"])

    Returns:
        [
            {
                "app_id": str,
                "title": str,
                "description": str,
                "link": str,
                "date": str (MM.DD 형태),
                "channel": "네이버 뉴스",
                "keyword": str (사용된 검색어)
            },
            ...
        ]
    """
    results = []
    seen_links = set()  # 중복 제거용

    for keyword in keywords:
        try:
            print(f"  [Naver News] {app_id}: '{keyword}'")

            # sort=1 은 최신순 정렬
            url = f"https://search.naver.com/search.naver?where=news&query={keyword}&sort=1"

            response = requests.get(url, headers=HEADERS, timeout=10)
            response.raise_for_status()

            soup = BeautifulSoup(response.content, 'html.parser')

            # 뉴스 검색 결과 파싱
            # Naver 뉴스는 특정 클래스로 구성됨
            news_items = soup.find_all('div', {'class': re.compile('.*news_item.*', re.IGNORECASE)})

            if not news_items:
                # 다른 구조 시도
                news_items = soup.find_all('li', {'class': re.compile('.*bx.*')})

            item_count = 0
            for item in news_items:
                if item_count >= 5:  # 키워드당 최대 5개
                    break

                try:
                    # 제목 추출
                    title_elem = item.find('a', {'class': re.compile('.*title.*')})
                    if not title_elem:
                        title_elem = item.find('a')

                    if not title_elem:
                        continue

                    title = title_elem.get_text(strip=True)
                    link = title_elem.get('href', '')

                    # 중복 체크
                    if link in seen_links or not link:
                        continue

                    seen_links.add(link)

                    # 설명 추출
                    desc_elem = item.find('div', {'class': re.compile('.*dsc.*')})
                    if not desc_elem:
                        desc_elem = item.find('span', {'class': re.compile('.*desc.*')})

                    description = ""
                    if desc_elem:
                        description = desc_elem.get_text(strip=True)[:200]

                    # 날짜 추출
                    date_elem = item.find('span', {'class': re.compile('.*date.*')})
                    date_str = ""
                    if date_elem:
                        date_text = date_elem.get_text(strip=True)
                        # "2시간 전", "어제", "3월 18일" 등의 형태
                        if '일' in date_text:
                            # "3월 18일" 형태
                            match = re.search(r'(\d{1,2})월\s*(\d{1,2})일', date_text)
                            if match:
                                month, day = match.groups()
                                date_str = f"{int(month):02d}.{int(day):02d}"
                        elif '어제' in date_text:
                            date_str = "어제"
                        elif '오늘' in date_text:
                            date_str = "오늘"
                        else:
                            date_str = date_text[:10]

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

                except Exception as e:
                    continue

            time.sleep(1.5)  # Rate limiting

        except requests.exceptions.RequestException as e:
            print(f"    [Error] {app_id} - '{keyword}': {type(e).__name__}")
            continue
        except Exception as e:
            print(f"    [Error] {app_id} - '{keyword}': {type(e).__name__}")
            continue

    return results
