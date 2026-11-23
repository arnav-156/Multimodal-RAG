"""Small source document for the public search demonstration."""


def evict_expired(cache, now):
    """Remove cached entries whose expiry timestamp is before now."""
    for key in list(cache):
        if cache[key]["expires"] < now:
            del cache[key]
