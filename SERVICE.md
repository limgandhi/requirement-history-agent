# requirement-history-agent

## 목표 (Goal)

Bedrock(Claude, Titan)과 SQLite+Chroma로, Markdown 요구사항 문서에서 요구사항을 추출하고 요청자·수정자를 필수로 기록하며 이력을 질의응답하고, 수동 수정이 있으면 반영된 새 버전 PRD 문서를 생성하는 CLI 에이전트를 새로 만든다. 변경 자동 감지와 웹 UI는 1차 이후에 확장한다.

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

### 뒤로 미룰 것 (2차 이후)

- 변경 자동 감지 (새 버전 문서와 이전 버전을 비교해 추가·변경·삭제 감지). 감지된 변경에도 요청자·수정자를 필수로 받는다
- 웹 UI

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
- 임베딩 저장·의미 검색은 Chroma의 새 컬렉션을 사용하며, day*_practice/의 chroma_db 및 sds_policies 컬렉션은 사용·수정하지 않는다
- LLM은 Bedrock `global.anthropic.claude-sonnet-4-5-20250929-v1:0`, 리전 `us-east-1`, temperature 0
- 폴백(fallback) LLM은 Bedrock `global.anthropic.claude-haiku-4-5-20251001-v1:0`을 쓴다 (리전, temperature는 기본 LLM과 동일)
- 임베딩은 Bedrock `amazon.titan-embed-text-v2:0`, 리전 `us-east-1`
- AWS 인증은 `.env`(load_dotenv) 방식을 따르며 자격증명 값은 코드/저장소에 넣지 않는다
- 모든 변경(1차는 수동 추가·수정·삭제)에 요청자(requester)와 수정자(modifier)를 필수로 받고, 비어 있으면 저장을 거부한다
- 수동 추가·수정·삭제 이력에는 수동/자동 구분을 기록한다
- 수동으로 수정된 부분이 있으면 반영된 새 버전의 PRD 문서를 Markdown으로 생성한다. 원본 입력 문서는 수정하지 않고 새 파일로 만든다
- 새 버전 문서에서 수정되지 않은 요구사항은 이전 버전과 내용이 같아야 한다
- 1단계는 CLI, 이후 웹 UI를 추가하므로 핵심 로직은 UI와 분리한다
- 새 코드는 프로젝트 루트에 구현하고 day*_practice/ 폴더는 참고용으로만 두며 수정하지 않는다
- 응답 속도는 요구사항이 아니다. 정확한 출처와 답변이 우선이다
- 출처를 찾지 못한 요구사항은 지어내지 않고 "찾지 못했다"고 알린 뒤, 사용자가 대안을 고르도록 안내한다

## 가정 (Assumptions)

- CLI 진입점 이름, 하위 명령어(ingest/add/update/delete/ask 등) 표기는 구현 시 정한다
- SQLite 파일 경로와 새 Chroma 컬렉션/persist_directory 이름은 구현 시 정한다(예: `data/requirements.db`, `data/chroma_db`, 컬렉션 `requirements_history`)
- Titan 출력 차원은 기본값 1024로 시작한다
- 폴백 LLM은 기본 LLM(Sonnet) 호출이 실패(스로틀링, 오류 등)했을 때만 전환하는 것으로 해석한다. 전환 조건과 재시도 횟수는 구현 시 정한다
- 새 버전 PRD 문서의 생성 시점(수동 변경마다 자동 / 사용자가 명령으로 요청), 저장 위치, 파일 이름 규칙(예: `PRDv2.md`처럼 버전 번호를 하나 올림)은 구현 시 정한다. "수동으로 수정된 부분"에는 수동 추가·수정·삭제를 모두 포함한다고 해석한다
- 새 버전 문서의 형식은 입력 문서와 같은 Markdown 구조(요구사항마다 `###` 헤딩)로 가정한다
- 청킹은 Markdown 헤딩 단위를 기본으로 하며 `day*_practice/chunking_md.py`를 참고한다
- "차안"은 "대안"으로 해석한다. 대안의 종류(비슷한 요구사항 후보, 다른 버전 확인, 직접 입력 등)는 구현 시 정한다
- "20~30명"은 20~30명으로 해석한다
- 요구사항 100개 이상 규모를 검증할 큰 샘플은 별도로 만든다. 현재 샘플은 8~9개뿐이다
- 정확도 100%는 "샘플 질문 세트 10개 전부 통과"를 1차 기준으로 두고, 구현 전에 다시 확인한다
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

### 2차 이후 완료 기준 (변경 감지)

- 변경된 PRDv2.md를 입력하면 추가·변경·삭제된 요구사항이 각각 감지되어 이력에 기록되고, 감지된 변경에도 요청자·수정자가 필수로 기록된다 (`tests/test_change_detection.py`, 샘플 `samples/PRDv2.md`)

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
| separation | 핵심 로직이 CLI와 분리되어 웹 UI 확장이 가능하다 | 0.15 |
| isolation | day*_practice/와 기존 Chroma 데이터를 건드리지 않는다 | 0.1 |

## 종료 조건 (Exit Conditions)

- **all_scenarios_pass:** 1차 완료 기준 7개 시나리오가 모두 통과한다

## 참고 자료 (References)

- **참고 경로:** `day*_practice/` — Bedrock LLM/임베딩, Chroma, Markdown 청킹 사용 예시. 참고용이며 수정하지 않음
- **기존 패턴:**
  - LangChain(`langchain_aws`, `langchain_chroma`) 사용
  - `load_dotenv()`로 `.env` 인증 로드
- **기존 의존성:** `langchain_aws`, `langchain_chroma`, `python-dotenv`
