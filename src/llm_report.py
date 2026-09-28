"""정량 밸류에이션 결과를 애널리스트 리포트 형태의 서술로 변환 (Claude API).

핵심 설계 원칙: LLM에게 숫자를 직접 계산시키지 않는다. 이미 계산이 끝난 값만
프롬프트에 JSON으로 주입하고, LLM은 해석·서술(스토리텔링)만 담당하게 해
할루시네이션으로 인한 숫자 왜곡 위험을 원천 차단한다.
"""
import json
import os

import anthropic
from dotenv import load_dotenv

load_dotenv()

_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY가 설정되지 않았습니다. valuation-ai/.env를 확인하세요.")
        _client = anthropic.Anthropic(api_key=api_key)
    return _client


SYSTEM_PROMPT = """당신은 탄소배출·ESG를 처음 접하는 일반인에게 쉽게 설명해주는 환경회계 교육자입니다.
사용자가 제공하는 정량 데이터(재무데이터, Scope 1+2 실측/추정치, Scope 3 추정치, 모델 성능)는
이미 계산이 끝난 값입니다. 당신의 역할은 이 숫자를 비전공자도 이해할 수 있게 풀어서 설명하는
것이지, 숫자를 다시 계산하는 것이 아닙니다.

반드시 지켜야 할 규칙:
1. 주어진 숫자를 그대로 인용하라. 새로운 숫자를 계산·추정하지 마라.
2. 실제 공시된 값이 있는 경우와 모델 추정치인 경우를 명확히 구분해서 설명하라 (둘을 섞어 말하지 말 것).
3. 회귀모델 추정치와 업종평균 추정치가 다르면 그 차이를 솔직하게 설명하라 (숨기거나 하나로 짜맞추지 말 것).
4. 이 모델의 단순화 가정/한계를 최소 1가지 이상 명시적으로 언급하라 (특히 Scope 3는 전산업 평균
   배율을 적용한 추정치라는 점).
5. 전문 용어(Scope 1/2/3, tCO2eq, EEIO 등)를 쓸 때는 처음 등장할 때 괄호 안에 쉬운 말로 한 번 풀어써라.
   예: "Scope 3(협력사·물류 등 공급망 전체에서 나오는 간접배출)"
6. 아래 순서로 작성하라:
   - 한 줄 요약 (쉬운 말로)
   - 재무 하이라이트 (2~3문장, 쉬운 말로 — 이 회사가 무슨 사업을 하고 매출 규모가 어느 정도인지)
   - 배출량 해석 (실측/추정 여부, 회귀모델 vs 업종평균 비교, Scope 3 규모가 왜 이렇게 큰지)
   - 리스크 및 이 계산의 한계
7. 친절하고 쉬운 한국어로, 중학생도 이해할 수 있는 문장으로, 800자 내외로 작성하라.
   단, 숫자와 사실관계는 정확하게 유지하라 (쉬운 말로 풀되 내용을 왜곡하지 말 것).
"""


def generate_report(context: dict) -> str:
    client = _get_client()
    user_content = (
        "다음은 자동으로 계산된 기업 탄소배출량 분석 데이터입니다. 이 데이터를 바탕으로 리포트를 작성하세요.\n\n"
        + json.dumps(context, ensure_ascii=False, indent=2)
    )

    response = client.messages.create(
        model="claude-opus-5",
        max_tokens=2048,
        system=SYSTEM_PROMPT,
        output_config={"effort": "medium"},
        messages=[{"role": "user", "content": user_content}],
    )

    return "".join(block.text for block in response.content if block.type == "text")
