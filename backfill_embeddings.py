"""Resumable embedding backfill for FootNotes's Markdown vault.

Markdown remains canonical. This command only creates or repairs derived rows
in vault/ingest.log and is safe to stop and rerun.
"""

import argparse
import asyncio


async def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill the local embedding index.")
    parser.add_argument("--dry-run", action="store_true", help="Report work only.")
    parser.add_argument("--force", action="store_true", help="Re-embed ready entries too.")
    parser.add_argument(
        "--limit",
        type=int,
        default=25,
        help="Maximum entries this run; 0 means all (default: 25).",
    )
    args = parser.parse_args()

    import server

    server.init_ingest_log()
    entries = server.load_vault_entries()
    states = [(entry, server.embedding_state(entry)) for entry in entries]
    candidates = [
        (entry, state)
        for entry, state in states
        if args.force or state != "ready"
    ]
    counts = {state: sum(1 for _entry, value in states if value == state)
              for state in ("ready", "missing", "stale", "failed")}
    print(
        f"[Embeddings] {len(entries)} memories: "
        + ", ".join(f"{value} {key}" for key, value in counts.items())
    )

    selected = candidates if args.limit == 0 else candidates[:max(args.limit, 0)]
    if args.dry_run:
        print(f"[Embeddings] Would process {len(selected)} of {len(candidates)} candidate(s).")
        for entry, state in selected:
            print(f"  {state:7s} {entry['id']}  {(entry.get('title') or entry.get('source_url'))[:60]}")
        return 0
    if not selected:
        print("[Embeddings] Index is current. Nothing to do.")
        return 0

    await server.verify_provider()
    if not server.embedding_provider.available:
        print("[Embeddings] Provider unavailable. No memories were changed; retry after configuration is fixed.")
        return 2

    outcomes = {"embedded": 0, "unchanged": 0, "failed": 0}
    for entry, _state in selected:
        outcome = await server.ensure_entry_embedding(entry, force=args.force)
        outcomes[outcome] += 1
        print(f"  {outcome:9s} {entry['id']}  {(entry.get('title') or entry.get('source_url'))[:60]}")

    remaining = len(candidates) - len(selected)
    print(
        "[Embeddings] "
        + ", ".join(f"{value} {key}" for key, value in outcomes.items())
        + (f"; {remaining} candidate(s) remain for the next run" if remaining else "")
    )
    return 1 if outcomes["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
