# 웹 API 명세

`src/web/app.py` (FastAPI, `create_app` 팩토리) 기준. 로그인·권한은 없다.

```
python -m uvicorn --factory src.web.app:create_app --host 0.0.0.0 --port 8000
```

- Base URL: `http://<host>:8000`
- 요청·응답: JSON (`/api/upload`만 `multipart/form-data`)
- 요구사항 추가·수정·삭제의 요청자(`requester`)·수정자(`modifier`)는 별도 필드가 아니라 **대화 메시지에 담아** 보낸다. 비어 있으면 에이전트가 저장을 거부한다.
- 서버가 자동 생성하는 OpenAPI 문서: `/docs`(Swagger UI), `/openapi.json`

## 공통

### 세션 ID
`session_id`는 정규식 `^[A-Za-z0-9_-]{8,64}$`. 대화 기록은 이 ID를 키로 SQLite 체크포인터(`data/web_sessions.db`)에 저장되므로 새로고침·서버 재시작 후에도 이어진다. 같은 세션의 요청은 서버에서 한 번에 하나씩만 처리한다.

### 오류 응답
```json
{ "error": "메시지" }
```
`HTTPException`이 낸 오류는 FastAPI 기본 형식 `{ "detail": "메시지" }`로 온다.

| 상태 | 의미 |
|---|---|
| 400 | 값 오류(`ValueError`), 업로드 검증 실패, 답변 누락 |
| 404 | 내려받을 문서 없음 |
| 409 | 세션 상태와 요청이 맞지 않음 |
| 422 | 요청 형식 오류(세션 ID, 요구사항 ID, 본문 스키마) |
| 500 | 예기치 못한 오류 (`{"error": "처리 중 오류가 났습니다: <예외명>"}`, 스택 트레이스 미노출) |

### Pending 질문 객체
에이전트가 `interrupt()`로 멈추면 응답의 `pending`에 아래 객체의 배열이 담긴다. 여러 tool이 동시에 멈출 수 있으므로 배열이다.

| 필드 | 타입 | 설명 |
|---|---|---|
| `id` | string | interrupt ID. `/api/resume`의 `answers` 키 |
| `kind` | `"confirm"` \| `"choice"` \| `"input"` | 질문 종류 |
| `message` | string | 사용자에게 보여 줄 문구 |
| `options` | `{id, label}[]` | `choice`일 때만 |

`kind`별 답변 값:

| kind | answers 값 | 비고 |
|---|---|---|
| `confirm` | `true` / `false` | `true`만 실행(저장), 그 외는 취소 |
| `choice` | 옵션 `id` 문자열 | 출처를 못 찾았을 때: `similar`(비슷한 요구사항 후보), `rephrase`(질문 바꿔 재검색), `cancel`(그만두기) |
| `input` | 문자열 | `rephrase` 선택 뒤의 새 질문 |

---

## 대화

### `POST /api/chat`
메시지 한 건을 에이전트에 보낸다.

요청
```json
{ "session_id": "abc12345", "message": "REQ-003 투표 기간이 언제 바뀌었어?" }
```
| 필드 | 제약 |
|---|---|
| `session_id` | 세션 ID 규칙 |
| `message` | 1~4000자 |

응답 `200`
```json
{ "reply": "답변 텍스트(출처 포함)", "pending": [] }
```
확인이 필요하면 `reply`는 `null`, `pending`에 질문이 담긴다.
```json
{
  "reply": null,
  "pending": [
    { "id": "…", "kind": "confirm", "message": "REQ-011을 추가할까요? (요청자: …, 수정자: …)" }
  ]
}
```
오류: `409` 이전 확인 요청에 아직 답하지 않음 · `422` 형식 오류

### `POST /api/resume`
대기 중인 질문에 답해 에이전트를 재개한다. **대기 중인 모든 질문**에 한 번에 답해야 한다.

요청
```json
{ "session_id": "abc12345", "answers": { "<interrupt id>": true } }
```
응답 `200`: `/api/chat`과 같은 `{ reply, pending }` (답변 뒤 또 확인이 필요하면 `pending`이 다시 채워진다).

