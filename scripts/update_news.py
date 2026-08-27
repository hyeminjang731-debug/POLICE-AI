#!/usr/bin/env python3
"""
경찰청 인공지능 및 데이터 기반 행정 위원회 소통채널용 뉴스 수집기.

목표
- Google News RSS에서 치안 AI 관련 기사를 수집
- '경찰청/경찰/치안/수사/112/KICS/모두의 경찰관'과 직접 관련된 기사만 통과
- 일반 AI·산업·교육·클라우드 기사 등 오탐을 최대한 제거
- 유사 제목 중복 제거
- 한 언론사 기사만 과도하게 노출되지 않도록 소스별 개수 제한
"""

from __future__ import annotations

import html
import json
import re
import sys
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote_plus
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "news.json"

# 검색은 넓게 하되, 아래 relevance_score()에서 엄격하게 다시 거릅니다.
QUERIES = [
    ("치안 AI", '"치안 AI" when:120d'),
    ("치안 AI", '"경찰청" AI when:90d'),
    ("모두의 경찰관", '"모두의 경찰관" when:180d'),
    ("수사지원 AI", '"수사지원 AI" 경찰 when:120d'),
    ("수사지원 AI", '경찰 수사 인공지능 AI when:90d'),
    ("KICS AI", '"KICS AI" 경찰 when:180d'),
    ("KICS AI", '"형사사법정보" AI 경찰 when:180d'),
    ("차세대 112", '"차세대 112" AI when:180d'),
    ("차세대 112", '경찰 112 인공지능 AI when:90d'),
]

MAX_PER_QUERY = 10
MAX_TOTAL = 24
MAX_PER_SOURCE = 4
MIN_RELEVANCE_SCORE = 5

TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")

# 반드시 하나 이상 잡혀야 하는 경찰·치안 앵커
POLICE_ANCHORS = (
    "경찰청", "경찰", "치안", "수사", "형사", "112",
    "kics", "형사사법정보", "모두의 경찰관", "경찰민원",
)

# AI 성격을 확인하는 키워드
AI_ANCHORS = (
    "ai", "인공지능", "생성형 ai", "챗봇", "에이전트",
    "인공지능서비스", "인공지능 시스템",
)

# 직접 관련도가 높은 문구
STRONG_TERMS = (
    "모두의 경찰관",
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
)

# 제목에 이런 일반 산업 맥락만 있고 경찰 앵커가 약하면 배제
GENERIC_NOISE_TERMS = (
    "주식", "증시", "코스피", "코스닥", "반도체", "게임",
    "채용", "입시", "수능", "대학", "교육과정", "클라우드 월드",
    "gpu", "데이터센터", "조선", "해양", "유통", "금융권",
    "부동산", "가상자산", "코인", "스타트업 투자",
)


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    value = html.unescape(value)
    value = TAG_RE.sub(" ", value)
    return SPACE_RE.sub(" ", value).strip()


def normalize_for_match(value: str) -> str:
    value = clean_text(value).lower()
    value = value.replace("ＡＩ", "ai")
    return SPACE_RE.sub(" ", value)


def normalize_title(value: str) -> str:
    value = normalize_for_match(value)
    value = re.sub(r"[\W_]+", "", value, flags=re.UNICODE)
    return value[:220]


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


def has_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def topic_bonus(topic: str, text: str) -> int:
    """주제별 직접 관련성 점수."""
    score = 0

    if topic == "모두의 경찰관":
        if "모두의 경찰관" in text:
            score += 7
        elif "경찰" in text and ("민원" in text or "챗봇" in text or "에이전트" in text):
            score += 4

    elif topic == "수사지원 AI":
        if "수사지원 ai" in text:
            score += 6
        if ("수사" in text or "형사" in text) and has_any(text, AI_ANCHORS):
            score += 4
        if any(term in text for term in ("조서", "ocr", "음성인식", "범죄일람표", "질문 추천")):
            score += 2

    elif topic == "KICS AI":
        if "kics ai" in text:
            score += 7
        elif "kics" in text and ("경찰" in text or "수사" in text or "형사사법" in text):
            score += 5
        elif "형사사법정보" in text and has_any(text, AI_ANCHORS):
            score += 5

    elif topic == "차세대 112":
        if "차세대 112" in text:
            score += 7
        elif "112" in text and ("경찰" in text or "치안" in text) and has_any(text, AI_ANCHORS):
            score += 5

    elif topic == "치안 AI":
        if "치안 ai" in text:
            score += 6
        if ("경찰청" in text or "경찰" in text or "치안" in text) and has_any(text, AI_ANCHORS):
            score += 4

    return score


