"""SQLite store so runs are resumable, plus CSV export."""
import csv
import json
import os
import sqlite3
import time

COLUMNS = [
    "mode", "business_name", "category", "website", "domain", "emails", "phones",
    "address", "city", "state", "zip", "rating", "review_count", "google_maps_url",
    "instagram", "peptides_found", "peptide_hits", "confidence", "contact_page",
    "source", "source_query", "notes", "scraped_at",
]


class Store:
    def __init__(self, path="data/leads.sqlite"):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS leads (domain TEXT, mode TEXT, data TEXT, "
            "enriched INTEGER DEFAULT 0, PRIMARY KEY (domain, mode))"
        )
        self.conn.execute("CREATE TABLE IF NOT EXISTS done_queries (q TEXT PRIMARY KEY)")
        self.conn.commit()

    def query_done(self, q):
        return self.conn.execute("SELECT 1 FROM done_queries WHERE q=?", (q,)).fetchone() is not None

    def mark_query(self, q):
        self.conn.execute("INSERT OR IGNORE INTO done_queries VALUES (?)", (q,))
        self.conn.commit()

    def add_candidate(self, mode, rec):
        """Insert if new; merge non-empty fields if it exists. Returns True if new."""
        dom = rec.get("domain")
        if not dom:
            return False
        row = self.conn.execute("SELECT data FROM leads WHERE domain=? AND mode=?", (dom, mode)).fetchone()
        if row:
            old = json.loads(row[0])
            for k, v in rec.items():
                if v and not old.get(k):
                    old[k] = v
            self.conn.execute("UPDATE leads SET data=? WHERE domain=? AND mode=?", (json.dumps(old), dom, mode))
            self.conn.commit()
            return False
        rec["mode"] = mode
        self.conn.execute("INSERT INTO leads (domain, mode, data, enriched) VALUES (?,?,?,0)",
                          (dom, mode, json.dumps(rec)))
        self.conn.commit()
        return True

    def pending(self, mode):
        rows = self.conn.execute("SELECT data FROM leads WHERE mode=? AND enriched=0", (mode,)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def save_enriched(self, mode, rec):
        rec["scraped_at"] = time.strftime("%Y-%m-%d %H:%M")
        self.conn.execute("UPDATE leads SET data=?, enriched=1 WHERE domain=? AND mode=?",
                          (json.dumps(rec), rec["domain"], mode))
        self.conn.commit()

    def all(self, mode=None):
        if mode:
            rows = self.conn.execute("SELECT data FROM leads WHERE mode=?", (mode,)).fetchall()
        else:
            rows = self.conn.execute("SELECT data FROM leads").fetchall()
        return [json.loads(r[0]) for r in rows]

    def export_csv(self, path, mode=None, min_hits=0, emails_only=False):
        rows = self.all(mode)
        rows = [r for r in rows if int(r.get("peptide_hits") or 0) >= min_hits]
        if emails_only:
            rows = [r for r in rows if r.get("emails")]
        rows.sort(key=lambda r: (-int(r.get("peptide_hits") or 0), r.get("state") or "", r.get("business_name") or ""))
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
            w.writeheader()
            for r in rows:
                w.writerow({c: r.get(c, "") for c in COLUMNS})
        return len(rows)
