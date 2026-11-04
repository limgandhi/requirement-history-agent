"""LangChain tools over app.service. Every tool is a thin wrapper: no business logic.

Errors are returned as text (not raised) so the agent can relay them to the user.
"""
from langchain_core.tools import tool

from core.service import service
from core.manual.new_version import DEFAULT_OUTPUT_DIR
from core.qa.qa import format_answer


def build_tools(db_path, chroma_dir, llm=None, store=None, output_dir=DEFAULT_OUTPUT_DIR) -> list:
    def guarded(fn):
        try:
            return fn()
        except (ValueError, OSError) as e:
            return f"오류: {e}"

    @tool
    def ingest_document(path: str, requester: str, modifier: str) -> str:
        """Markdown(.md) 요구사항 문서를 입력해 요구사항을 추출·저장한다.
        requester(요청자)와 modifier(수정자)는 반드시 사용자가 말한 값을 쓴다. 없으면 호출하지 말고 사용자에게 묻는다."""
        def run():
            r = service.ingest_and_index(db_path, chroma_dir, path, requester, modifier, llm=llm, store=store)
            return f"입력 완료: {r.name}v{r.version} (청크 {r.chunk_count}, 요구사항 {r.requirement_count})"
        return guarded(run)

    @tool
    def ask_history(question: str) -> str:
        """요구사항과 변경 이력에 대한 질문에 출처(문서, 버전, 요청자, 수정자)와 함께 답한다.
        결과 문자열을 그대로 사용자에게 전달하고, 출처를 덧붙이거나 지어내지 않는다."""
        return guarded(lambda: format_answer(
            service.ask_question(db_path, chroma_dir, question, llm=llm, store=store)))

    @tool
    def add_requirement(title: str, content: str, requester: str, modifier: str) -> str:
        """요구사항 하나를 수동으로 추가한다. ID는 자동 부여된다.
        requester와 modifier는 사용자가 말한 값만 쓰고, 없으면 호출하지 말고 묻는다."""
        return guarded(lambda: "추가 완료: " + service.add_requirement_manual(
            db_path, chroma_dir, title, content, requester, modifier, store=store))

    @tool
    def update_requirement(req_key: str, content: str, requester: str, modifier: str) -> str:
        """기존 요구사항(예: REQ-003)의 내용을 수동으로 수정한다.
        requester와 modifier는 사용자가 말한 값만 쓰고, 없으면 호출하지 말고 묻는다."""
        def run():
            service.update_requirement_manual(db_path, chroma_dir, req_key, content, requester, modifier, store=store)
            return f"수정 완료: {req_key}"
        return guarded(run)

    @tool
    def delete_requirement(req_key: str, requester: str, modifier: str) -> str:
        """요구사항(예: REQ-006)을 수동으로 삭제 처리한다. 되돌릴 수 없으므로 사용자가 삭제를 분명히 요청했을 때만 호출한다.
        requester와 modifier는 사용자가 말한 값만 쓰고, 없으면 호출하지 말고 묻는다."""
        def run():
            service.delete_requirement_manual(db_path, chroma_dir, req_key, requester, modifier, store=store)
            return f"삭제 완료: {req_key}"
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