def relevance_score(topic: str, title: str, description: str, source: str) -> int:
    """
    엄격한 관련성 필터.
    핵심 원칙:
    1) 경찰/치안 앵커가 반드시 존재
    2) AI 관련 기사여야 함
    3) 주제별 직접 관련 문구에 가중치
    4) 일반 산업기사 오탐 감점
    """
    title_t = normalize_for_match(title)
    desc_t = normalize_for_match(description)
    source_t = normalize_for_match(source)
    text = f"{title_t} {desc_t} {source_t}"

    # 필수 게이트 1: 경찰·치안과 직접 연결되어야 함
    if not has_any(text, POLICE_ANCHORS):
        return -100

    # 필수 게이트 2: AI 맥락 또는 강한 특정 치안 AI 문구가 있어야 함
    if not (has_any(text, AI_ANCHORS) or has_any(text, STRONG_TERMS)):
        return -100

    score = 0

    # 제목에 경찰/치안 앵커가 있으면 더 신뢰
    if has_any(title_t, POLICE_ANCHORS):
        score += 3
    else:
        score += 1

    # 제목에 AI 앵커가 있으면 가점
    if has_any(title_t, AI_ANCHORS):
        score += 2
    elif has_any(desc_t, AI_ANCHORS):
        score += 1

    # 강한 직접 관련 문구
    strong_hits = sum(1 for term in STRONG_TERMS if term in text)
    score += min(strong_hits * 3, 9)

    # 주제별 세부 가중치
    score += topic_bonus(topic, text)

    # 너무 일반적인 산업 기사 감점
    noise_hits = sum(1 for term in GENERIC_NOISE_TERMS if term in title_t)
    if noise_hits:
        # 경찰/치안이 제목에 명확히 있지 않으면 강하게 배제
        if not any(term in title_t for term in ("경찰청", "경찰", "치안", "수사", "112", "kics")):
            score -= 8
        else:
            score -= min(noise_hits * 2, 6)

    return score


def build_summary(topic: str, description: str, title: str, source: str) -> str:
    """
    Google News RSS description이 단순히 '제목 + 언론사'인 경우가 많아,
    정보가 빈약하면 주제 중심의 짧은 설명으로 대체합니다.
    """
    text = clean_text(description)
    title_clean = clean_text(title)
    source_clean = clean_text(source)

    if text.startswith(title_clean):
        text = text[len(title_clean):].lstrip(" -–—:")
    if source_clean and text == source_clean:
        text = ""
    if len(text) < 30:
        fallback = {
            "치안 AI": "경찰청의 치안 AI 정책·서비스 추진과 관련된 최신 보도입니다.",
            "모두의 경찰관": "경찰민원 AI 에이전트 「모두의 경찰관」 추진과 관련된 최신 보도입니다.",
            "수사지원 AI": "경찰 수사업무를 지원하는 AI 기능·시스템 고도화와 관련된 최신 보도입니다.",
            "KICS AI": "KICS·형사사법정보 기반 AI 활용 및 수사지원 고도화와 관련된 최신 보도입니다.",
            "차세대 112": "112 신고·상황관리 분야의 AI 활용 및 차세대 시스템과 관련된 최신 보도입니다.",
        }
        return fallback.get(topic, "경찰청 치안 AI와 관련된 최신 보도입니다.")

    text = re.sub(r"\s*더보기\s*$", "", text)
    if len(text) > 180:
        text = text[:177].rstrip() + "…"
    return text


def fetch_feed(topic: str, query: str) -> list[dict]:
    url = (
        "https://news.google.com/rss/search?"
        f"q={quote_plus(query)}&hl=ko&gl=KR&ceid=KR:ko"
    )
    req = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; KNPA-AI-News-Monitor/2.0)"
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

        # Google News 제목 끝의 " - 언론사" 제거
        if source and title.endswith(" - " + source):
            title = title[: -(len(source) + 3)].rstrip()

        score = relevance_score(topic, title, description, source)
        if score < MIN_RELEVANCE_SCORE:
            continue

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


def main() -> int:
    collected: list[dict] = []
    errors: list[str] = []

    for topic, query in QUERIES:
        try:
            collected.extend(fetch_feed(topic, query))
        except Exception as exc:
            errors.append(f"{topic}: {exc}")

    if not collected:
        existing = load_existing()
        print("관련성 기준을 통과한 새 기사가 없습니다. 기존 news.json을 유지합니다.")
        for error in errors:
            print("WARN", error, file=sys.stderr)
        return 0 if existing.get("items") else 1

    # 최신순을 기본으로 하되, 같은 날짜라면 관련도 높은 기사 우선
    collected.sort(
        key=lambda x: (x.get("_ts", 0), x.get("_score", 0)),
        reverse=True,
    )

    seen_titles: set[str] = set()
    source_counts: dict[str, int] = {}
    unique: list[dict] = []

    for item in collected:
        key = normalize_title(item["title"])
        if not key or key in seen_titles:
            continue

        source = item.get("source") or "Google 뉴스"
        if source_counts.get(source, 0) >= MAX_PER_SOURCE:
            continue

        seen_titles.add(key)
        source_counts[source] = source_counts.get(source, 0) + 1

        item.pop("_ts", None)
        item.pop("_score", None)
        unique.append(item)

        if len(unique) >= MAX_TOTAL:
            break

    # 통과 기사가 너무 적더라도 관련 없는 기사로 채우지 않습니다.
    if not unique:
        existing = load_existing()
        print("관련성 기준을 통과한 기사가 없어 기존 news.json을 유지합니다.")
        return 0 if existing.get("items") else 1

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "filter_version": "strict-police-ai-v2",
        "items": unique,
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"{len(unique)}개 관련 기사 저장: {OUT}")

    for error in errors:
        print("WARN", error, file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
