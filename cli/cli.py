"""Thin CLI: parse args, call app.service, print. No business logic here."""
import argparse
import sys

from app.service import ask_question, ingest_and_index
from config import config
from db.db import DEFAULT_DB_PATH
from qa.qa import format_answer


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="reqhist", description="요구사항 이력 에이전트")
    p.add_argument("--db", default=str(DEFAULT_DB_PATH))
    p.add_argument("--chroma", default=config.CHROMA_DIR)
    sub = p.add_subparsers(dest="command", required=True)

    ing = sub.add_parser("ingest", help="Markdown 요구사항 문서 입력")
    ing.add_argument("path")
    ing.add_argument("--requester", default="")
    ing.add_argument("--modifier", default="")

    a = sub.add_parser("ask", help="이력 질문")
    a.add_argument("question")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "ingest":
            r = ingest_and_index(args.db, args.chroma, args.path, args.requester, args.modifier)
            print(f"입력 완료: {r.name}v{r.version} (청크 {r.chunk_count}, 요구사항 {r.requirement_count})")
        else:
            print(format_answer(ask_question(args.db, args.chroma, args.question)))
    except (ValueError, OSError) as e:
        print(f"오류: {e}", file=sys.stderr)
        return 1
    return 0
