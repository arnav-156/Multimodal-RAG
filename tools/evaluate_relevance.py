"""File-level binary relevance metrics; labels and policy must precede inference."""
import math


def metrics(ranking, relevant, k=5):
    if k < 1 or not relevant or len(set(ranking)) != len(ranking):
        raise ValueError('Require positive k, nonempty labels and unique ranked files')
    relevant=set(relevant)
    ranks=[i+1 for i,name in enumerate(ranking[:k]) if name in relevant]
    ideal=sum(1/math.log2(i+2) for i in range(min(k,len(relevant))))
    return {'relevant_ranks':ranks,'recall_at_k':len(ranks)/len(relevant),
        'precision_at_k':len(ranks)/k,'mrr_at_k':1/min(ranks) if ranks else 0,
        'ndcg_at_k':sum(1/math.log2(i+1) for i in ranks)/ideal,
        'query_failure':not bool(ranks)}
