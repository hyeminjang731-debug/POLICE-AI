#!/usr/bin/env python3
"""
경찰청 인공지능 및 데이터 기반 행정 위원회 소통채널용 뉴스 수집기 v3.

핵심 개선
1. 해양경찰청·해경 기사 제외
2. 경찰청·시도경찰청·경찰서 등 국가경찰 관련성 강화
3. 동일 사건·유사 제목 기사 1건만 남기는 사건 단위 중복 제거
4. 특정 언론사·기업·행사가 목록을 독점하지 않도록 제한
5. 주제별 균형 배치
   - 모두의 경찰관
   - 수사지원 AI
   - 112·현장 AI
   - 고영향·위험관리
   - AI 정책·거버넌스
   - 교육·확산
"""

from __future__ import annotations

import html
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote_plus
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "news.json"

# 검색은 비교적 넓게, 최종 선별은 아래 로직에서 엄격히 처리
QUERIES = [
    '"치안 AI" when:120d',
    '"경찰청" AI when:90d',
    '"경찰청" 인공지능 when:90d',
    '"모두의 경찰관" when:180d',
    '"경찰민원 AI" when:180d',
    '"수사지원 AI" 경찰 when:120d',
    '"KICS AI" 경찰 when:180d',
    '"형사사법정보" AI 경찰 when:180d',
    '"차세대 112" AI when:180d',
    '경찰 112 인공지능 when:90d',
    '"고영향 AI" 경찰 when:180d',
    '"AI 영향평가" 경찰 when:180d',
    '"AI 위험관리" 경찰 when:180d',
    '경찰 AI 교육 리터러시 when:180d',
]

MAX_PER_QUERY = 12
MAX_TOTAL = 18
MAX_PER_SOURCE = 3
MAX_PER_TOPIC = 4
MIN_RELEVANCE_SCORE = 6

TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")
TOKEN_RE = re.compile(r"[0-9A-Za-z가-힣]+")

# 해양경찰은 이 페이지의 대상에서 제외
EXCLUDED_TERMS = (
    "해양경찰청",
    "해양경찰",
    "해경",
    "코스트가드",
    "coast guard",
)

# 국가경찰 관련성을 판단할 핵심 앵커
POLICE_ANCHORS = (
    "경찰청",
    "경찰서",
    "경찰관",
    "경찰",
    "치안",
    "수사",
    "형사",
    "112",
    "kics",
    "형사사법정보",
    "모두의 경찰관",
    "경찰민원",
)

# 시도경찰청 명칭도 직접 관련 신호로 인정
PROVINCIAL_POLICE_RE = re.compile(
    r"(서울|부산|대구|인천|광주|대전|울산|세종|경기남부|경기북부|강원|충북|충남|"
    r"전북|전남|경북|경남|제주)(특별자치도)?경찰청"
)

AI_ANCHORS = (
    "ai",
    "인공지능",
    "생성형 ai",
    "챗봇",
    "에이전트",
    "머신러닝",
    "딥러닝",
    "인공지능서비스",
    "인공지능 시스템",
)

STRONG_TERMS = (
    "모두의 경찰관",
    "경찰민원 ai",
    "수사지원 ai",
    "kics ai",
    "차세대 112",
    "치안 ai",
    "경찰 ai",
    "경찰청 ai",
    "경찰 인공지능",
    "3d 얼굴인식",
    "고영향 ai",
    "ai 영향평가",
    "ai 위험관리",
    "ai 윤리",
    "ai 리터러시",
    "caio",
)

GENERIC_NOISE_TERMS = (
    "주식", "증시", "코스피", "코스닥", "반도체", "게임", "채용", "입시",
    "수능", "대학", "교육과정", "클라우드 월드", "gpu", "데이터센터",
    "조선", "해양", "유통", "금융권", "부동산", "가상자산", "코인",
    "스타트업 투자", "증권", "실적", "매출", "주가",
)

# 사건 중복 비교 시 제거할 너무 일반적인 단어
EVENT_STOPWORDS = {
    "경찰", "경찰청", "인공지능", "ai", "치안", "관련", "추진", "활용", "도입",
    "본격", "지원", "업무", "시스템", "서비스", "개발", "고도화", "위한", "통해",
    "등", "및", "의", "에", "서", "과", "와", "로", "으로", "한다", "나선다",
    "출범", "착수", "확대", "강화", "구축", "운영", "공개", "발표",
}

