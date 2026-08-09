# Ensures the repo root is on sys.path during test collection so that
# top-level modules (config, db, models) import cleanly regardless of how
# pytest is invoked.
