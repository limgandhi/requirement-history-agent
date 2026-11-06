# requirement-history-agent

## 목표 (Goal)

Bedrock(Claude, Titan)과 SQLite+Chroma로, Markdown 요구사항 문서에서 요구사항을 추출하고 요청자·수정자를 필수로 기록하며 이력을 질의응답하고, 수동 수정이 있으면 반영된 새 버전 PRD 문서를 생성하는 대화형 에이전트를 새로 만든다. 변경 자동 감지와 웹 UI는 1차 이후에 확장한다.

- **프로젝트 유형:** greenfield

## 배경 (Background)

### 사용자

- **대상:** 개발자, 기획자, 고객 (약 20~30명)
- **고객:** 직접 사용할 수 있다

### 현재 방식과 문제

- **현재 방식:** 문서 버전 파일, 엑셀, 피피티, 지라 등으로 요구사항을 관리한다
- **문제:** 어느 요구사항이나 기획이 언제, 어디서 변경됐는지 추적하기 어렵다

### 데이터

- **입력:** Markdown(.md) 문서만 지원한다
- **검증용 데이터:** 가상 샘플을 쓴다 (`samples/PRDv1.md`, `samples/PRDv2.md`, 정기투표앱 PRD)
- **규모:** 문서 한 건당 요구사항 100개 이상을 다룰 수 있어야 한다

## 범위 (Scope)

### 1차에서 만들 것

1. 요구사항 추출: 문서에서 요구사항을 추출해 저장
2. 이력 질의응답: 이력을 근거로 출처와 함께 답변
3. 수동 추가·수정·삭제: 요청자·수정자 필수, 이력에 수동으로 기록
4. 새 버전 PRD 생성: 수동으로 수정된 부분이 있으면 그 내용을 반영한 새 버전의 PRD 문서(Markdown)를 생성

### 2차에서 만든 것 (구현 완료)

- 변경 자동 감지 (새 버전 문서와 이전 버전을 비교해 추가·변경·삭제 감지). 감지된 변경에도 요청자·수정자를 필수로 받는다
- 웹 UI (브라우저에서 같은 기능을 쓴다. 아래 가정의 "웹 UI" 참고)

### 하지 않을 것 (범위 밖)

- 로그인과 권한(역할별 구분 포함)
- 승인 워크플로
- 엑셀, 피피티, 지라 같은 다른 입력 형식
- 알림
- 요구사항 간 의존 관계 분석과 영향도 분석

핵심 기능 구현을 먼저 목표로 하고, 위 항목은 그 뒤에 다룬다.

## 제약 조건 (Constraints)

- Markdown(.md) 문서만 입력으로 지원한다
- 구조화 데이터(documents, chunks, requirements, requirement_history 등 목적별 테이블)는 SQLite에 저장한다
- 임베딩 저장·의미 검색은 Chroma의 새 컬렉션을 사용하며, practices/day*_practice/의 chroma_db 및 sds_policies 컬렉션은 사용·수정하지 않는다
- LLM은 Bedrock `global.anthropic.claude-sonnet-4-5-20250929-v1:0`, 리전 `us-east-1`, temperature 0
- 폴백(fallback) LLM은 Bedrock `global.anthropic.claude-haiku-4-5-20251001-v1:0`을 쓴다 (리전, temperature는 기본 LLM과 동일)
- 임베딩은 Bedrock `amazon.titan-embed-text-v2:0`, 리전 `us-east-1`
- AWS 인증은 `.env`(load_dotenv) 방식을 따르며 자격증명 값은 코드/저장소에 넣지 않는다
- 모든 변경(1차는 수동 추가·수정·삭제)에 요청자(requester)와 수정자(modifier)를 필수로 받고, 비어 있으면 저장을 거부한다
- 수동 추가·수정·삭제 이력에는 수동/자동 구분을 기록한다
- 수동으로 수정된 부분이 있으면 반영된 새 버전의 PRD 문서를 Markdown으로 생성한다. 원본 입력 문서는 수정하지 않고 새 파일로 만든다
- 새 버전 문서에서 수정되지 않은 요구사항은 이전 버전과 내용이 같아야 한다
- 1단계는 터미널 대화형 에이전트, 이후 웹 UI를 추가하므로 핵심 로직은 UI와 분리한다
- 새 코드는 프로젝트 루트에 구현하고 practices/day*_practice/ 폴더는 참고용으로만 두며 수정하지 않는다
- 응답 속도는 요구사항이 아니다. 정확한 출처와 답변이 우선이다
- 출처를 찾지 못한 요구사항은 지어내지 않고 "찾지 못했다"고 알린 뒤, 사용자가 대안을 고르도록 안내한다

