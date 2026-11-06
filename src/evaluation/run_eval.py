"""run_eval.py - 개발 사이클마다 돌리는 정답셋 기반 평가.

    python evaluation/run_eval.py                 # 전체
    python evaluation/run_eval.py --only hist     # id가 hist로 시작하는 문항만
    python evaluation/run_eval.py --no-history    # eval_history.jsonl에 기록하지 않음
    python evaluation/run_eval.py --judge         # 답변 문장을 LLM 심사자도 평가(참고용, 통과 여부에는 영향 없음)
    python evaluation/run_eval.py --ragas         # RAGAS faithfulness·answer_relevancy 점수 산출(참고용)

eval_set.json의 각 문항을 실제 Bedrock으로 질의응답(core.qa.ask)해 기대값과 비교하고
eval_result.json(문항별 결과)과 eval_history.jsonl(실행 한 줄 요약, 추세 확인용)에 남긴다.
직전 실행과 비교해 새로 틀린 문항(회귀)과 고쳐진 문항을 알려 준다.

시나리오(scenario)별로 임시 DB·Chroma에 데이터를 한 번씩 만들어 쓰므로 data/는 건드리지 않는다.
  v1      : samples/PRDv1.md 입력 (lee/kim)
  history : v1 -> PRDv2 자동 감지(park/choi) -> 수동 수정·삭제·추가(han/seo)

문항 기대값(expected):
  found     : 답을 찾았는가 (no_answer 문항은 false여야 통과)
  keywords  : 답변에 들어가야 할 단어 묶음 목록. 모든 묶음에서 하나 이상 있어야 통과
  source    : 출처 중 하나가 아래 항목을 모두 만족해야 통과 (적은 항목만 비교)
              req_key, action, source_type, requester, modifier, document, version
"""
import argparse
import json
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.core.change_detection.changes import ingest_new_version  # noqa: E402
from src.core.ingest.ingest import ingest_document  # noqa: E402
from src.core.manual.manual import add_requirement, delete_requirement, update_requirement  # noqa: E402
from src.core.qa.qa import ask  # noqa: E402
from src.evaluation.judge import evidence_for, evidence_list, judge_answer  # noqa: E402
from src.infrastructure.db.db import connect, init_schema  # noqa: E402
from src.infrastructure.indexing.indexing import get_vectorstore, index_document  # noqa: E402
from src.infrastructure.llm.llm import fallback_count  # noqa: E402

HERE = Path(__file__).resolve().parent
EVAL_SET = HERE / "eval_set.json"
EVAL_RESULT = HERE / "eval_result.json"
EVAL_HISTORY = HERE / "eval_history.jsonl"
SAMPLES = ROOT / "samples"

# Source field -> attribute on core.qa.qa.Source
_SOURCE_FIELDS = {"req_key": "req_key", "action": "action", "source_type": "source_type",
                  "requester": "requester", "modifier": "modifier",
                  "document": "document_name", "version": "version"}


def build_v1(base: Path):
    conn = connect(base / "v1.db")
    init_schema(conn)
    r = ingest_document(conn, SAMPLES / "PRDv1.md", "lee", "kim")
    store = get_vectorstore(persist_dir=base / "v1_chroma")
    index_document(conn, r.document_id, store)
    return conn, store


def build_history(base: Path):
    conn = connect(base / "history.db")
    init_schema(conn)
    r1 = ingest_document(conn, SAMPLES / "PRDv1.md", "lee", "kim")
    store = get_vectorstore(persist_dir=base / "history_chroma")
    index_document(conn, r1.document_id, store)
    ingest_new_version(conn, SAMPLES / "PRDv2.md", "park", "choi", store=store)
    update_requirement(conn, "REQ-004", "관리자는 투표를 매주, 매월 또는 매분기 반복하도록 설정할 수 있어야 한다.",
                       "han", "seo", store=store)
    delete_requirement(conn, "REQ-008", "han", "seo", store=store)
    add_requirement(conn, "투표 공유", "관리자는 투표 링크를 공유할 수 있어야 한다.", "han", "seo", store=store)
    return conn, store


SCENARIOS = {"v1": build_v1, "history": build_history}


