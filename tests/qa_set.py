"""T15 question set over samples/PRDv1.md (ingested with requester=lee, modifier=kim).

Each item: question, the requirement that must appear in the sources, and keyword
groups the answer text must contain (all groups required; any word in a group is enough).
"""
REQUESTER, MODIFIER = "lee", "kim"
DOC_NAME, DOC_VERSION = "PRD", 1

QUESTIONS = [
    {"q": "투표 기간은 최대 며칠인가?", "req": "REQ-003", "keywords": [["7일"]]},
    {"q": "비밀번호는 어떻게 저장하나?", "req": "REQ-001", "keywords": [["암호화"]]},
    {"q": "초대받지 않은 사용자도 투표에 참여할 수 있나?", "req": "REQ-002", "keywords": [["초대"]]},
    {"q": "투표를 반복하도록 설정할 수 있는 주기는 무엇인가?", "req": "REQ-004", "keywords": [["매주"], ["매월"]]},
    {"q": "한 사람이 같은 투표에 몇 번 참여할 수 있나?", "req": "REQ-005",
     "keywords": [["한 번", "한번", "1번", "1회"]]},
    {"q": "투표 마감 알림은 마감 얼마 전에 보내나?", "req": "REQ-006", "keywords": [["24시간"]]},
    {"q": "투표 결과는 어떤 형태로 보여 주나?", "req": "REQ-007", "keywords": [["막대"]]},
    {"q": "지난 투표에서 조회할 수 있는 항목은 무엇인가?", "req": "REQ-008",
     "keywords": [["안건"], ["기간"], ["결과"]]},
    {"q": "정기 투표 생성 요구사항(REQ-003)은 누가 요청하고 누가 수정했나?", "req": "REQ-003",
     "keywords": [["lee"], ["kim"]]},
    {"q": "관리자가 구성원을 조직에 추가하는 방법은?", "req": "REQ-002", "keywords": [["이메일"]]},
]