## 가정 (Assumptions)

- 진입점은 루트 `main.py`이며 인자 없이 `python main.py`로 에이전트 대화(chat)만 시작한다. 기능별 명령과 `--db`, `--chroma` 옵션은 없고 DB·Chroma 경로는 `config` 기본값을 쓴다
- 기능은 `src/interfaces/tools/tools.py`의 LangChain tool(ingest_document, ask_history, add_requirement, update_requirement, delete_requirement, create_new_version)로 제공하며, `main.py`의 chat이 `src/interfaces/agent/agent.py`의 LangGraph 에이전트(agent 노드 ↔ ToolNode, 대화 기록은 체크포인터가 thread_id별로 보관)로 자연어 입력을 받아 tool을 호출한다. 요청자·수정자는 tool 인자로 필수이고 비면 서비스 계층이 거부한다. 모든 기능은 `chat`(tool)으로만 쓴다
- 코드 구조는 `src/interfaces/`(agent, tools) → `src/core/`(service, ingest, chunking, extraction, qa, manual) → `src/infrastructure/`(db, indexing, llm) 3계층이고 `config/`와 진입점 `main.py`는 루트에 둔다. 의존은 한 방향으로만 흐른다
- SQLite 파일 경로는 `data/requirements.db`, Chroma persist_directory는 `data/chroma_db`, 컬렉션 이름은 `requirements_history`로 확정했다(T6, `config/config.py`)
- Titan 출력 차원은 기본값 1024로 시작한다
- 폴백 LLM은 기본 LLM(Sonnet) 호출이 실패(스로틀링, 오류 등)했을 때만 전환하는 것으로 해석한다. 기본 LLM은 최대 2번(재시도 1회) 시도하고, 그래도 예외가 나면 그 호출만 Haiku로 전환한다(다음 호출은 다시 Sonnet부터). 폴백이 쓰이면 로그를 남기고 CLI가 "폴백 모델(Haiku)로 답했습니다"라고 알린다. 폴백 중에도 같은 정확도 기준을 적용하는 것으로 하되, Haiku로 질문 세트를 따로 측정하지는 않았다(T14 확정)
- 새 버전 PRD 문서는 사용자가 에이전트에게 요청해 `create_new_version` tool이 실행될 때 생성한다(자동 생성 안 함). 저장 위치는 `data/generated/`, 파일 이름은 `<문서명>v<최신 버전+1>.md`(예: `PRDv2.md`)이며 기존 파일은 덮어쓰지 않는다. 직전 문서와 요구사항이 같으면(수동 변경 없음) 만들지 않는다. "수동으로 수정된 부분"에는 수동 추가·수정·삭제를 모두 포함한다고 해석한다(T12a 확정)
- **저장할 때마다 새 버전 문서 생성(T21 구현):** 문서 입력을 제외한 수동 추가·수정·삭제는 저장 전에 `interrupt()`로 한 번 확인(y/N)을 받고, 확인하면 변경을 저장한 뒤 곧바로 새 버전 PRD 파일을 만든다. 저장할 때마다 새 파일이 생기며(PRDv2, PRDv3, ...) 따로 고르지 않는다. 확인을 거절하면 DB·Chroma·파일 모두 변하지 않는다. 확인은 저장 전에 한 번만 받는다(저장 뒤에 `interrupt()`를 두면 재개 때 tool이 다시 실행되어 저장이 반복되기 때문이며, CLAUDE.md의 interrupt 규칙). 새 버전 생성이 실패해도 이미 저장된 변경은 유지하고 오류만 알린다. 요청 시 직접 만드는 `create_new_version` tool도 그대로 둔다. 형식, 저장 위치, 파일 이름, 덮어쓰기 금지는 T12a 확정 사항을 따른다
- 새 버전 문서의 형식은 입력 문서와 같은 Markdown 구조(요구사항마다 `###` 헤딩)로 가정한다. 수동으로 추가·수정된 요구사항에는 내용 뒤에 `- **요청자:**`, `- **수정자:**`, `- **수정일:**`(변경한 날짜, UTC) 세 줄을 적고, 수동 삭제된 요구사항은 `## 삭제된 요구사항` 섹션에 삭제일·요청자·수정자와 함께 나열한다. 수정하지 않은 요구사항에는 아무것도 붙이지 않아 이전 버전과 같다. 이 문서를 나중에 청킹하면 이 정보가 청크 본문(DB에 저장)과 `Chunk.meta`에 들어가고, 요구사항 추출은 본문에서 이 세 줄을 떼어 내 `change_info`로 따로 돌려준다(DB의 별도 컬럼 저장과 변경 감지 활용은 2차)
- 검색은 하이브리드로 한다(T16). 한국어 형태소 BM25(가중치 0.3)와 Chroma 벡터 검색(0.7)을 가중 RRF로 합치고, 요구사항 청크(REQ-ID가 있는 것)만 대상으로 상위 4개를 근거로 쓴다. `practices/day2_practice/hybrid.py`를 참고했다
- **변경 자동 감지(2차, T22~T25 구현):** 이미 있는 문서 이름의 더 높은 버전(예: PRDv1 뒤의 `PRDv2.md`)을 입력하면 처음 입력이 아니라 변경 감지로 처리한다. 같거나 낮은 버전은 거부한다. 비교 기준은 직전 문서가 아니라 현재 DB의 활성 요구사항(수동 변경 반영)이며, 같은 문서 이름의 요구사항과 직접 추가한 요구사항이 대상이다. 문서의 ID가 현재 활성 요구사항의 ID와 같으면 같은 요구사항으로 보고, 제목이나 내용이 공백을 제외하고 다르면 변경으로 기록한다. 매칭되지 않는 항목은 추가이며, 문서의 ID가 DB에 한 번도 없으면 그 ID를 그대로 쓰고 ID가 없거나 삭제된 ID(다시 쓰지 않는다)와 같으면 다음 번호(삭제된 것 포함 최대 번호 + 1)를 새로 매긴다(새로 매기면 사용자에게 알린다). 문서에 없는 활성 요구사항은 삭제로 기록한다. ID가 없는 문서는 전부 추가로 처리하고 삭제는 감지하지 않는다. 감지된 변경은 `auto` 이력으로, 새 문서를 출처로 기록하며 요청자·수정자는 필수다. 새 문서에 요구사항별 요청자·수정자(`change_info`)가 적혀 있으면 그 값을, 없으면 입력 때 받은 값을 쓴다. 바뀐 것이 없으면 저장하지 않고 알린다. 변경 없는 요구사항에는 이력을 만들지 않고, `requirements.document_id`(출처 문서)는 처음 입력한 문서로 유지한다. Chroma는 변경·추가된 요구사항의 벡터를 교체·추가하고 삭제된 것은 제거하며, 실패하면 SQLite 변경도 되돌린다. 에이전트의 문서 입력은 감지 요약(추가·변경·삭제 항목과 새로 매긴 ID)을 보여 주고 `interrupt()`로 요약 전체에 y/N을 한 번 받은 뒤 저장한다(항목별 선택은 없다, T26). 같은 이름·버전이 시스템이 만든 새 버전 문서(generated)이면 "버전을 올려 입력하라"고 안내하고(T27), 생성된 파일을 더 높은 버전으로 다시 입력하면 바뀐 것이 없다고 알린다. 한 프로세스 안에서 쓰기(입력·수동 변경·새 버전 생성)는 한 번에 하나씩 실행한다
- **변경 이력 질문(T28):** 삭제된 요구사항은 현재 요구사항으로는 답하지 않는다. 다만 질문이 요구사항 ID를 직접 말하거나, 삭제·변경·누가·언제 같은 이력 질문이면 삭제된 요구사항을 키워드(질문과 겹치는 단어 2개 이상)로 찾아 이력 근거로 보여 준다. 삭제된 요구사항의 벡터는 Chroma에서 지우므로 이 경로는 SQLite에서 직접 찾는다. 답변 출처에는 이력의 이전 값·새 값, 문서 버전, 요청자, 수정자, 수동/자동이 붙는다. 정확도는 변경 이력 질문 세트 10개(`tests/test_change_history_qa.py`)로 확인한다
- **웹 UI(2차, T30~T37 구현):** 루트 `src/web/`에 FastAPI 서버(`create_app`)와 정적 HTML/JS 화면이 있다(`main.py`처럼 진입점이라 3계층 밖에 둔다). 로컬에서 서버를 띄워 같은 네트워크의 20~30명이 접속하는 것으로 가정한다(별도 배포 없음, 로그인 없음이라 내부망 전용). 실행은 `python -m uvicorn --factory src.web.app:create_app --host 0.0.0.0 --port 8000`이다. 화면은 채팅(질문·수동 변경·문서 입력), 확인·선택 버튼(`interrupt()`로 멈춘 질문을 버튼으로 보여 주고 `/api/resume`로 답함), 요청자·수정자 입력칸(채팅 메시지에 자동으로 함께 보냄), 문서 올리기, 요구사항 표와 요구사항별 이력, 문서 목록과 새 버전 파일 내려받기로 이루어진다. 기능 로직은 `src/core/service`를 그대로 쓰고 화면 쪽에는 로직이 없다. 대화는 브라우저마다 세션 ID 하나로 이어지며 SQLite 체크포인터(`data/web_sessions.db`)에 저장돼 새로고침·서버 재시작 후에도 남는다. 업로드한 문서는 `data/uploads/`에 저장하고(.md, 최대 1MB, UTF-8, 폴더 경로 제거), 에이전트는 이 폴더의 파일만 입력할 수 있다. 내려받을 수 있는 것은 시스템이 만든 새 버전 문서뿐이다. 같은 대화에서 확인 요청에 답하기 전에는 새 메시지를 받지 않는다(409). 저장과 새 버전 생성은 한 덩어리로 잠가서 여러 명이 동시에 저장해도 저장마다 번호가 다른 새 파일이 생긴다
- 새 패키지: `fastapi`(서버), 이미 있던 `uvicorn`·`python-multipart`(업로드)·`httpx`(테스트)를 `requirements.txt`에 적었다
- **LLM·도구 호출 로그(trace):** `practices/day7_practice/local_tracer.py`를 참고한 콜백(`src/infrastructure/llm/tracer.py`의 `FileTracer`)이 에이전트 실행마다 LLM 호출(사용 모델, 입력 프롬프트, 답변·도구 선택, 토큰 수, 소요 시간, 오류)과 도구 호출(이름, 입력, 출력, 소요 시간, 오류, 확인 대기)을 한 줄에 하나씩 JSON Lines로 `data/logs/llm_trace.jsonl`에 남긴다. 콘솔(`main.py`)과 웹 서버가 같은 설정(`run_config`)을 쓰므로 둘 다 기록되고, 도구 안에서 부르는 질의응답·요구사항 추출 모델 호출도 같은 대화 ID(`thread_id`)로 남는다. 폴백이 쓰이면 오류 기록 뒤에 Haiku 호출이 `model`로 구분되어 보인다. 긴 글은 1,000자에서 자르고, 로그 기록이 실패해도 요청은 계속 처리하며, 요구사항 내용이 들어 있으므로 `data/` 아래에 두어 커밋하지 않는다. `.env`에 `TRACE_ENABLED=0`을 쓰면 끈다(테스트는 항상 끈다)
- **LLM-as-judge(평가 도구, `src/evaluation/judge.py`):** `python src/evaluation/run_eval.py --judge`로 켠다(기본은 꺼짐). 정답셋 평가에서 답을 찾은 답변마다 같은 근거(답변 출처가 가리키는 요구사항과 이력)를 LLM 심사자에게 보여 주고 `supported`(답변 내용이 모두 근거로 뒷받침됨)와 `answers_question`(질문에 실제로 답함)을 판정하게 한다. 키워드·출처의 정확한 대조(`run_eval.judge()`)를 대신하지 않는 보조 수단이다. 출처(문서, 버전, 요청자, 수정자)는 코드가 SQLite에서 만들므로 LLM으로 심사하지 않는다. 심사 결과는 참고용이라 PASS/FAIL과 종료 코드에 영향이 없고(LLM 판정은 실행마다 달라질 수 있다), "확인 필요"로 표시된 문항을 사람이 읽어 본다. "찾지 못했다" 답변은 심사하지 않는다. 심사 모델은 답변 모델과 같은 기본 LLM(Sonnet, 실패 시 Haiku 폴백)이며 문항당 Bedrock 호출이 1회 늘어난다. 서비스 기능(`src/core/`, `src/interfaces/`)이 아니라 개발용 평가 도구이므로 3계층 밖의 `src/evaluation/`에 둔다. 결과는 `src/evaluation/eval_result.json`의 문항별 `llm_judge`와 `eval_history.jsonl`의 `judge`·`judge_flagged`에 남는다. 심사 로직은 가짜 LLM으로 `tests/test_eval_judge.py`가 검증한다(Bedrock 호출 없음)
- **RAGAS 지표(평가 도구, `src/evaluation/ragas_metrics.py`, 1단계):** `python src/evaluation/run_eval.py --ragas`로 켠다(기본은 꺼짐, `--judge`와 함께 쓸 수 있다). 답을 찾은 답변마다 기준 답변이 필요 없는 두 지표를 0~1로 산출한다: `faithfulness`(답변의 진술 중 근거가 뒷받침하는 비율)와 `answer_relevancy`(답변에서 역으로 만든 질문과 실제 질문의 임베딩 유사도, Titan 사용). 근거(`retrieved_contexts`)는 답변 출처가 가리키는 요구사항과 그 이력 행이다(`src/evaluation/judge.py`의 `evidence_list`, 답변 LLM이 본 것과 같은 텍스트). 점수는 참고용이라 PASS/FAIL과 종료 코드에 영향이 없다. 요약에는 지표별 평균과 0.7 미만 문항이 나오고, `eval_result.json`의 문항별 `ragas`와 `eval_history.jsonl`의 `ragas`(평균)에 남는다. `answer_relevancy`는 짧고 정확한 답에도 낮게(예: 0.4) 나올 수 있으므로 절대 기준이 아니라 추세와 이상치를 보는 용도다. Context Precision/Recall은 서술형 기준 답변과 검색된 청크가 필요해(`eval_set.json`에는 키워드·출처만 있고 `ask()`는 검색 결과를 돌려주지 않는다) 넣지 않았다(2단계로 남김). 구현상 주의: ① ragas 0.4.3이 가져오는 `langchain_community.chat_models.vertexai`가 설치된 langchain-community에 없어 가짜 모듈을 등록한 뒤 import한다(Vertex는 쓰지 않는다). ② ragas가 사용 통계를 외부로 보내므로 `RAGAS_DO_NOT_TRACK=true`를 설정한다(요구사항 내용은 Bedrock 밖으로 나가지 않는다). ③ ragas는 LangChain 채팅 모델이 필요해 폴백 없는 Sonnet을 직접 쓴다(스로틀링은 ragas가 재시도하고 그래도 실패하면 해당 지표를 `None`과 오류 문구로 남긴다). 새 패키지 `ragas`를 `requirements.txt`에 적었다(`datasets`, `pandas`, `openai` 등 의존성이 함께 설치된다). 점수 산출은 가짜 지표로 `tests/test_eval_judge.py`가 검증한다(Bedrock 호출 없음)
- 청킹은 Markdown 헤딩 단위를 기본으로 하며 `practices/day*_practice/chunking_md.py`를 참고한다
- "차안"은 "대안"으로 해석한다. 대안은 `ask_history`가 출처를 찾지 못했을 때 LangGraph `interrupt()`로 사용자에게 고르게 하는 3가지(비슷한 요구사항 후보 보기, 질문을 바꿔 다시 검색(최대 3회), 그만두기)로 정했다(T13)
- 사용자 확인은 모두 LangGraph `interrupt()`로 구현한다. 문서 입력·요구사항 추가·수정·삭제 tool은 저장 전에 확인(y/N)을 받고, 거절하면 아무것도 저장하지 않는다. 요청자·수정자가 비면 확인을 묻기 전에 거부한다. 이 확인은 같은 사용자가 하는 실행 확인이며, 다른 사람이 승인하는 승인 워크플로가 아니다
- "20~30명"은 20~30명으로 해석한다
- 요구사항 100개 이상 규모는 `samples/LargePRDv1.md`(요구사항 104개, 가상 문서)로 검증한다(T17). 질문은 3개만 확인했다(T18)
- 정확도 100%는 "샘플 질문 세트 10개 전부 통과"를 1차 기준으로 확정했다. 질문 세트는 `tests/qa_set.py`에 있다(T15)
- 고객이 직접 쓰지만 권한 구분은 범위 밖이므로, 1차에서는 모든 사용자가 같은 기능을 쓴다