def judge(case: dict, answer) -> tuple[bool, str]:
    """(passed, detail) of one answer against the case's expected values."""
    exp = case["expected"]
    if not exp.get("found", True):
        return (not answer.found,
                "찾지 못했다고 답함" if not answer.found else f"찾지 못해야 하는데 답함: {answer.text}")
    if not answer.found:
        return False, f"찾지 못함: {answer.text}"
    for group in exp.get("keywords", []):
        if not any(w in answer.text for w in group):
            return False, f"답변에 {group} 없음: {answer.text}"
    want = {k: v for k, v in exp.get("source", {}).items() if k in _SOURCE_FIELDS}
    if want:
        def match(s):
            return all(getattr(s, _SOURCE_FIELDS[k]) == v for k, v in want.items())
        if not any(match(s) for s in answer.sources):
            got = sorted({tuple(getattr(s, a) for a in _SOURCE_FIELDS.values()) for s in answer.sources},
                         key=str)
            return False, f"출처에 {want} 없음 (받은 출처 {list(_SOURCE_FIELDS)}: {got})"
    return True, "답변·출처 일치"


def llm_judge(llm, conn, question: str, answer) -> dict:
    """Advisory LLM verdict on the answer text (see evaluation/judge.py). Never raises."""
    try:
        v = judge_answer(llm, question, answer.text, evidence_for(conn, answer))
        return {"ok": v.ok, "supported": v.supported, "answers_question": v.answers_question, "reason": v.reason}
    except Exception as e:
        return {"ok": None, "reason": f"[심사 오류] {type(e).__name__}: {e}"}


def run(cases: list[dict], use_judge: bool = False, use_ragas: bool = False) -> list[dict]:
    results = []
    judge_model = None
    if use_judge:
        from src.infrastructure.llm.llm import get_llm

        judge_model = get_llm()
    scorer = None
    if use_ragas:
        from src.evaluation.ragas_metrics import RagasScorer  # imported only when asked: ragas is slow to import

        scorer = RagasScorer()
    with tempfile.TemporaryDirectory(prefix="req_eval_", ignore_cleanup_errors=True) as tmp:  # Windows keeps Chroma files open
        base = Path(tmp)
        env = {}  # scenario name -> (conn, store), built on first use
        try:
            for case in cases:
                out = {"id": case["id"], "scenario": case["scenario"], "type": case["type"],
                       "question": case["question"], "passed": False, "detail": "", "status": "ok", "answer": "",
                       "fallback": False, "llm_judge": None, "ragas": None}
                try:
                    if case["scenario"] not in SCENARIOS:
                        out.update(status="unsupported", detail=f"[미지원 scenario: {case['scenario']}]")
                    elif "expected" not in case:
                        out.update(status="malformed", detail="[형식 오류] expected가 없습니다")
                    else:
                        if case["scenario"] not in env:
                            env[case["scenario"]] = SCENARIOS[case["scenario"]](base)
                        conn, store = env[case["scenario"]]
                        before = fallback_count()
                        answer = ask(conn, case["question"], store=store)
                        out["fallback"] = fallback_count() > before  # Haiku answered instead of Sonnet
                        out["answer"] = answer.text
                        out["passed"], out["detail"] = judge(case, answer)
                        if judge_model is not None and answer.found:
                            out["llm_judge"] = llm_judge(judge_model, conn, case["question"], answer)
                        if scorer is not None and answer.found:
                            out["ragas"] = scorer.score(case["question"], answer.text, evidence_list(conn, answer))
                except Exception as e:  # one broken case must not stop the run
                    out.update(status="error", detail=f"[실행 오류] {type(e).__name__}: {e}")
                results.append(out)
                print(f"  [{'PASS' if out['passed'] else 'FAIL'}]{'(폴백)' if out['fallback'] else ''} {out['id']} {out['question']}"
                      + ("" if out["passed"] else f"\n         -> {out['detail']}"), flush=True)
                jd = out["llm_judge"]
                if jd and jd["ok"] is not True:
                    print(f"         [심사 {'확인 필요' if jd['ok'] is False else '오류'}] {jd['reason']}", flush=True)
                rg = out["ragas"]
                if rg:
                    print(f"         [ragas] faithfulness={rg['faithfulness']} answer_relevancy={rg['answer_relevancy']}"
                          + (f" ({rg['error']})" if rg.get("error") else ""), flush=True)
        finally:
            for conn, _ in env.values():
                conn.close()
    return results


def git_commit() -> str | None:
    try:
        r = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True,
                           text=True, timeout=10)
        return r.stdout.strip() or None
    except Exception:
        return None


