# CLAUDE.md

이 프로젝트의 기준 문서는 `SERVICE.md`다. 요구사항, 범위, 완료 기준은 거기를 따르고, 이 파일과 충돌하면 `SERVICE.md`가 우선한다.

## 프로젝트 개요

Markdown 요구사항 문서에서 요구사항을 추출하고, 변경 이력을 요청자·수정자와 함께 기록하며, 이력을 근거로 출처와 함께 질의응답하는 대화형 에이전트. 사용자는 개발자, 기획자, 고객(약 20~30명).

## 범위

- **1차에서 만든다:** 요구사항 추출, 이력 질의응답, 수동 추가·수정·삭제, 수동 수정 반영 새 버전 PRD 생성
- **2차에서 만들었다:** 변경 자동 감지, 웹 UI (`TASKS.md`의 묶음 7·8)
- **하지 않는다:** 로그인·권한, 승인 워크플로, Markdown 외 입력 형식(엑셀·피피티·지라), 알림, 의존·영향도 분석

범위 밖 기능을 요청 없이 추가하지 않는다. 필요해 보이면 구현하지 말고 먼저 물어본다.

## 사용자 확인 (interrupt)

- 사용자에게 확인·선택을 받는 곳은 모두 LangGraph `interrupt()`로 구현한다(문서 입력·추가·수정·삭제의 실행 확인, 출처를 못 찾았을 때의 대안 선택). 프롬프트 지시만으로 확인을 대신하지 않는다.
- `interrupt()`는 `src/interfaces/tools/tools.py`에서만 호출하고, 재개는 `src/interfaces/agent/agent.py`의 `run_turn(agent, text, config, respond)`가 맡는다. 콘솔 입출력(`cli_respond`)만 `main.py`에 둔다.
- 재개 시 tool이 처음부터 다시 실행되므로 `interrupt()` 앞의 코드는 부작용이 없어야 한다(검증·조회만). 저장은 확인 뒤에 한다.

## 기술 스택

- Python, LangChain(`langchain-aws`, `langchain-chroma`), LangGraph(에이전트 그래프), `python-dotenv`
- LLM: Bedrock `global.anthropic.claude-sonnet-4-5-20250929-v1:0`, `us-east-1`, temperature 0
- 폴백 LLM: Bedrock `global.anthropic.claude-haiku-4-5-20251001-v1:0` (기본 LLM이 2번 시도 후에도 실패할 때만 그 호출을 전환, 리전·temperature 동일, 전환 사실은 로그와 CLI 안내로 알림. `src/infrastructure/llm/llm.py`의 `get_llm()`)
- 임베딩: Bedrock `amazon.titan-embed-text-v2:0`, `us-east-1`, 차원 1024로 시작
- 구조화 데이터: SQLite (`documents`, `chunks`, `requirements`, `requirement_history` 등 목적별 테이블)
- 의미 검색: Chroma 새 컬렉션 (예: `requirements_history`)
- 테스트: pytest
- 가상환경은 루트의 `.venv`를 쓴다. 의존성은 `requirements.txt`에 있으며, 새 패키지를 추가할 때는 먼저 확인한다. (평가용 `ragas`는 확인을 받고 추가했다.)

## LLM 호출 로그

- 에이전트를 부르는 곳(`main.py`, `src/web/app.py`)은 `src/interfaces/agent/agent.py`의 `run_config(thread_id)`로 설정을 만든다. 여기에 trace 콜백이 들어 있어 LLM·도구 호출이 `data/logs/llm_trace.jsonl`에 JSON Lines로 남는다. 설정을 직접 만들면 로그가 빠진다.
- 로그에는 요구사항 내용이 들어가므로 `data/` 밖으로 옮기거나 커밋하지 않는다. 끄려면 `.env`에 `TRACE_ENABLED=0`.

## 반드시 지킬 규칙