TOPIC_ORDER = [
    "모두의 경찰관",
    "수사지원 AI",
    "112·현장 AI",
    "고영향·위험관리",
    "AI 정책·거버넌스",
    "교육·확산",
    "기타 치안 AI",
]


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    value = html.unescape(value)
    value = TAG_RE.sub(" ", value)
    return SPACE_RE.sub(" ", value).strip()


def normalize_for_match(value: str) -> str:
    value = clean_text(value).lower()
    return SPACE_RE.sub(" ", value)


def has_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def parse_date(value: str | None) -> tuple[str, float]:
    if not value:
        return "", 0.0
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.date().isoformat(), dt.timestamp()
    except Exception:
        return clean_text(value)[:10], 0.0


def remove_source_suffix(title: str, source: str) -> str:
    if source and title.endswith(" - " + source):
        return title[: -(len(source) + 3)].rstrip()
    return title


def is_excluded(text: str) -> bool:
    t = normalize_for_match(text)
    return has_any(t, EXCLUDED_TERMS)


def has_police_anchor(text: str) -> bool:
    t = normalize_for_match(text)
    return has_any(t, POLICE_ANCHORS) or bool(PROVINCIAL_POLICE_RE.search(t))


def classify_topic(title: str, description: str) -> str:
    text = normalize_for_match(f"{title} {description}")

    if "모두의 경찰관" in text or "경찰민원 ai" in text or (
        "경찰" in text and "민원" in text and has_any(text, AI_ANCHORS)
    ):
        return "모두의 경찰관"

    if any(term in text for term in (
        "수사지원 ai", "kics ai", "kics", "형사사법정보", "조서",
        "범죄일람표", "ocr", "음성인식", "수사 ai", "수사 인공지능"
    )):
        return "수사지원 AI"

    if "차세대 112" in text or (
        "112" in text and any(term in text for term in ("신고", "상황", "출동", "현장", "ai", "인공지능"))
    ):
        return "112·현장 AI"

    if any(term in text for term in (
        "고영향 ai", "영향평가", "위험관리", "3d 얼굴인식",
        "안전성", "신뢰성", "ai 윤리", "인공지능 윤리"
    )):
        return "고영향·위험관리"

    if any(term in text for term in (
        "caio", "리터러시", "ai 교육", "인공지능 교육",
        "해커톤", "세미나", "지역거점", "ai 담당관", "인공지능 담당관"
    )):
        return "교육·확산"

    if any(term in text for term in (
        "치안 ai", "경찰청 ai", "경찰 ai", "경찰 인공지능",
        "ai 정책", "인공지능 정책", "거버넌스", "국정과제", "위원회"
    )):
        return "AI 정책·거버넌스"

    return "기타 치안 AI"


def relevance_score(title: str, description: str, source: str) -> int:
    title_t = normalize_for_match(title)
    desc_t = normalize_for_match(description)
    text = f"{title_t} {desc_t} {normalize_for_match(source)}"

    # 해양경찰 관련은 즉시 제외
    if is_excluded(text):
        return -100

    # 국가경찰·치안 맥락 필수
    if not has_police_anchor(text):
        return -100

    # AI 맥락 필수
    if not (has_any(text, AI_ANCHORS) or has_any(text, STRONG_TERMS)):
        return -100

    score = 0

    # 제목에 경찰 관련성이 명시되면 강한 가점
    if has_police_anchor(title_t):
        score += 4
    else:
        score += 1

    if has_any(title_t, AI_ANCHORS):
        score += 3
    elif has_any(desc_t, AI_ANCHORS):
        score += 1

    strong_hits = sum(1 for term in STRONG_TERMS if term in text)
    score += min(strong_hits * 3, 12)

    # 경찰청·시도청 직접 언급 추가 가점
    if "경찰청" in title_t or PROVINCIAL_POLICE_RE.search(title_t):
        score += 3

    # 일반 산업 뉴스 감점
    noise_hits = sum(1 for term in GENERIC_NOISE_TERMS if term in title_t)
    if noise_hits:
        if not has_police_anchor(title_t):
            score -= 10
        else:
            score -= min(noise_hits * 2, 8)

    return score


