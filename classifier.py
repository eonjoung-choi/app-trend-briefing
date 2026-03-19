"""
크롤링된 항목 분류기
- "feature" (업데이트 소식) vs "marketing" (마케팅 소식) 분류
- 키워드 기반 분류
- 자동 해시태그 생성
- 마케팅 타입 및 상태 결정
"""

import re
from datetime import datetime

# 분류 키워드
FEATURE_KEYWORDS = {
    '업데이트', '개편', '출시', '신기능', '버전', '리뉴얼', '개선',
    'UI', 'UX', '기능', '추가', '수정', '개선', '최적화', '버그',
    '안정성', '성능', '보안', '변경', '개선됨'
}

MARKETING_KEYWORDS = {
    '이벤트', '프로모션', '할인', '적립', '캐시백', '쿠폰', '콜라보',
    '캠페인', '경품', '기획전', '제휴', '이벤트', '프로모', '이벤트',
    '특가', '한정', '세일', '판매', '상품', '구매', '댓글', '추첨',
    '참여', '응모', '보상', '리워드', '포인트', '마일리지'
}


def classify(item: dict) -> dict:
    """
    크롤링된 항목을 분류하고 메타데이터 추가

    Args:
        item: 크롤링된 항목 (title, description, channel 등)

    Returns:
        분류된 항목 (category, tags, type, tc, status 필드 추가)
    """
    title = item.get('title', '').lower()
    desc = item.get('description', '').lower()
    content = f"{title} {desc}"

    # 1. 카테고리 분류 (feature or marketing)
    feature_score = sum(1 for kw in FEATURE_KEYWORDS if kw.lower() in content)
    marketing_score = sum(1 for kw in MARKETING_KEYWORDS if kw.lower() in content)

    if marketing_score > feature_score:
        category = 'marketing'
    else:
        category = 'feature'

    # 2. 해시태그 자동 생성
    tags = []
    for kw in FEATURE_KEYWORDS:
        if kw.lower() in content:
            tags.append(f"{kw}개선" if kw in ['UI', 'UX', '성능'] else kw)
    for kw in MARKETING_KEYWORDS:
        if kw.lower() in content:
            if kw not in tags:
                tags.append(kw)

    # 중복 제거 및 상위 3개만
    tags = list(dict.fromkeys(tags))[:3]

    # 3. 마케팅 타입 결정 (프로모션, 이벤트, 콜라보, 캠페인)
    marketing_type = None
    marketing_tc = None

    if category == 'marketing':
        content_lower = content.lower()

        if any(kw in content_lower for kw in ['할인', '특가', '세일', '캐시백']):
            marketing_type = '프로모션'
            marketing_tc = 'promo'
        elif any(kw in content_lower for kw in ['이벤트', '참여', '응모', '추첨']):
            marketing_type = '이벤트'
            marketing_tc = 'event'
        elif any(kw in content_lower for kw in ['콜라보', '제휴']):
            marketing_type = '콜라보'
            marketing_tc = 'collab'
        elif any(kw in content_lower for kw in ['캠페인', '기획전']):
            marketing_type = '캠페인'
            marketing_tc = 'campaign'
        else:
            marketing_type = '마케팅'
            marketing_tc = 'marketing'

    # 4. 상태 결정 (live, ended, scheduled)
    status = 'live'
    content_lower = content.lower()

    if any(kw in content_lower for kw in ['종료', '끝', '마감', '종료됨']):
        status = 'ended'
    elif any(kw in content_lower for kw in ['예정', '시작예정', '곧']):
        status = 'scheduled'

    # 5. 기간 추출 (마케팅 항목)
    period = None
    if category == 'marketing':
        # "MM.DD ~ MM.DD" 패턴 찾기
        period_match = re.search(r'(\d{1,2})\.(\d{1,2})\s*~\s*(\d{1,2})\.(\d{1,2})', title + desc)
        if period_match:
            m1, d1, m2, d2 = period_match.groups()
            period = f"{int(m1):02d}.{int(d1):02d} ~ {int(m2):02d}.{int(d2):02d}"

    # 결과 생성
    result = {
        **item,
        'category': category,
        'tags': tags,
    }

    if category == 'marketing':
        result['type'] = marketing_type
        result['tc'] = marketing_tc
        result['status'] = status
        if period:
            result['period'] = period

    return result


def generate_analysis(item: dict) -> str:
    """
    항목의 분석 요약문 생성

    Args:
        item: 분류된 항목

    Returns:
        분석 문구
    """
    title = item.get('title', '')
    category = item.get('category')
    tags = item.get('tags', [])

    if category == 'feature':
        tags_str = ', '.join(tags) if tags else '기능 개선'
        return f"{tags_str} 관련 업데이트"
    else:
        marketing_type = item.get('type', '마케팅')
        return f"{marketing_type} 진행 중"


def deduplicate_items(items: list[dict]) -> list[dict]:
    """
    중복 항목 제거 (제목 유사도 기반)

    Args:
        items: 항목 리스트

    Returns:
        중복이 제거된 항목 리스트
    """
    unique = {}
    for item in items:
        # 제목을 키로 사용 (채널과 링크 포함)
        key = (
            item.get('app_id'),
            item.get('title', '').lower().strip(),
            item.get('channel')
        )
        if key not in unique:
            unique[key] = item

    return list(unique.values())
