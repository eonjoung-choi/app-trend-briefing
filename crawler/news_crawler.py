"""
뉴스 크롤러 (Google News RSS 기반)
- 앱 서비스 업데이트/UX/기능 관련 뉴스만 큐레이션
- Google News RSS 사용 (서버 환경에서 안정적)
- 최근 1개월 뉴스만 수집
- 관련성 필터 강화: 앱 서비스/UX와 무관한 뉴스 차단
- desc: 제목 반복 대신 요약 텍스트, 출처명 제거
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

# 앱 서비스/UX와 무관한 노이즈 키워드 (제목에 포함 시 제외)
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

# 앱 서비스/UX 관련 긍정 키워드 (제목에 포함 시 우선 수집)
POSITIVE_KEYWORDS = [
    "업데이트", "신기능", "출시", "개편", "리뉴얼", "UI", "UX",
    "사용자 경험", "인터페이스", "디자인", "홈 화면", "탭", "메뉴",
    "간편결제", "간편송금", "생체인증", "얼굴인식", "지문",
    "알림", "푸시", "위젯", "다크모드", "접근성",
    "오픈뱅킹", "마이데이터", "슈퍼앱", "미니앱",
    "서비스 개선", "기능 추가", "기능 개선", "버전",
    "앱 서비스", "모바일 앱", "모바일 서비스",
    "뱅킹 앱", "쇼핑 앱", "결제 앱", "배달 앱",
    "챗봇", "AI 상담", "고객센터", "CS",
]


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
    clean = clean.replace('&nbsp;', ' ').replace('\u00a0', ' ').replace('\xa0', ' ')
    clean = clean.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
    clean = clean.replace('&#39;', "'").replace('&quot;', '"')
    clean = re.sub(r'\s+', ' ', clean)
    return clean.strip()


def _extract_source(title):
    """제목에서 소스명 분리 (Google News RSS: '기사 제목 - 언론사')"""
    if ' - ' in title:
        parts = title.rsplit(' - ', 1)
        return parts[0].strip(), parts[1].strip()
    return title.strip(), ""


def _remove_source_from_desc(desc, source_name):
    """설명 텍스트에서 출처명 제거"""
    if not desc or not source_name:
        return desc
    # 끝부분의 출처명 제거 (공백 포함)
    patterns = [
        f"  {source_name}",
        f" {source_name}",
        source_name,
    ]
    for pat in patterns:
        if desc.endswith(pat):
            desc = desc[:-len(pat)].strip()
            break
    return desc


def _make_summary(title, desc, source_name):
    """제목과 다른 요약 텍스트 생성 (출처 제거, 중복 방지)"""
    # 출처명 제거
    clean_desc = _remove_source_from_desc(desc, source_name)

    # desc가 title과 거의 동일하면 빈 문자열 반환
    if not clean_desc:
        return ""
    # 제목과 80% 이상 유사하면 중복으로 판단
    title_clean = title.lower().replace(" ", "")
    desc_clean = clean_desc.lower().replace(" ", "")
    if title_clean and desc_clean:
        shorter = min(len(title_clean), len(desc_clean))
        if shorter > 0:
            overlap = sum(1 for a, b in zip(title_clean, desc_clean) if a == b)
            if overlap / shorter > 0.7:
                return ""

    # 200자 이내로 잘라서 반환
    if len(clean_desc) > 200:
        clean_desc = clean_desc[:200] + "..."
    return clean_desc


def _has_noise(title):
    """제목에 노이즈 키워드가 포함되어 있는지 확인"""
    title_lower = title.lower()
    for noise in NOISE_KEYWORDS:
        if noise.lower() in title_lower:
            return True
    return False


def _has_positive_signal(title):
    """제목에 앱 서비스/UX 관련 긍정 키워드가 있는지 확인"""
    title_lower = title.lower()
    for pos in POSITIVE_KEYWORDS:
        if pos.lower() in title_lower:
            return True
    return False


def _is_relevant(title, app_name, keywords):
    """
    기사가 앱 서비스/UX 관점에서 관련 있는지 엄격히 판단
    1) 노이즈 키워드 포함 -> 제외
    2) 앱 이름/핵심어가 제목에 있어야 함
    3) 서비스/UX 관련 긍정 신호가 있으면 우선 통과
    """
    # 1. 노이즈 필터
    if _has_noise(title):
        return False

    title_lower = title.lower()

    # 2. 앱 이름 또는 핵심어가 제목에 포함되어야 함
    name_found = False
    # 앱 이름의 핵심 부분 추출
    core_names = []
    # "은행", "카드", "증권", "보험" 등 접미사 제거
    suffixes = ["은행", "카드", "증권", "보험", "캐피탈", "페이", "쇼핑", "뱅크"]
    base_name = app_name
    for s in suffixes:
        base_name = base_name.replace(s, "")
    base_name = base_name.strip()
    if base_name:
        core_names.append(base_name)
    core_names.append(app_name)

    # 키워드에서도 핵심어 추출
    for kw in keywords:
        core = kw
        for remove in ["업데이트", "신기능", "이벤트", "앱", "출시", "개편"]:
            core = core.replace(remove, "")
        core = core.strip()
        if core and len(core) >= 2:
            core_names.append(core)

    for name in core_names:
        if name.lower() in title_lower:
            name_found = True
            break

    if not name_found:
        return False

    # 3. 긍정 키워드가 있으면 더 좋지만, 앱 이름만 있어도 통과
    #    단, 너무 일반적인 기사는 긍정 키워드가 있어야 통과
    if _has_positive_signal(title):
        return True

    # 앱 이름이 있지만 긍정 신호가 없는 경우:
    # 제목 길이가 짧으면 (정보성 낮음) 제외
    if len(title) < 15:
        return False

    return True


def _fetch_google_news(keyword, max_items=5):
    """Google News RSS에서 뉴스 검색 (최근 1개월)"""
    results = []
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

            if not _is_recent(date_str, days=31):
                continue

            # 설명 텍스트 정리: HTML 제거 후 출처명 제거
            desc_raw = _clean_html(description)
            desc = _make_summary(title, desc_raw, source_name)

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
    앱별 뉴스 큐레이션
    - 앱 서비스/UX/기능 업데이트 관련 뉴스만 수집
    - 노이즈(퀴즈, 주가, 채용 등) 자동 필터링
    - 출처명 제거된 깨끗한 요약 텍스트 제공
    """
    all_results = []
    seen_titles = set()

    if not keywords:
        return all_results

    from config import APPS
    app_config = APPS.get(app_id, {})
    app_name = app_config.get("name", "")

    print(f"  [News] {app_id} ({app_name}):")

    for keyword in keywords:
        print(f"    [Google News] '{keyword}'")
        try:
            items = _fetch_google_news(keyword, max_items=8)
            accepted = 0

            for item in items:
                title = item["title"]

                if title in seen_titles:
                    continue

                # 엄격한 관련성 체크
                if app_name and not _is_relevant(title, app_name, keywords):
                    print(f"      [Skip] {title[:50]}...")
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
                accepted += 1

            print(f"      -> {len(items)}건 중 {accepted}건 채택")

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