def build_summary(topic: str, description: str, title: str, source: str) -> str:
    text = clean_text(description)
    title_clean = clean_text(title)
    source_clean = clean_text(source)

    if text.startswith(title_clean):
        text = text[len(title_clean):].lstrip(" -–—:")
    if source_clean and text == source_clean:
        text = ""

    if len(text) < 30:
        fallback = {
            "모두의 경찰관": "경찰민원 AI 에이전트 「모두의 경찰관」 추진과 관련된 최신 보도입니다.",
            "수사지원 AI": "경찰 수사업무를 지원하는 AI 기능·시스템 고도화와 관련된 최신 보도입니다.",
            "112·현장 AI": "112 신고·상황관리 및 현장 대응 분야의 AI 활용과 관련된 최신 보도입니다.",
            "고영향·위험관리": "치안 AI의 고영향 판단, 영향평가, 위험관리·신뢰성 확보와 관련된 최신 보도입니다.",
            "AI 정책·거버넌스": "경찰청의 치안 AI 정책·거버넌스 및 주요 사업 추진과 관련된 최신 보도입니다.",
            "교육·확산": "경찰 조직의 AI 교육·리터러시·지역확산과 관련된 최신 보도입니다.",
            "기타 치안 AI": "경찰청 치안 AI와 직접 관련된 최신 보도입니다.",
        }
        return fallback[topic]

    text = re.sub(r"\s*더보기\s*$", "", text)
    if len(text) > 180:
        text = text[:177].rstrip() + "…"
    return text


def event_tokens(title: str) -> set[str]:
    text = normalize_for_match(title)
    tokens = set(TOKEN_RE.findall(text))
    cleaned = {
        token for token in tokens
        if len(token) >= 2 and token not in EVENT_STOPWORDS
    }
    return cleaned


def char_bigrams(title: str) -> set[str]:
    text = re.sub(r"[^0-9a-z가-힣]+", "", normalize_for_match(title))
    if len(text) < 2:
        return {text} if text else set()
    return {text[i:i+2] for i in range(len(text) - 1)}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def same_event(title_a: str, title_b: str) -> bool:
    """
    다른 언론사가 표현을 조금 달리해도 동일 사건이면 1건으로 묶기.
    """
    ta, tb = event_tokens(title_a), event_tokens(title_b)
    token_sim = jaccard(ta, tb)

    ba, bb = char_bigrams(title_a), char_bigrams(title_b)
    char_sim = jaccard(ba, bb)

    shared = ta & tb

    # 구체 단어가 2개 이상 겹치고 문자 유사도도 어느 정도 있으면 동일 사건
    if len(shared) >= 2 and char_sim >= 0.34:
        return True

    # 제목 자체가 매우 유사
    if token_sim >= 0.58 or char_sim >= 0.60:
        return True

    # 동일 기관·기업·프로젝트 이름 등 긴 핵심 토큰이 겹치는 경우
    long_shared = {t for t in shared if len(t) >= 4}
    if len(long_shared) >= 2 and (token_sim >= 0.35 or char_sim >= 0.30):
        return True

    return False


def fetch_feed(query: str) -> list[dict]:
    url = (
        "https://news.google.com/rss/search?"
        f"q={quote_plus(query)}&hl=ko&gl=KR&ceid=KR:ko"
    )
    req = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; KNPA-AI-News-Monitor/3.0)"
        },
    )

    with urlopen(req, timeout=20) as res:
        raw = res.read()

    root = ET.fromstring(raw)
    items: list[dict] = []

    for node in root.findall(".//item")[:MAX_PER_QUERY]:
        title = clean_text(node.findtext("title"))
        link = clean_text(node.findtext("link"))
        description = node.findtext("description") or ""
        pub_date = node.findtext("pubDate")
        source_node = node.find("source")
        source = clean_text(source_node.text if source_node is not None else "")

        if not title or not link:
            continue

        title = remove_source_suffix(title, source)

        score = relevance_score(title, description, source)
        if score < MIN_RELEVANCE_SCORE:
            continue

        topic = classify_topic(title, description)
        published, ts = parse_date(pub_date)

        items.append({
            "title": title,
            "link": link,
            "source": source or "Google 뉴스",
            "topic": topic,
            "published": published,
            "summary": build_summary(topic, description, title, source),
            "_ts": ts,
            "_score": score,
        })

    return items