## 완료 기준 (Acceptance Criteria, 1차)

| # | 설명 | 검증 명령 | 산출물 |
|---|------|-----------|--------|
| 1 | PRDv1.md를 요청자·수정자와 함께 입력하면 요구사항이 추출되어 저장된다 | `python -m pytest -q tests/test_ingest.py` | `samples/PRDv1.md` |
| 2 | 수동 추가·수정·삭제는 요청자·수정자가 있어야 저장되고, 이력에 수동으로 표시된다 | `python -m pytest -q tests/test_manual_crud.py` | - |
| 3 | 요청자 또는 수정자를 비우면 어떤 변경 경로에서도 저장이 거부된다 | `python -m pytest -q tests/test_required_actors.py` | - |
| 4 | 이력 질문 10개에 출처(문서, 버전, 요청자, 수정자)까지 10개 모두 정확히 답한다 | `python -m pytest -q tests/test_history_qa.py` | - |
| 5 | 출처를 찾지 못한 요구사항을 물으면 "찾지 못했다"고 답하고 대안을 고르도록 안내한다 | `python -m pytest -q tests/test_not_found.py` | - |
| 6 | 요구사항 100개 이상인 문서 한 건을 입력해 추출·저장·질의가 동작한다 | `python -m pytest -q tests/test_scale.py` | - |
| 7 | 수동 수정 후 새 버전 PRD 문서가 생성되고, 수정 내용이 반영되며 수정되지 않은 요구사항은 이전 버전과 같고 원본 문서는 변하지 않는다 | `python -m pytest -q tests/test_new_version.py` | - |