def last_run() -> dict | None:
    if not EVAL_HISTORY.exists():
        return None
    lines = [ln for ln in EVAL_HISTORY.read_text(encoding="utf-8").splitlines() if ln.strip()]
    return json.loads(lines[-1]) if lines else None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", help="id가 이 값으로 시작하는 문항만 실행")
    ap.add_argument("--judge", action="store_true",
                    help="답변 문장을 LLM 심사자도 평가(Bedrock 호출이 문항당 1회 늘어남, 통과 여부에는 영향 없음)")
    ap.add_argument("--ragas", action="store_true",
                    help="RAGAS faithfulness·answer_relevancy 점수 산출(답을 찾은 문항마다 Bedrock 호출 여러 번, 통과 여부에는 영향 없음)")
    ap.add_argument("--no-history", action="store_true", help="eval_history.jsonl에 기록하지 않음")
    args = ap.parse_args()

    cases = json.loads(EVAL_SET.read_text(encoding="utf-8"))
    if args.only:
        cases = [c for c in cases if c["id"].startswith(args.only)]
    extras = "".join([", LLM 심사 포함" if args.judge else "", ", RAGAS 포함" if args.ragas else ""])
    print(f"평가 {len(cases)}문항 (실제 Bedrock 호출{extras})\n")
    results = run(cases, use_judge=args.judge, use_ragas=args.ragas)

    scored = [r for r in results if r["status"] == "ok"]
    skipped = [r for r in results if r["status"] != "ok"]
    passed = sum(r["passed"] for r in scored)
    by_type: dict[str, list[int]] = {}
    for r in scored:
        t = by_type.setdefault(r["type"], [0, 0])
        t[0] += r["passed"]
        t[1] += 1

    print(f"\n===== 결과: {passed}/{len(scored)} 통과 =====")
    for t, (p, n) in by_type.items():
        print(f"  {t}: {p}/{n}")
    fell_back = sum(r["fallback"] for r in scored)
    if fell_back:
        print(f"  ※ {fell_back}문항은 기본 모델(Sonnet) 호출이 실패해 폴백 모델(Haiku)로 답했습니다. "
              "Sonnet 기준 결과가 아니므로 한도가 풀린 뒤 다시 돌려 확인하세요.")
    if skipped:
        print(f"\n===== 채점 제외 {len(skipped)}문항 (평가셋 또는 실행 환경을 고쳐야 합니다) =====")
        for r in skipped:
            print(f"  {r['id']} ({r['status']}) {r['detail']}")

    judged = [r for r in scored if r["llm_judge"] and r["llm_judge"]["ok"] is not None]
    judge_flagged = sorted(r["id"] for r in judged if not r["llm_judge"]["ok"])
    if args.judge:
        errors = sum(1 for r in scored if r["llm_judge"] and r["llm_judge"]["ok"] is None)
        print(f"\n===== LLM 심사(참고용): {len(judged) - len(judge_flagged)}/{len(judged)} 문항 이상 없음 =====")
        print(f"  확인 필요: {judge_flagged or '없음'}" + (f"  (심사 오류 {errors}건)" if errors else ""))

    ragas_means: dict[str, float | None] = {}
    if args.ragas:
        print("\n===== RAGAS 평균(참고용, 0~1) =====")
        for name in ("faithfulness", "answer_relevancy"):
            vals = [r["ragas"][name] for r in scored if r["ragas"] and r["ragas"][name] is not None]
            ragas_means[name] = round(sum(vals) / len(vals), 3) if vals else None
            low = sorted(r["id"] for r in scored
                         if r["ragas"] and r["ragas"][name] is not None and r["ragas"][name] < 0.7)
            print(f"  {name}: {ragas_means[name]} ({len(vals)}문항)" + (f", 0.7 미만: {low}" if low else ""))

    # Compare with the previous run (only when the same ids were run, so --only is not a regression).
    failed_ids = sorted(r["id"] for r in scored if not r["passed"])
    prev = last_run()
    if prev and not args.only:
        newly = sorted(set(failed_ids) - set(prev["failed"]))
        fixed = sorted(set(prev["failed"]) - set(failed_ids))
        print(f"\n===== 직전 실행({prev['time']}, {prev.get('commit')}: {prev['passed']}/{prev['scored']})과 비교 =====")
        print(f"  새로 틀린 문항(회귀): {newly or '없음'}")
        print(f"  고쳐진 문항: {fixed or '없음'}")

    EVAL_RESULT.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n문항별 결과: {EVAL_RESULT.relative_to(ROOT)}")
    if not args.no_history and not args.only:
        row = {"time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "commit": git_commit(),
               "passed": passed, "scored": len(scored), "skipped": len(skipped), "fallback": fell_back,
               "by_type": {t: f"{p}/{n}" for t, (p, n) in by_type.items()}, "failed": failed_ids}
        if args.judge:
            row["judge"] = f"{len(judged) - len(judge_flagged)}/{len(judged)}"
            row["judge_flagged"] = judge_flagged
        if args.ragas:
            row["ragas"] = ragas_means
        with EVAL_HISTORY.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(f"실행 요약 추가: {EVAL_HISTORY.relative_to(ROOT)}")
    return 0 if not failed_ids and not skipped else 1


if __name__ == "__main__":
    sys.exit(main())