def load_existing() -> dict:
    try:
        return json.loads(OUT.read_text(encoding="utf-8"))
    except Exception:
        return {"generated_at": None, "items": []}


def deduplicate_events(items: list[dict]) -> list[dict]:
    """
    최신순·관련도순으로 보면서 이미 뽑힌 기사와 같은 사건이면 제거.
    """
    selected: list[dict] = []

    for item in items:
        if any(same_event(item["title"], chosen["title"]) for chosen in selected):
            continue
        selected.append(item)

    return selected


def balanced_select(items: list[dict]) -> list[dict]:
    """
    한 주제나 언론사에 치우치지 않도록 라운드로빈 방식으로 선별.
    """
    topic_buckets: dict[str, list[dict]] = defaultdict(list)
    for item in items:
        topic_buckets[item["topic"]].append(item)

    for topic in topic_buckets:
        topic_buckets[topic].sort(
            key=lambda x: (x.get("_ts", 0), x.get("_score", 0)),
            reverse=True,
        )

    source_counts: dict[str, int] = defaultdict(int)
    topic_counts: dict[str, int] = defaultdict(int)
    result: list[dict] = []

    # 1차: 각 주제에서 최소 1건씩 확보
    for topic in TOPIC_ORDER:
        bucket = topic_buckets.get(topic, [])
        while bucket:
            item = bucket.pop(0)
            source = item.get("source") or "Google 뉴스"
            if source_counts[source] >= MAX_PER_SOURCE:
                continue
            result.append(item)
            source_counts[source] += 1
            topic_counts[topic] += 1
            break

    # 2차: 주제별로 돌아가며 균형 있게 채움
    while len(result) < MAX_TOTAL:
        added = False

        for topic in TOPIC_ORDER:
            if len(result) >= MAX_TOTAL:
                break
            if topic_counts[topic] >= MAX_PER_TOPIC:
                continue

            bucket = topic_buckets.get(topic, [])
            while bucket:
                item = bucket.pop(0)
                source = item.get("source") or "Google 뉴스"

                if source_counts[source] >= MAX_PER_SOURCE:
                    continue

                # 이미 결과에 들어간 사건과 혹시 중복되는지 한 번 더 확인
                if any(same_event(item["title"], chosen["title"]) for chosen in result):
                    continue

                result.append(item)
                source_counts[source] += 1
                topic_counts[topic] += 1
                added = True
                break

        if not added:
            break

    return result


def main() -> int:
    collected: list[dict] = []
    errors: list[str] = []

    for query in QUERIES:
        try:
            collected.extend(fetch_feed(query))
        except Exception as exc:
            errors.append(f"{query}: {exc}")

    if not collected:
        existing = load_existing()
        print("관련성 기준을 통과한 새 기사가 없습니다. 기존 news.json을 유지합니다.")
        for error in errors:
            print("WARN", error, file=sys.stderr)
        return 0 if existing.get("items") else 1

    # 최신순 + 관련도순
    collected.sort(
        key=lambda x: (x.get("_ts", 0), x.get("_score", 0)),
        reverse=True,
    )

    # 동일 사건 제거 → 주제 균형 선별
    event_unique = deduplicate_events(collected)
    selected = balanced_select(event_unique)

    if not selected:
        existing = load_existing()
        print("최종 선별 결과가 없어 기존 news.json을 유지합니다.")
        return 0 if existing.get("items") else 1

    clean_items = []
    for item in selected:
        item = dict(item)
        item.pop("_ts", None)
        item.pop("_score", None)
        clean_items.append(item)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "filter_version": "strict-police-ai-v3-diverse",
        "selection_policy": {
            "exclude_maritime_police": True,
            "event_deduplication": True,
            "max_per_source": MAX_PER_SOURCE,
            "max_per_topic": MAX_PER_TOPIC,
            "max_total": MAX_TOTAL,
        },
        "items": clean_items,
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"{len(clean_items)}개 관련 기사 저장: {OUT}")
    print("주제별:", dict(
        (topic, sum(1 for i in clean_items if i["topic"] == topic))
        for topic in TOPIC_ORDER
        if any(i["topic"] == topic for i in clean_items)
    ))

    for error in errors:
        print("WARN", error, file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
