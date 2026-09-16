"""Keyword scoring: proves a site actually mentions peptides, and classifies it."""

PEPTIDE_TERMS = [
    "peptide", "peptides", "bpc-157", "bpc157", "tb-500", "tb500", "semaglutide",
    "tirzepatide", "retatrutide", "sermorelin", "ipamorelin", "cjc-1295", "cjc1295",
    "tesamorelin", "nad+", "nad plus", "glp-1", "glp1", "pt-141", "pt141", "ghk-cu",
    "ghk cu", "mots-c", "motsc", "aod-9604", "aod9604", "epitalon", "thymosin",
    "kisspeptin", "selank", "semax", "dsip", "5-amino-1mq", "ss-31", "hexarelin",
    "gonadorelin", "oxytocin", "melanotan", "tesofensine", "cagrilintide",
]
MEDSPA_TERMS = ["med spa", "medspa", "medical spa", "botox", "filler", "aesthetic",
                "laser", "microneedling", "prp", "facial", "dermal"]
CLINIC_TERMS = ["clinic", "wellness", "hormone", "trt", "hrt", "anti-aging",
                "longevity", "iv therapy", "weight loss", "telehealth", "functional medicine"]
VENDOR_TERMS = ["wholesale", "supplier", "distributor", "503b", "503a", "outsourcing facility",
                "compounding pharmacy", "manufacturer", "bulk", "coa", "certificate of analysis",
                "research use only", "api ", "lyophilized", "purity", "hplc", "gmp"]


def score(text: str) -> dict:
    t = (text or "").lower()
    found = sorted({term for term in PEPTIDE_TERMS if term in t})
    return {
        "peptide_hits": len(found),
        "peptides_found": ", ".join(found),
        "medspa_score": sum(t.count(k) for k in MEDSPA_TERMS),
        "clinic_score": sum(t.count(k) for k in CLINIC_TERMS),
        "vendor_score": sum(t.count(k) for k in VENDOR_TERMS),
    }


def classify(s: dict) -> str:
    if s["vendor_score"] >= 3 and s["vendor_score"] >= s["medspa_score"]:
        return "vendor"
    if s["medspa_score"] >= 2 and s["medspa_score"] >= s["clinic_score"]:
        return "medspa"
    if s["clinic_score"] >= 2:
        return "clinic"
    return "unknown"