### 2차 완료 기준 (변경 감지, 웹 UI)

- 변경된 PRDv2.md를 입력하면 추가·변경·삭제된 요구사항이 각각 감지되어 이력에 기록되고, 감지된 변경에도 요청자·수정자가 필수로 기록된다 (`python -m pytest -q tests/test_change_detection.py tests/test_change_flow.py`, 샘플 `samples/PRDv2.md`)
- 변경 이력 질문 10개(변경·삭제·추가, 수동/자동)에 출처까지 10개 모두 정확히 답한다 (`python -m pytest -q tests/test_change_history_qa.py`)
- 브라우저에서 문서 올리기, 질문, 수동 변경, 확인 버튼, 새 버전 생성·내려받기를 끝까지 할 수 있고 여러 명이 동시에 저장해도 누락·중복이 없다 (`python -m pytest -q tests/test_web_api.py tests/test_web_concurrency.py`)

## 도메인 모델 (Ontology: RequirementHistory)

요구사항 문서, 청크, 요구사항, 변경 이력의 도메인 모델입니다.

| 필드 | 타입 | 설명 |
|------|------|------|
| document | object | Markdown 문서와 버전(PRDv1, PRDv2 등). 수동 수정 후 생성된 새 버전 문서도 포함 |
| chunk | object | 문서를 헤딩 단위로 나눈 조각. 임베딩 대상 |
| requirement | object | 추출된 요구사항(ID, 내용, 현재 상태) |
| history_entry | object | 추가/수정/삭제 이력. 이전 값, 새 값, 시각, 출처, 수동/자동 구분 |
| requester | string | 변경을 요청한 사람. 필수 |
| modifier | string | 변경을 수행한 사람. 필수 |