1. **요청자·수정자 필수:** 모든 변경(1차는 수동 추가·수정·삭제)에 `requester`와 `modifier`를 받고, 비어 있으면 저장을 거부한다. 어떤 경로로도 우회할 수 없어야 한다.
2. **수동/자동 구분:** 이력에 변경이 수동인지 자동인지 기록한다.
   - 수동 추가·수정·삭제는 저장 전에 `interrupt()`로 한 번 확인(y/N)을 받고, 저장할 때마다 새 버전 문서를 새 파일로 자동 생성한다(T21). 사용자가 직접 요청해서 만드는 방식도 그대로 둔다.
   - 수동으로 수정된 부분이 있으면 반영된 새 버전 PRD 문서(Markdown)를 새 파일로 생성한다. 원본 입력 문서는 수정하지 않는다.
3. **출처를 지어내지 않는다:** 출처를 찾지 못하면 "찾지 못했다"고 답하고 사용자가 대안을 고르도록 안내한다. 답변에는 문서, 버전, 요청자, 수정자 출처를 함께 보여 준다.
4. **정확성이 속도보다 우선이다:** 응답 속도는 요구사항이 아니다. 정확한 출처와 답변을 우선한다.
5. **핵심 로직은 UI와 분리한다:** 대화 진입점(`main.py`)과 에이전트·tool은 얇게 두고, 이후 웹 UI가 같은 로직을 재사용할 수 있어야 한다.
   - 코드는 3계층으로 나누고 의존은 `interfaces → core → infrastructure` 한 방향으로만 둔다. 설정(`config/`)은 루트에 두고 모든 계층이 읽는다.
     - `src/interfaces/`: `agent/`, `tools/`. 기능은 `src/core/service/service.py`를 호출만 한다. `agent`는 LangGraph 그래프(agent 노드 ↔ ToolNode)로 tool을 골라 호출하고 대화 기록은 체크포인터가 관리한다. 여기에는 로직을 넣지 않는다.
     - 루트 `src/web/`(FastAPI 서버와 정적 화면)도 `main.py`처럼 서비스 진입점이다. 웹 서버는 `python -m uvicorn --factory src.web.app:create_app --host 0.0.0.0 --port 8000`으로 띄운다. 여기에도 로직을 넣지 않고 `src/core/service`와 에이전트(`step`, `resume`)만 호출한다.
     - 루트 `main.py`가 서비스 진입점이다. 인자 없이 `python main.py`로 에이전트 대화만 시작하며 기능별 명령은 없다. 여기에도 로직을 넣지 않는다.
     - `src/core/`: `service/`(유스케이스 진입점), `ingest/`, `chunking/`, `extraction/`, `qa/`, `manual/`, `change_detection/`(2차: 새 버전 문서와 현재 요구사항 비교). UI와 무관한 핵심 로직.
     - `src/infrastructure/`: `db/`(SQLite), `indexing/`(Chroma), `llm/`(Bedrock, 폴백, LLM·도구 호출 로그 `tracer.py`). 외부 시스템 연결.
6. **기존 자료는 건드리지 않는다:** `practices/day*_practice/`는 참고용이다. 수정하지 않고, 그 안의 `chroma_db`와 `sds_policies` 컬렉션도 사용하지 않는다.
7. **새 코드는 루트에 둔다:** `practices/day*_practice/` 안에 새 코드를 만들지 않는다.
8. **자격증명을 코드에 넣지 않는다:** AWS 인증은 `.env`(`load_dotenv`)로만 한다. `.env`를 커밋하거나 출력하지 않는다.

## 참고할 기존 코드

`practices/day*_practice/`의 아래 파일은 구조와 사용법을 참고하되 복사해서 새로 만든다.

- `rag_chain.py`: Bedrock LLM·임베딩과 Chroma 연결 방식
- `chunking_md.py`: Markdown 헤딩 단위 청킹
- `retriever.py`, `hybrid.py`: 검색 방식 (하이브리드: 한국어 BM25 0.3 + 벡터 0.7, 요구사항 청크만 대상. `src/infrastructure/indexing/indexing.py`)
- `verify_sources.py`: 출처 검증 방식

