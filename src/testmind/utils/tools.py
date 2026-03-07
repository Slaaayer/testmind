import hashlib
import json


def generate_unique_id(**kwargs):
    normalized = json.dumps(kwargs, sort_keys=True)
    return hashlib.sha256(normalized.encode()).hexdigest()
