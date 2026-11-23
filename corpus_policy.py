"""Selected corpus membership without touching source files during SQL selection."""
import os
from pathlib import Path
import config_loader


def lexical_path(path):
    return os.path.normcase(os.path.abspath(os.path.expanduser(os.path.expandvars(str(path)))))


def under(path, root):
    try:return os.path.commonpath((path,root)) == root
    except ValueError:return False


class CorpusPolicy:
    def __init__(self):
        self.roots=tuple(lexical_path(root) for root in config_loader.get_config()['scanner']['scan_paths'])
        self.live_roots=tuple(lexical_path(root) for root in config_loader.get_live_config()['scanner']['scan_paths'])

    def allows(self, path):
        if not path:return False
        path=lexical_path(path)
        return any(under(path,root) for root in self.roots) and any(under(path,root) for root in self.live_roots)

    def install(self, connection):
        """Apply the same predicate in SQL before LIMIT/count aggregation."""
        connection.create_function('corpus_selected',1,self.allows,deterministic=True)
        from description_contract import generation_fingerprint
        generation=generation_fingerprint()
        connection.create_function('description_generation',0,lambda:generation,deterministic=True)


def source_allowed(path):
    """Recheck live revocation before any source stat/read/render/model input."""
    policy=CorpusPolicy()
    if not policy.allows(path):return False
    candidate=Path(path)
    try:
        if any(p.is_symlink() or (hasattr(p,'is_junction') and p.is_junction()) for p in (candidate,*candidate.parents)):
            return False
        resolved=lexical_path(candidate.resolve())
        return any(under(resolved,root) for root in policy.roots) and any(under(resolved,root) for root in policy.live_roots)
    except (OSError,RuntimeError):return False


CURRENT_CHUNKS = """f.is_deleted=0 AND corpus_selected(f.path) AND r.status='done' AND r.source_hash IS f.file_hash
    AND (ch.chunk_index>=0 OR EXISTS(SELECT 1 FROM rag_descriptions d WHERE d.file_id=f.id
        AND d.generation_fingerprint=description_generation() AND d.source_hash IS f.file_hash
        AND d.embedding_fingerprint=(SELECT value FROM index_metadata WHERE key='embedding_fingerprint') AND d.embedded=1))"""