## 샘플과 검증

- `samples/PRDv1.md`, `samples/PRDv2.md`: 정기투표앱 가상 PRD (요구사항 8~9개)
  - v1에서 v2로: REQ-003 변경(7일에서 14일), REQ-006 삭제, REQ-009·REQ-010 추가
  - 1차 검증은 `PRDv1.md`를 쓴다. `PRDv2.md`는 2차(변경 감지)에서 쓴다.
- `samples/LargePRDv1.md`: 요구사항 104개 가상 PRD. 규모 검증(`tests/test_scale.py`)에 쓴다. 요구사항이 더 많은 샘플을 새로 만들 때는 사용자에게 먼저 알린다.
- 1차 완료 기준은 `SERVICE.md`의 "완료 기준" 7개이며, 각각 `tests/` 아래 pytest로 검증한다(검증 명령은 `SERVICE.md` 표). 일부 테스트는 실제 Bedrock을 호출한다.
- 정확도 기준(질문 10개에 출처까지 모두 정확)은 "샘플 질문 세트 10개 전부 통과"로 확정했다. 질문 세트는 `tests/qa_set.py`다.

## 정답셋 평가와 LLM 심사

- `python src/evaluation/run_eval.py`가 `src/evaluation/eval_set.json`을 실제 Bedrock으로 돌려 기대값(키워드, 출처)과 대조한다. 출처는 코드로 정확히 비교하며 LLM에 맡기지 않는다.
- `--judge`를 붙이면 `src/evaluation/judge.py`의 LLM 심사자가 답변 문장이 근거에 충실한지(`supported`), 질문에 답했는지(`answers_question`)를 추가로 본다. 참고용이라 PASS/FAIL·종료 코드에 영향을 주지 않으며, 기본은 꺼짐이다(문항당 Bedrock 호출 1회 증가). 심사 결과로 통과 기준을 바꾸지 않는다.
- `--ragas`를 붙이면 `src/evaluation/ragas_metrics.py`가 RAGAS `faithfulness`·`answer_relevancy`(0~1)를 산출한다. 역시 참고용이며 기본은 꺼짐이다(문항당 Bedrock 호출 여러 번). ragas를 쓰는 코드는 `RAGAS_DO_NOT_TRACK`을 설정하고 vertexai 가짜 모듈을 등록하는 이 파일을 거쳐야 한다(직접 `import ragas` 금지: 사용 통계 외부 전송, import 오류). Context Precision/Recall은 아직 없다(2단계).
- 평가 도구는 `src/evaluation/`에 두고 서비스 코드(`src/core/`, `src/interfaces/`)가 `src/evaluation/`를 가져다 쓰지 않는다. `src/evaluation/judge.py`는 `core.qa.qa`를 읽기만 한다.
- `run_eval.py`는 `src/evaluation/eval_result.json`을 덮어쓰므로 일부 문항만 돌린 뒤에는 커밋하지 않는다.

## 작업 방식

- `SERVICE.md`를 수정할 때마다 `CLAUDE.md`에 반영할 내용(범위, 규칙, 구조, 명령어 등)이 있는지 확인하고, 있으면 같은 작업에서 함께 갱신한다.
- 한국어로 소통하고, 코드 식별자와 주석은 영어 또는 한국어 중 파일 안에서 일관되게 쓴다.
- 구현 전에 `SERVICE.md`의 해당 섹션을 확인하고, 문서와 다르게 만들어야 하면 먼저 알린다.
- `SERVICE.md`의 "가정" 항목(진입점·tool 이름, DB·Chroma 경로 등)을 구현 중에 확정하면 `SERVICE.md`도 함께 갱신한다.
- AWS Bedrock 호출은 비용과 외부 전송이 생기므로, 테스트에서는 가능하면 호출 횟수를 최소화한다.
