# 디자인펀치 블로그 검색순위 자동 점검

네이버 블로그(`blog.naver.com/giant7000`)에 올린 글이 네이버 검색에서 **몇 위에 노출되는지** 자동으로 확인하는 도구입니다.

## 무엇을 하나요?
1. 네이버에서 내 블로그 글 목록(제목·글번호·날짜)을 자동 수집
2. 각 글의 핵심 키워드로 네이버 검색 API를 돌려 순위 측정 (글번호로 정확히 매칭)
3. 보기 좋은 `report.html` 표 생성 + `data.json` 에 이력 누적(변동 추적)

## 직접 실행하기 (로컬)
1. 네이버 개발자센터에서 **검색 API** 키 발급 → `.env` 파일 생성
   ```
   NAVER_CLIENT_ID=발급받은_아이디
   NAVER_CLIENT_SECRET=발급받은_시크릿
   ```
2. 실행
   ```
   python3 check_rankings.py            # 최근 24개월
   python3 check_rankings.py --months 1 # 최근 1개월
   python3 check_rankings.py --all      # 전체
   ```
3. `report.html` 더블클릭해서 확인

## 자동 실행 (GitHub Actions)
- `.github/workflows/monthly.yml` 이 **매주 월요일 오전 9시(KST)** 자동 실행됩니다.
- 깃허브 레포 **Settings → Secrets and variables → Actions** 에 아래 두 개를 등록해야 작동합니다.
  - `NAVER_CLIENT_ID`
  - `NAVER_CLIENT_SECRET`
- 실행 결과(`report.html`, `data.json`)는 자동으로 커밋됩니다.

## 키워드가 어색하면
`keyword_overrides.csv` 에 `글번호,키워드` 한 줄씩 추가하면 다음 점검부터 그 키워드로 검색합니다.

## 참고 / 한계
- 순위는 네이버 **검색 API(유사도 기준)** 값으로, 실제 통합검색 화면과 약간 차이가 있을 수 있습니다.
- API 무료 한도는 하루 25,000회로 충분합니다.
