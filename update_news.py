#!/usr/bin/env python3
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

QUERIES = [
    ("치안 AI", '"치안 AI" when:90d'),
    ("모두의 경찰관", '"모두의 경찰관" when:90d'),
    ("수사지원 AI", '"수사지원 AI" 경찰 when:90d'),
    ("KICS AI", '"KICS AI" 경찰 when:90d'),
    ("차세대 112", '"차세대 112" AI 경찰 when:90d'),
    ("경찰 AI", '"경찰청" 인공지능 AI when:30d'),
]

MAX_PER_QUERY = 8
MAX_TOTAL = 18

TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    value = html.unescape(value)
    value = TAG_RE.sub(" ", value)
    return SPACE_RE.sub(" ", value).strip()


def normalize_title(value: str) -> str:
    value = clean_text(value).lower()
    value = re.sub(r"[\W_]+", "", value, flags=re.UNICODE)
    return value[:180]


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


def summarize(description: str, title: str) -> str:
    text = clean_text(description)
    if not text:
        return "치안 AI 관련 최신 기사입니다."
    title_clean = clean_text(title)
    if text.startswith(title_clean):
        text = text[len(title_clean):].lstrip(" -–—:")
    text = re.sub(r"\s*더보기\s*$", "", text)
    if len(text) > 170:
        text = text[:167].rstrip() + "…"
    return text or "치안 AI 관련 최신 기사입니다."


def fetch_feed(topic: str, query: str) -> list[dict]:
    url = (
        "https://news.google.com/rss/search?"
        f"q={quote_plus(query)}&hl=ko&gl=KR&ceid=KR:ko"
    )
    req = Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; KNPA-AI-News-Monitor/1.0)"},
    )
    with urlopen(req, timeout=20) as res:
        raw = res.read()

    root = ET.fromstring(raw)
    items = []

    for node in root.findall(".//item")[:MAX_PER_QUERY]:
        title = clean_text(node.findtext("title"))
        link = clean_text(node.findtext("link"))
        description = node.findtext("description") or ""
        pub_date = node.findtext("pubDate")
        source_node = node.find("source")
        source = clean_text(source_node.text if source_node is not None else "")

        if not title or not link:
            continue

        published, ts = parse_date(pub_date)
        if source and title.endswith(" - " + source):
            title = title[: -(len(source) + 3)].rstrip()

        items.append({
            "title": title,
            "link": link,
            "source": source or "Google 뉴스",
            "topic": topic,
            "published": published,
            "summary": summarize(description, title),
            "_ts": ts,
        })
    return items


def load_existing() -> dict:
    try:
        return json.loads(OUT.read_text(encoding="utf-8"))
    except Exception:
        return {"generated_at": None, "items": []}


def main() -> int:
    collected = []
    errors = []

    for topic, query in QUERIES:
        try:
            collected.extend(fetch_feed(topic, query))
        except Exception as exc:
            errors.append(f"{topic}: {exc}")

    if not collected:
        existing = load_existing()
        print("새 기사를 수집하지 못했습니다. 기존 news.json을 유지합니다.")
        for error in errors:
            print("WARN", error, file=sys.stderr)
        return 0 if existing.get("items") else 1

    seen = set()
    unique = []

    for item in sorted(collected, key=lambda x: x.get("_ts", 0), reverse=True):
        key = normalize_title(item["title"])
        if not key or key in seen:
            continue
        seen.add(key)
        item.pop("_ts", None)
        unique.append(item)
        if len(unique) >= MAX_TOTAL:
            break

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "items": unique,
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(unique)}개 기사 저장: {OUT}")

    for error in errors:
        print("WARN", error, file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
