# 시연 영상 자동 녹화

```bash
pip install playwright imageio-ffmpeg && python -m playwright install chromium
python demo/record_demo.py      # 프로젝트 루트에서 → demo/out/demo.mp4
```

## 장면 (record_demo.py 의 자막 번호와 동일)
1. 소개 2. 회사 검색(삼성전자 입력) 3. 분석 시작(로딩 구간 cut)
4. 세 방법 비교 5. 회귀모델 내부 계산 6. 편차율 게이지 7. Scope 3 도넛 8. 규모 경고
9. Claude 리포트

- 앱을 localhost:8599 로 직접 띄우고 SUPABASE_URL/KEY 를 비워서 **DB에 기록이 남지 않음**.
- 장면이 실패하면 보통 앱 문구가 바뀐 것 → `record_demo.py` 의 selector/문구를 고쳐 다시 실행.
- DART / Claude API 는 실제로 호출됨 (API 키는 .env 에서 로드).
