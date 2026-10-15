from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime, timedelta
from importlib.resources import files
from pathlib import Path

from .config import load
from .coordinator import Coordinator, resume_run
from .evaluation import evaluate
from .locking import database_lock
from .rendering import export, markdown
from .storage import Repository
from .util import date, dumps


def parser():
    p = argparse.ArgumentParser(
        prog="llm-updates", description="Evidence-first personal LLM update digests"
    )
    p.add_argument("--config", type=Path, default=Path("config.toml"))
    commands = p.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="Create editable configuration and local database")
    init.add_argument("--config", type=Path, dest="init_config")
    init.add_argument(
        "--demo", action="store_true", help="Use a separate database for fictional fixtures"
    )
    for name in ("collect", "run"):
        run = commands.add_parser(name)
        run.add_argument("--since")
        run.add_argument("--until")
        run.add_argument("--fixture", type=Path, help="Use synthetic normalized JSONL; no network")
        run.add_argument("--key", default="manual", help="Explicit idempotency key")
        run.add_argument(
            "--refresh-of", help="Create an intentional refresh linked to an earlier run"
        )
        run.add_argument("--profile", type=Path)
        run.add_argument("--top", type=int, choices=range(1, 6), default=5)
        run.add_argument("--mode", choices=("baseline", "research"), default="baseline")
        run.add_argument(
            "--ranker", choices=("baseline", "newest", "keyword-recency"), default="baseline"
        )
    resume = commands.add_parser("resume")
    resume.add_argument("run_id")
    replay = commands.add_parser("replay")
    replay.add_argument("run_id")
    replay.add_argument("--offline", action="store_true", required=True)
    digest = commands.add_parser("digest").add_subparsers(dest="digest_command", required=True)
    show = digest.add_parser("show")
    show.add_argument("digest_id")
    show.add_argument("--format", choices=("markdown", "json"), default="markdown")
    feedback = commands.add_parser("feedback")
    feedback.add_argument("entry_id")
    feedback.add_argument("label", choices=("useful", "already_knew", "skip"))
    feedback.add_argument("--note")
    inspect = commands.add_parser("inspect")
    inspect.add_argument("item_id")
    inspect.add_argument("--versions", action="store_true")
    inspect.add_argument("--evidence", action="store_true")
    ev = commands.add_parser("evaluate")
    ev.add_argument("--dataset", required=True, type=Path)
    ev.add_argument("--mode", choices=("baseline", "newest", "keyword-recency"), default="baseline")
    commands.add_parser("status")
    serve = commands.add_parser("serve", help="Browse digests and ML guides in a local web app")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--verbose", action="store_true", help="Log each request")
    site = commands.add_parser("site", help="Export a static, read-only website")
    site.add_argument("--out", type=Path, help="Output folder (default: data/site)")
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        if args.command == "init":
            path = args.init_config or args.config
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                contents = (
                    files("llm_updates")
                    .joinpath("defaults/config.toml")
                    .read_text(encoding="utf-8")
                )
                if args.demo:
                    contents = contents.replace(
                        "data/updates.sqlite3", "data/demo.sqlite3"
                    ).replace("data/digests", "data/demo-digests")
                path.write_text(contents, encoding="utf-8")
            config = load(path)
            with database_lock(config.database):
                repo = Repository(config.database)
                repo.configure(config)
                repo.close()
            print(
                dumps(
                    {
                        "status": "initialized",
                        "config": str(path.resolve()),
                        "database": config.database,
                    }
                )
            )
            return 0
        config = load(args.config, getattr(args, "profile", None))
        if args.command == "evaluate":
            print(json.dumps(evaluate(args.dataset, config, args.mode), indent=2))
            return 0
        if args.command == "serve":
            from .web.server import serve

            return serve(config.database, args.port, args.verbose)
        if args.command == "site":
            from .web.site import build_site

            out = args.out or Path(config.output_dir).parent / "site"
            print(dumps(build_site(config.database, out)))
            return 0
        with database_lock(config.database):
            repo = Repository(config.database)
            try:
                if args.command in ("run", "collect"):
                    until = (
                        date(args.until)
                        if args.until
                        else datetime.now(UTC).isoformat(timespec="seconds")
                    )
                    since = (
                        date(args.since)
                        if args.since
                        else (
                            datetime.fromisoformat(until)
                            - timedelta(days=config.limits.lookback_days)
                        ).isoformat()
                    )
                    if not since or not until:
                        raise ValueError("invalid time window")
                    result, path = Coordinator(repo, config).run(
                        since,
                        until,
                        fixture=args.fixture,
                        mode=args.mode,
                        top=args.top,
                        key=args.key,
                        collect_only=args.command == "collect",
                        parent=args.refresh_of,
                        ranking_mode=args.ranker,
                    )
                    print(
                        dumps(
                            {
                                "run_id": result["run_id"],
                                "status": result["status"],
                                "digest_id": result.get("digest_id"),
                                "entries": len(result.get("entries", [])),
                                "output": path,
                                "coverage": {
                                    k: result["coverage"][k]
                                    for k in ("checked", "succeeded", "failed", "incomplete")
                                },
                            }
                        )
                    )
                    return 0 if result["status"] == "succeeded" else 2
                if args.command == "resume":
                    result, path = resume_run(repo, args.run_id)
                    print(
                        dumps({"run_id": args.run_id, "status": result["status"], "output": path})
                    )
                    return 0 if result["status"] == "succeeded" else 2
                if args.command in ("digest", "replay"):
                    payload = repo.digest(
                        args.digest_id if args.command == "digest" else args.run_id
                    )
                    if not payload:
                        raise ValueError("no committed digest; resume the run instead")
                    if args.command == "replay":
                        print(
                            dumps({"output": export(payload, config.output_dir), "offline": True})
                        )
                    else:
                        print(
                            markdown(payload)
                            if args.format == "markdown"
                            else json.dumps(payload, indent=2)
                        )
                    return 0
                if args.command == "feedback":
                    print(dumps(repo.feedback(args.entry_id, args.label, args.note)))
                elif args.command == "inspect":
                    item = repo.one(
                        "SELECT * FROM items WHERE item_id=? OR canonical_key=?",
                        (args.item_id, args.item_id),
                    )
                    if not item:
                        raise ValueError("unknown item")
                    result = {"item": item}
                    if args.versions or args.evidence:
                        result["versions"] = repo.all(
                            "SELECT * FROM versions WHERE item_id=? ORDER BY rowid",
                            (item["item_id"],),
                        )
                    if args.evidence:
                        result["evidence"] = repo.all(
                            "SELECT e.* FROM evidence e JOIN versions v USING(version_id) WHERE v.item_id=?",
                            (item["item_id"],),
                        )
                    print(json.dumps(result, indent=2))
                elif args.command == "status":
                    print(
                        json.dumps(
                            repo.all(
                                "SELECT run_id,status,stage,started_at,attempt FROM runs ORDER BY started_at DESC LIMIT 10"
                            ),
                            indent=2,
                        )
                    )
                return 0
            finally:
                repo.close()
    except KeyboardInterrupt:
        print(
            dumps({"status": "interrupted", "message": "Checkpoint saved; use resume RUN_ID."}),
            file=sys.stderr,
        )
        return 130
    except Exception as exc:
        print(
            dumps(
                {
                    "status": "failed",
                    "error_code": type(exc).__name__,
                    "message": str(exc)
                    if type(exc) in (ValueError, FileNotFoundError)
                    else "See the stored run event.",
                }
            ),
            file=sys.stderr,
        )
        return 1