## 평가 원칙 (Evaluation Principles)

| 이름 | 설명 | 가중치 |
|------|------|--------|
| source_accuracy | 답변의 출처(문서, 버전, 요청자, 수정자)가 정확하다 | 0.3 |
| traceability | 모든 이력은 문서·버전·요청자·수정자로 추적 가능해야 한다 | 0.3 |
| honest_not_found | 출처를 모르면 지어내지 않고 모른다고 알리며 대안을 안내한다 | 0.15 |
| separation | 핵심 로직이 대화 진입점·에이전트와 분리되어 웹 UI 확장이 가능하다 | 0.15 |
| isolation | practices/day*_practice/와 기존 Chroma 데이터를 건드리지 않는다 | 0.1 |

## 종료 조건 (Exit Conditions)

- **all_scenarios_pass:** 1차 완료 기준 7개 시나리오가 모두 통과한다

## 참고 자료 (References)

- **참고 경로:** `practices/day*_practice/` — Bedrock LLM/임베딩, Chroma, Markdown 청킹 사용 예시. 참고용이며 수정하지 않음
- **기존 패턴:**
  - LangChain(`langchain_aws`, `langchain_chroma`) 사용
  - `load_dotenv()`로 `.env` 인증 로드
- **기존 의존성:** `langchain_aws`, `langchain_chroma`, `python-dotenv`
