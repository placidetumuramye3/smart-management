def normalize_uid(uid):
    return str(uid or "").strip().replace(" ", "").upper()
