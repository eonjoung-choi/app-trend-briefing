"""
iTunes Lookup API 크롤러
- 한국 앱의 iOS 버전, 릴리스 노트, 업데이트 날짜 추출
- iTunes Lookup API 사용 (인증 불필요)
"""

import requests
import time
from datetime import datetime

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
}


def crawl_app_store(app_id: str, itunes_id: str) -> dict | None:
    """
    iTunes Lookup API를 통해 App Store 정보 추출

    Args:
        app_id: 앱 ID (config의 키)
        itunes_id: iTunes ID (예: "839333328")

    Returns:
        {
            "app_id": str,
            "version": str or None,
            "release_notes": str or None,
            "last_updated": str or None,
            "channel": "App Store 업데이트 노트",
            "url": str
        }
    """
    if not itunes_id:
        return None

    url = f"https://itunes.apple.com/lookup?id={itunes_id}&country=kr"

    try:
        print(f"  [App Store] {app_id}: iTunes ID {itunes_id}")
        response = requests.get(url, headers=HEADERS, timeout=10)
        response.raise_for_status()

        data = response.json()

        # iTunes API는 "results" 배열에 데이터 반환
        if data.get('resultCount', 0) == 0:
            print(f"    [Warning] {app_id}: No results from iTunes API")
            return None

        app_info = data['results'][0]

        version = app_info.get('version')
        release_notes = app_info.get('releaseNotes')
        release_date = app_info.get('currentVersionReleaseDate')

        # 날짜 포맷팅 (ISO 8601 -> MM.DD)
        last_updated = None
        if release_date:
            try:
                # "2026-03-18T10:30:00Z" 형태
                date_obj = datetime.fromisoformat(release_date.replace('Z', '+00:00'))
                last_updated = f"{date_obj.month:02d}.{date_obj.day:02d}"
            except:
                last_updated = release_date

        app_store_url = f"https://apps.apple.com/kr/app/id{itunes_id}"

        time.sleep(1.0)  # Rate limiting

        return {
            "app_id": app_id,
            "version": version,
            "release_notes": release_notes,
            "last_updated": last_updated,
            "channel": "App Store 업데이트 노트",
            "url": app_store_url
        }

    except requests.exceptions.RequestException as e:
        print(f"    [Error] {app_id}: {type(e).__name__}")
        return None
    except Exception as e:
        print(f"    [Error] {app_id}: {type(e).__name__} - {str(e)}")
        return None
