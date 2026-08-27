# 치안 AI 업무 추진현황 브리핑 — 최종본

경찰청 인공지능 및 데이터 기반 행정 위원회 소통채널 공유용 GitHub Pages 웹페이지입니다.

## 주요 구성
- 6대 영역별 치안 AI 추진현황
- 주요 치안 AI 사업·서비스 현황
- 언론 보도·관련 뉴스
- GitHub Actions 기반 뉴스 자동 업데이트

## 뉴스 자동 업데이트
- `.github/workflows/update-news.yml` : 30분마다 자동 실행
- `scripts/update_news.py` : 관련 뉴스 RSS 수집
- `data/news.json` : 최신 뉴스 데이터
- `index.html` : 접속 시 최신 뉴스 데이터 표시

## GitHub Pages 업로드 후
1. 저장소 최상위에 이 폴더의 구조 그대로 업로드
2. `Settings` → `Pages`
3. `Deploy from a branch`
4. Branch `main`, Folder `/(root)` 선택
5. `Actions` 탭에서 `Update AI news` 확인
6. 처음 한 번 `Run workflow` 실행
