import argparse
import os
import sys
import time

import yaml
from dotenv import load_dotenv

load_dotenv()

from .store import Store  # noqa: E402
from . import discover, enrich as enrich_mod  # noqa: E402
from .session import login, session_path  # noqa: E402
from .discover import domain_of  # noqa: E402

CFG = "config/queries.yaml"


def load_cfg():
    with open(CFG) as f:
        return yaml.safe_load(f)


def cmd_discover(args):
    cfg, store = load_cfg(), Store()
    mode = args.mode
    local = mode in ("medspa", "clinic")
    cities = args.cities.split(";") if args.cities else cfg["cities"]
    queries = [q.format(city=c) for q in cfg[mode] for c in cities] if local else list(cfg[mode])
    if args.limit:
        queries = queries[:args.limit]
    print(f"[{mode}] {len(queries)} queries")
    new_total = 0
    for i, q in enumerate(queries, 1):
        if store.query_done(f"{mode}|{q}"):
            continue
        print(f"  ({i}/{len(queries)}) {q}")
        try:
            results = discover.search(q, local=local)
        except Exception as e:
            print(f"    error: {e}"); continue
        new = sum(store.add_candidate(mode, r) for r in results)
        new_total += new
        print(f"    {len(results)} results, {new} new")
        store.mark_query(f"{mode}|{q}")
    # directory pages
    for url in cfg.get("directories", {}).get(mode, []):
        key = f"{mode}|dir|{url}"
        if store.query_done(key):
            continue
        print(f"  directory: {url}")
        try:
            results = enrich_mod.crawl_directory(url, session_path(domain_of(url)))
        except Exception as e:
            print(f"    error: {e}"); results = []
        new = sum(store.add_candidate(mode, r) for r in results)
        new_total += new
        print(f"    {len(results)} links, {new} new")
        store.mark_query(key)
    enrich_mod.shutdown_browser()
    print(f"Done. {new_total} new candidates. Pending enrichment: {len(store.pending(mode))}")


def cmd_enrich(args):
    store = Store()
    pending = store.pending(args.mode)
    if args.limit:
        pending = pending[:args.limit]
    print(f"[{args.mode}] enriching {len(pending)} sites")
    for i, rec in enumerate(pending, 1):
        print(f"  ({i}/{len(pending)}) {rec['domain']}", end=" ")
        try:
            rec = enrich_mod.enrich(rec)
        except Exception as e:
            rec["confidence"] = "error"; rec["notes"] = (rec.get("notes") or "") + f" | {e}"
        store.save_enriched(args.mode, rec)
        print(f"-> hits={rec.get('peptide_hits', 0)} emails={rec.get('emails', '') or '-'}")
        time.sleep(enrich_mod.DELAY)
    enrich_mod.shutdown_browser()
    cmd_export(args)


def cmd_export(args):
    store = Store()
    os.makedirs("data/out", exist_ok=True)
    stamp = time.strftime("%Y-%m-%d")
    mode = getattr(args, "mode", None)
    tag = mode or "all"
    n_all = store.export_csv(f"data/out/{tag}_all_{stamp}.csv", mode)
    n_pep = store.export_csv(f"data/out/{tag}_peptide_confirmed_{stamp}.csv", mode, min_hits=1)
    n_em = store.export_csv(f"data/out/{tag}_peptide_with_email_{stamp}.csv", mode, min_hits=1, emails_only=True)
    print(f"Exported to data/out/: {n_all} total | {n_pep} peptide-confirmed | {n_em} with email")


def cmd_run(args):
    cmd_discover(args)
    cmd_enrich(args)


def cmd_login(args):
    login(args.domain)


def cmd_status(args):
    store = Store()
    for m in ("medspa", "clinic", "vendor"):
        rows = store.all(m)
        done = [r for r in rows if r.get("scraped_at")]
        em = [r for r in done if r.get("emails")]
        pep = [r for r in done if int(r.get("peptide_hits") or 0) > 0]
        print(f"{m:7s} candidates={len(rows):5d} enriched={len(done):5d} peptide_confirmed={len(pep):5d} with_email={len(em):5d}")


def main():
    ap = argparse.ArgumentParser(prog="bot", description="Peptide lead scraper")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in (("run", cmd_run), ("discover", cmd_discover), ("enrich", cmd_enrich)):
        s = sub.add_parser(name)
        s.add_argument("--mode", choices=["medspa", "clinic", "vendor"], required=True)
        s.add_argument("--cities", help='Override cities, e.g. "Las Vegas, NV;Phoenix, AZ"')
        s.add_argument("--limit", type=int, help="Cap queries/sites for a test run")
        s.set_defaults(fn=fn)
    s = sub.add_parser("export"); s.add_argument("--mode", choices=["medspa", "clinic", "vendor"]); s.set_defaults(fn=cmd_export)
    s = sub.add_parser("login"); s.add_argument("domain"); s.set_defaults(fn=cmd_login)
    s = sub.add_parser("status"); s.set_defaults(fn=cmd_status)
    args = ap.parse_args()
    if not os.path.exists(CFG):
        sys.exit("Run from the project root (config/queries.yaml not found)")
    args.fn(args)


if __name__ == "__main__":
    main()
