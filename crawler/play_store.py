"""
Google Play Store 크롤러
- 한국 앱의 "What's New" (업데이트 노트) 추출
- requests + BeautifulSoup 사용
"""

import requests
from bs4 import BeautifulSoup
import time
import re
from datetime import datetime

# User-Agent 헤더
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
}


def crawl_play_store(app_id: str, play_id: str) -> dict | None:
    """
    Google Play Store에서 앱의 업데이트 노트 추출

    Args:
        app_id: 앱 ID (config의 키)
        play_id: Play Store 패키지 ID (예: "viva.republica.toss")

    Returns:
        {
            "app_id": str,
            "version": str or None,
            "release_notes": str or None,
            "last_updated": str or None,
            "channel": "Play Store 업데이트 노트",
            "url": str
        }
    """
    url = f"https://play.google.com/store/apps/details?id={play_id}&hl=ko"

    try:
        print(f"  [Play Store] {app_id}: {url}")
        response = requests.get(url, headers=HEADERS, timeout=10)
        response.raise_for_status()

        soup = BeautifulSoup(response.content, 'html.parser')

        # 버전 추출 (버전 번호는 보통 "버전 X.X.X" 형태)
        version = None
        version_elem = soup.find('span', {'class': re.compile('.*Version.*')})
        if version_elem:
            version_text = version_elem.get_text(strip=True)
            # "버전 X.X.X" 또는 "Version X.X.X" 형태
            match = re.search(r'[\d.]+', version_text)
            if match:
                version = match.group()

        # "What's New" (업데이트 노트) 추출
        release_notes = None
        whats_new_header = soup.find('h3', string=re.compile("새로운 기능|What's New", re.IGNORECASE))
        if whats_new_header:
            # 헤더 다음의 텍스트 찾기
            next_elem = whats_new_header.find_next()
            if next_elem:
                release_notes = next_elem.get_text(strip=True)

        # 헤더를 못 찾으면 다른 방식으로 시도
        if not release_notes:
            # 모든 텍스트에서 "새로운 기능" 이후의 내용 찾기
            text = soup.get_text()
            match = re.search(r'새로운 기능\s*([^\n]*(?:\n[^\n]*){0,10})', text)
            if match:
                release_notes = match.group(1).strip()[:500]  # 최대 500자

        # 마지막 업데이트 날짜 추출
        last_updated = None
        updated_elem = soup.find('span', string=re.compile('업데이트|Updated', re.IGNORECASE))
        if updated_elem:
            parent = updated_elem.find_parent()
            if parent:
                updated_text = parent.get_text(strip=True)
                # "YYYY년 MM월 DD일" 형태 추출
                match = re.search(r'(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일', updated_text)
                if match:
                    year, month, day = match.groups()
                    last_updated = f"{month}.{day}"

        time.sleep(1.5)  # Rate limiting

        return {
            "app_id": app_id,
            "version": version,
            "release_notes": release_notes,
            "last_updated": last_updated,
            "channel": "Play Store 업데이트 노트",
            "url": url
        }

    except requests.exceptions.RequestException as e:
        print(f"    [Error] {app_id}: {type(e).__name__}")
        return None
    except Exception as e:
        print(f"    [Error] {app_id}: {type(e).__name__} - {str(e)}")
        return None
