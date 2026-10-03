"""After a discovery run: recompile the vendor CSVs and write a new-vendors-only file (domains absent from the previous export)."""
import csv, subprocess, sys, os
ROOT=os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)),"..","..")); HERE=os.path.dirname(os.path.abspath(__file__)); S=os.environ.get("VENDOR_CRAWL_DIR", os.path.join(ROOT,"data","vendor_crawl")); stamp=sys.argv[1]
subprocess.run([sys.executable, f"{HERE}/compile_csv.py", stamp], cwd=ROOT, check=True)
before={r["domain"] for r in csv.DictReader(open(f"{S}/vendors_before.csv", encoding="utf-8-sig"))}
rows=list(csv.DictReader(open(f"{ROOT}/data/out/us_peptide_vendors_all_{stamp}.csv", encoding="utf-8-sig")))
new=[r for r in rows if r["domain"] and r["domain"] not in before]
with open(f"{ROOT}/exports/us_peptide_vendors_NEW_{stamp}.csv","w",newline="",encoding="utf-8-sig") as f:
    w=csv.DictWriter(f, fieldnames=rows[0].keys()); w.writeheader(); w.writerows(new)
for name in ("all","with_email"):
    os.replace(f"{ROOT}/data/out/us_peptide_vendors_{name}_{stamp}.csv", f"{ROOT}/exports/us_peptide_vendors_{name}_{stamp}.csv")
print(f"new vendors: {len(new)} (with email {sum(1 for r in new if r['primary_email'])}) | full list now {len(rows)} rows, {sum(1 for r in rows if r['primary_email'])} with email")