오류: `409` 답할 확인 요청이 없음 · `400` `answers`의 키가 대기 중인 질문 ID와 다름 · `422` 형식 오류

### `GET /api/session/{session_id}`
대화 내역과 대기 중인 질문을 복원한다(페이지 새로고침용).

응답 `200`
```json
{
  "messages": [
    { "role": "user",  "text": "…" },
    { "role": "agent", "text": "…" }
  ],
  "pending": []
}
```
`role`은 `user` \| `agent`. tool 호출·빈 메시지는 제외한다.
오류: `422` 세션 ID 형식 오류

---

## 문서

### `POST /api/upload`
Markdown 문서를 `data/uploads/`에 저장한다. 저장만 하며 요구사항 추출은 이후 대화(문서 입력 요청 + 확인)로 한다.

요청: `multipart/form-data`, 필드 `file`

| 제약 | 값 |
|---|---|
| 파일명 | `^[\w가-힣 ().\-]{1,100}\.md$` (폴더 부분은 버림) |
| 크기 | 최대 1MB |
| 인코딩 | UTF-8 |

응답 `200`
```json
{ "path": "…/data/uploads/PRDv2.md", "name": "PRDv2.md", "document": "PRD", "version": "v2" }
```
`document`·`version`은 파일명에서 파싱한 값. 오류: `400` 파일명·크기·인코딩 위반

### `GET /api/documents`
입력 문서와 시스템이 생성한 새 버전 문서 목록(최신순).

응답 `200`
```json
[
  { "id": 3, "name": "PRD", "version": "v3", "kind": "generated",
    "source_path": "data/generated/PRDv3.md", "created_at": "2026-11-05 01:23:45" }
]
```
`kind`: `input`(입력 문서) \| `generated`(생성된 새 버전)

### `GET /api/files/{document_id}`
시스템이 생성한 새 버전 PRD(`kind = generated`)를 Markdown 파일로 내려받는다. 입력 문서는 내려받을 수 없다.

- 응답 `200`: `text/markdown`, `Content-Disposition` 파일명 포함
- 오류: `404` 해당 ID가 없거나 생성 문서가 아님

---

## 요구사항·이력

### `GET /api/requirements`
전체 요구사항(활성 먼저, 삭제 포함).

응답 `200`
```json
[
  { "req_key": "REQ-003", "title": "투표 기간", "content": "…",
    "status": "active", "updated_at": "2026-11-05 01:23:45" }
]
```
`status`: `active` \| `deleted`

### `GET /api/requirements/{req_key}/history`
요구사항 한 건의 변경 이력(오래된 것부터). `req_key`는 `^REQ-\d+$`(대소문자 무시).

응답 `200`
```json
[
  { "action": "update", "source_type": "manual",
    "old_value": "7일", "new_value": "14일",
    "requester": "기획자 A", "modifier": "개발자 B",
    "created_at": "2026-11-05 01:23:45",
    "document": "PRD", "version": "v2" }
]
```
| 필드 | 값 |
|---|---|
| `action` | `add` \| `update` \| `delete` |
| `source_type` | `manual`(수동) \| `auto`(자동 추출·감지) |
| `document` | 출처 문서 이름. 문서 없이 수동 입력한 경우 `"수동 입력"` |
| `version` | 출처 문서 버전. 없으면 `null` |

`old_value`·`new_value`·`requester`·`modifier`는 `null`일 수 있다(예: 추가 시 `old_value`). 없는 요구사항이면 빈 배열. 오류: `422` 요구사항 ID 형식 오류

---

## 기타

| 경로 | 설명 |
|---|---|
| `GET /` | 웹 화면(`src/web/static/index.html`) |
| `GET /static/*` | 정적 파일 |

## 호출 예 (curl)

```bash
SID=demo-session-01

# 질문
curl -s localhost:8000/api/chat -H 'Content-Type: application/json' \
  -d "{\"session_id\":\"$SID\",\"message\":\"REQ-003 변경 이력 알려줘\"}"

# 확인 요청에 답하기
curl -s localhost:8000/api/resume -H 'Content-Type: application/json' \
  -d "{\"session_id\":\"$SID\",\"answers\":{\"<pending[0].id>\":true}}"
```
