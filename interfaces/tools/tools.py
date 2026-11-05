"""LangChain tools over app.service. Every tool is a thin wrapper: no business logic.

Errors are returned as text (not raised) so the agent can relay them to the user.
"""
from langchain_core.tools import tool
from langgraph.errors import GraphInterrupt
from langgraph.types import interrupt

from core.service import service
from core.manual.new_version import DEFAULT_OUTPUT_DIR
from core.qa.alternatives import MAX_ROUNDS, OPTIONS
from core.qa.qa import format_answer

CANCELED = "취소했습니다. 아무것도 저장하지 않았습니다."


def _confirm(message: str) -> bool:
    """Pause the graph until the user answers yes/no (resume value is a bool)."""
    return interrupt({"kind": "confirm", "message": message}) is True


SAVE, SAVE_NEW_VERSION, CANCEL = "save", "save_new_version", "cancel"
SAVE_OPTIONS = [
    {"id": SAVE, "label": "저장만"},
    {"id": SAVE_NEW_VERSION, "label": "저장하고 새 버전 문서도 만들기"},
    {"id": CANCEL, "label": "취소"},
]


def _choose_save(message: str) -> str:
    """One interrupt before saving: save / save + new version / cancel.

    Asked before the change, never after: a pause after saving would re-run the
    tool on resume and save twice. Anything unexpected counts as cancel.
    """
    choice = interrupt({"kind": "choice", "message": message, "options": SAVE_OPTIONS})
    return choice if choice in (SAVE, SAVE_NEW_VERSION) else CANCEL


def build_tools(db_path, chroma_dir, llm=None, store=None, output_dir=DEFAULT_OUTPUT_DIR) -> list:
    def guarded(fn):
        try:
            return fn()
        except (ValueError, OSError) as e:
            return f"오류: {e}"

    def finish(done: str, choice: str) -> str:
        """After the change is saved: also write the new-version PRD when the user chose so."""
        if choice != SAVE_NEW_VERSION:
            return done
        try:
            r = service.create_new_version(db_path, output_dir)
        except (ValueError, OSError) as e:  # the saved change stays; only the new file failed
            return f"{done}\n새 버전 문서는 만들지 못했습니다(변경은 저장됨): {e}"
        return f"{done}\n새 버전 생성: {r.path} ({r.name} v{r.version}, 요구사항 {r.requirement_count}개)"

    @tool
    def ingest_document(path: str, requester: str, modifier: str) -> str:
        """Markdown(.md) 요구사항 문서를 입력해 요구사항을 추출·저장한다.
        requester(요청자)와 modifier(수정자)는 반드시 사용자가 말한 값을 쓴다. 없으면 호출하지 말고 사용자에게 묻는다."""
        def run():
            service.check_actors(requester, modifier)
            if not _confirm(f"문서 '{path}'를 입력합니다 (요청자 {requester}, 수정자 {modifier}). 진행할까요?"):
                return CANCELED
            r = service.ingest_and_index(db_path, chroma_dir, path, requester, modifier, llm=llm, store=store)
            return f"입력 완료: {r.name}v{r.version} (청크 {r.chunk_count}, 요구사항 {r.requirement_count})"
        return guarded(run)

    # An interrupt re-runs the tool from its start on resume, so the LLM answer of a
    # question is kept here until the tool finishes (no second Bedrock call).
    answers: dict = {}

    def ask_once(question: str):
        if question not in answers:
            answers[question] = service.ask_question(db_path, chroma_dir, question, llm=llm, store=store)
        return answers[question]

    def ask_with_alternatives(question: str) -> str:
        for round_no in range(MAX_ROUNDS):
            answer = ask_once(question)
            if answer.found:
                return format_answer(answer)
            choice = interrupt({
                "kind": "choice",
                "message": f"{answer.text}\n대안을 선택하세요.",
                "options": OPTIONS,
            })
            if choice == "similar":
                return answer.text + "\n" + service.find_similar_requirements(
                    db_path, chroma_dir, question, store=store)
            if choice != "rephrase":
                return answer.text + "\n대안을 선택하지 않아 여기서 마칩니다."
            question = interrupt({"kind": "input", "message": "새 질문을 입력하세요."})
        return answer.text + "\n다시 검색해도 찾지 못했습니다."

    @tool
    def ask_history(question: str) -> str:
        """요구사항과 변경 이력에 대한 질문에 출처(문서, 버전, 요청자, 수정자)와 함께 답한다.
        출처를 찾지 못하면 사용자에게 대안을 고르게 한다(자동으로 진행됨).
        결과 문자열을 그대로 사용자에게 전달하고, 출처를 덧붙이거나 지어내지 않는다."""
        try:
            out = guarded(lambda: ask_with_alternatives(question))
        except GraphInterrupt:
            raise  # waiting for the user: keep the cached answer
        answers.clear()
        return out

    @tool
    def add_requirement(title: str, content: str, requester: str, modifier: str) -> str:
        """요구사항 하나를 수동으로 추가한다. ID는 자동 부여된다.
        requester와 modifier는 사용자가 말한 값만 쓰고, 없으면 호출하지 말고 묻는다."""
        def run():
            service.check_actors(requester, modifier)
            choice = _choose_save(f"요구사항을 추가합니다.\n제목: {title}\n내용: {content}\n"
                                  f"요청자 {requester}, 수정자 {modifier}.")
            if choice == CANCEL:
                return CANCELED
            key = service.add_requirement_manual(
                db_path, chroma_dir, title, content, requester, modifier, store=store)
            return finish(f"추가 완료: {key}", choice)
        return guarded(run)

    @tool
    def update_requirement(req_key: str, content: str, requester: str, modifier: str) -> str:
        """기존 요구사항(예: REQ-003)의 내용을 수동으로 수정한다.
        requester와 modifier는 사용자가 말한 값만 쓰고, 없으면 호출하지 말고 묻는다."""
        def run():
            service.check_actors(requester, modifier)
            choice = _choose_save(f"{req_key}의 내용을 다음으로 수정합니다.\n{content}\n"
                                  f"요청자 {requester}, 수정자 {modifier}.")
            if choice == CANCEL:
                return CANCELED
            service.update_requirement_manual(db_path, chroma_dir, req_key, content, requester, modifier, store=store)
            return finish(f"수정 완료: {req_key}", choice)
        return guarded(run)

    @tool
    def delete_requirement(req_key: str, requester: str, modifier: str) -> str:
        """요구사항(예: REQ-006)을 수동으로 삭제 처리한다. 되돌릴 수 없으므로 사용자가 삭제를 분명히 요청했을 때만 호출한다.
        requester와 modifier는 사용자가 말한 값만 쓰고, 없으면 호출하지 말고 묻는다."""
        def run():
            service.check_actors(requester, modifier)
            choice = _choose_save(f"{req_key}를 삭제합니다 (요청자 {requester}, 수정자 {modifier}).")
            if choice == CANCEL:
                return CANCELED
            service.delete_requirement_manual(db_path, chroma_dir, req_key, requester, modifier, store=store)
            return finish(f"삭제 완료: {req_key}", choice)
        return guarded(run)

    @tool
    def create_new_version() -> str:
        """수동 변경을 반영한 새 버전 PRD Markdown 파일을 새로 생성한다(원본은 수정하지 않음)."""
        def run():
            r = service.create_new_version(db_path, output_dir)
            return f"새 버전 생성: {r.path} ({r.name} v{r.version}, 요구사항 {r.requirement_count}개)"
        return guarded(run)

    return [ingest_document, ask_history, add_requirement, update_requirement,
            delete_requirement, create_new_version]
