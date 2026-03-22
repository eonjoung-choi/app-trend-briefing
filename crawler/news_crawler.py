"""
뉴스 크롤러 (Google News RSS 기반)
- 앱 서비스 업데이트/UX/기능 관련 뉴스만 큐레이션
- Google News RSS 사용 (서버 환경에서 안정적)
- 최근 1개월 뉴스만 수집
- 관련성 필터 강화: 앱 서비스/UX와 무관한 뉴스 차단
- 동일 토픽 중복 제거: 같은 사건/주제의 뉴스는 대표 1건만 수집
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
    "갤럭시", "아이폰", "영상갤",
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
    "장애", "접속 오류", "서버 점검",
]

# 2글자 한글 base_name 중 일반 단어로 오매칭 위험이 큰 이름
GENERIC_SHORT_NAMES = {"우리", "하나", "신한"}


def _parse_rss_date(date_str):
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
    try:
        dt = datetime.strptime(date_str, "%Y.%m.%d")
        cutoff = datetime.now() - timedelta(days=days)
        return dt >= cutoff
    except (ValueError, TypeError):
        return True


def _clean_html(text):
    if not text:
        return ""
    clean = re.sub(r'<[^>]+>', '', text)
    clean = clean.replace('&nbsp;', ' ').replace('\u00a0', ' ').replace('\xa0', ' ')
    clean = clean.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
    clean = clean.replace('&#39;', "'").replace('&quot;', '"')
    clean = re.sub(r'\s+', ' ', clean)
    return clean.strip()


def _extract_source(title):
    if ' - ' in title:
        parts = title.rsplit(' - ', 1)
        return parts[0].strip(), parts[1].strip()
    return title.strip(), ""


def _remove_source_from_desc(desc, source_name):
    if not desc or not source_name:
        return desc
    patterns = [f"  {source_name}", f" {source_name}", source_name]
    for pat in patterns:
        if desc.endswith(pat):
            desc = desc[:-len(pat)].strip()
            break
    return desc


def _make_summary(title, desc, source_name):
    clean_desc = _remove_source_from_desc(desc, source_name)
    if not clean_desc:
        return ""
    title_clean = title.lower().replace(" ", "")
    desc_clean = clean_desc.lower().replace(" ", "")
    if title_clean and desc_clean:
        shorter = min(len(title_clean), len(desc_clean))
        if shorter > 0:
            overlap = sum(1 for a, b in zip(title_clean, desc_clean) if a == b)
            if overlap / shorter > 0.7:
                return ""
    if len(clean_desc) > 200:
        clean_desc = clean_desc[:200] + "..."
    return clean_desc


def _has_noise(title):
    title_lower = title.lower()
    for noise in NOISE_KEYWORDS:
        if noise.lower() in title_lower:
            return True
    return False


def _has_positive_signal(title):
    title_lower = title.lower()
    for pos in POSITIVE_KEYWORDS:
        if pos.lower() in title_lower:
            return True
    return False


def _is_relevant(title, app_name, keywords):
    """제목이 해당 앱의 서비스/UX 관련 뉴스인지 판단"""
    if _has_noise(title):
        return False

    title_lower = title.lower()
    name_found = False

    # 앱 이름 매칭용 후보 생성
    core_names = []
    suffixes = ["은행", "카드", "증권", "보험", "캐피탈", "페이", "쇼핑", "뱅크"]
    base_name = app_name
    for s in suffixes:
        base_name = base_name.replace(s, "")
    base_name = base_name.strip()

    # 2글자 일반 한글 단어(우리, 하나, 신한)는 base_name으로 사용 안 함
    if base_name and base_name not in GENERIC_SHORT_NAMES:
        core_names.append(base_name)
    # 전체 앱 이름은 항상 포함
    core_names.append(app_name)

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

    # 앱/서비스 관련 긍정 신호가 있으면 채택
    if _has_positive_signal(title):
        return True

    # 긍정 신호 없고 제목이 너무 짧으면 제외
    if len(title) < 15:
        return False

    # 긍정 신호는 없지만 앱 이름이 제목에 있는 경우
    # → 전체 앱 이름(예: "우리은행")이 있어야 채택 (base_name "우리"만으론 부족)
    if app_name.lower() in title_lower:
        return True

    return False


# ── 동일 토픽 중복 제거 ──

def _extract_topic_words(title):
    """제목에서 토픽 비교용 핵심 단어 집합 추출"""
    # 따옴표, 특수문자, 말줄임 제거
    clean = re.sub(r"['\"\u2018\u2019\u201c\u201d\u2026\u00b7\-\?!,\.\(\)\[\]]", " ", title)
    words = set()
    for w in clean.split():
        w = w.strip()
        if len(w) >= 2:
            words.add(w)
    return words


def _is_same_topic(title_a, title_b):
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


def _pick_best_item(items):
    """동일 토픽 뉴스 중 가장 좋은 1건 선택 (desc 있는 것 우선, 최신 우선)"""
    best = items[0]
    for item in items[1:]:
        # desc가 있는 기사 우선
        if item.get("description", "") and not best.get("description", ""):
            best = item
        # 날짜가 최신인 것 우선
        elif item.get("date", "") > best.get("date", ""):
            best = item
    return best


def _dedup_by_topic(items):
    """동일 토픽 뉴스를 그룹핑하고 대표 1건만 남김"""
    if len(items) <= 1:
        return items

    groups = []  # list of lists
    for item in items:
        title = item.get("title", "")
        placed = False
        for group in groups:
            rep_title = group[0].get("title", "")
            if _is_same_topic(title, rep_title):
                group.append(item)
                placed = True
                break
        if not placed:
            groups.append([item])

    result = []
    for group in groups:
        result.append(_pick_best_item(group))
    return result


def _fetch_google_news(keyword, max_items=5):
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

            desc_raw = _clean_html(description)
            desc = _make_summary(title, desc_raw, source_name)

            results.append({
                "title": title, "link": link, "date": date_str,
                "description": desc, "source": source_name,
                "channel": f"Google News ({source_name})" if source_name else "Google News",
            })
            count += 1
    except requests.exceptions.RequestException as e:
        print(f"    [Error] Google News RSS '{keyword}': {type(e).__name__}: {e}")
    except ET.ParseError as e:
        print(f"    [Error] XML parse '{keyword}': {e}")
    return results


def crawl_news(app_id: str, keywords: list) -> list:
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
                if app_name and not _is_relevant(title, app_name, keywords):
                    print(f"      [Skip] {title[:50]}...")
                    continue
                seen_titles.add(title)
                all_results.append({
                    "app_id": app_id, "title": title,
                    "description": item.get("description", ""),
                    "link": item["link"], "date": item["date"],
                    "channel": item.get("channel", "Google News"),
                    "keyword": keyword,
                })
                accepted += 1
            print(f"      -> {len(items)}건 중 {accepted}건 채택")
        except Exception as e:
            print(f"    [Error] {app_id} - '{keyword}': {type(e).__name__}: {e}")
            continue
        time.sleep(1)

    # ★ 동일 토픽 중복 제거: 같은 사건의 뉴스는 대표 1건만 유지
    before_dedup = len(all_results)
    all_results = _dedup_by_topic(all_results)
    deduped = before_dedup - len(all_results)
    if deduped > 0:
        print(f"  [Dedup] {app_id}: {before_dedup}건 → {len(all_results)}건 (토픽 중복 {deduped}건 제거)")

    print(f"  [News] {app_id}: 총 {len(all_results)}건 수집\n")
    return all_results
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
    try:
        dt = datetime.strptime(date_str, "%Y.%m.%d")
        cutoff = datetime.now() - timedelta(days=days)
        return dt >= cutoff
    except (ValueError, TypeError):
        return True


def _clean_html(text):
    if not text:
        return ""
    clean = re.sub(r'<[^>]+>', '', text)
    clean = clean.replace('&nbsp;', ' ').replace('\u00a0', ' ').replace('\xa0', ' ')
    clean = clean.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
    clean = clean.replace('&#39;', "'").replace('&quot;', '"')
    clean = re.sub(r'\s+', ' ', clean)
    return clean.strip()


def _extract_source(title):
    if ' - ' in title:
        parts = title.rsplit(' - ', 1)
        return parts[0].strip(), parts[1].strip()
    return title.strip(), ""


def _remove_source_from_desc(desc, source_name):
    if not desc or not source_name:
        return desc
    patterns = [f"  {source_name}", f" {source_name}", source_name]
    for pat in patterns:
        if desc.endswith(pat):
            desc = desc[:-len(pat)].strip()
            break
    return desc


def _make_summary(title, desc, source_name):
    clean_desc = _remove_source_from_desc(desc, source_name)
    if not clean_desc:
        return ""
    title_clean = title.lower().replace(" ", "")
    desc_clean = clean_desc.lower().replace(" ", "")
    if title_clean and desc_clean:
        shorter = min(len(title_clean), len(desc_clean))
        if shorter > 0:
            overlap = sum(1 for a, b in zip(title_clean, desc_clean) if a == b)
            if overlap / shorter > 0.7:
                return ""
    if len(clean_desc) > 200:
        clean_desc = clean_desc[:200] + "..."
    return clean_desc


def _has_noise(title):
    title_lower = title.lower()
    for noise in NOISE_KEYWORDS:
        if noise.lower() in title_lower:
            return True
    return False


def _has_positive_signal(title):
    title_lower = title.lower()
    for pos in POSITIVE_KEYWORDS:
        if pos.lower() in title_lower:
            return True
    return False


def _is_relevant(title, app_name, keywords):
    if _has_noise(title):
        return False
    title_lower = title.lower()
    name_found = False
    core_names = []
    suffixes = ["은행", "카드", "증권", "보험", "캐피탈", "페이", "쇼핑", "뱅크"]
    base_name = app_name
    for s in suffixes:
        base_name = base_name.replace(s, "")
    base_name = base_name.strip()
    if base_name:
        core_names.append(base_name)
    core_names.append(app_name)
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
    if _has_positive_signal(title):
        return True
    if len(title) < 15:
        return False
    return True


def _fetch_google_news(keyword, max_items=5):
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
            desc_raw = _clean_html(description)
            desc = _make_summary(title, desc_raw, source_name)
            results.append({
                "title": title, "link": link, "date": date_str,
                "description": desc, "source": source_name,
                "channel": f"Google News ({source_name})" if source_name else "Google News",
            })
            count += 1
    except requests.exceptions.RequestException as e:
        print(f"    [Error] Google News RSS '{keyword}': {type(e).__name__}: {e}")
    except ET.ParseError as e:
        print(f"    [Error] XML parse '{keyword}': {e}")
    return results


def crawl_news(app_id: str, keywords: list) -> list:
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
                if app_name and not _is_relevant(title, app_name, keywords):
                    print(f"      [Skip] {title[:50]}...")
                    continue
                seen_titles.add(title)
                all_results.append({
                    "app_id": app_id, "title": title,
                    "description": item.get("description", ""),
                    "link": item["link"], "date": item["date"],
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
