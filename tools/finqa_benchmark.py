"""Label-independent FinQA source conversion and deterministic company sampling.

Row conversion follows the authors' corrected May 2022 uniform serialization.
Only source fields enter retrieval; QA answers and upstream retrieved/gold fields
must never be copied into indexed documents.
"""
import hashlib
import math
import re
from collections import Counter


def normalize(text):
    return ' '.join(text.split())


def table_row(header, row):
    return normalize((header[0] + ' ' if header[0] else '') + ''.join(
        f'the {row[0]} of {head} is {cell} ; ' for head, cell in zip(header[1:], row[1:])))


def source_units(example):
    units = {f'text_{i}': text for i, text in enumerate(example['pre_text'] + example['post_text'])}
    units.update({f'table_{i}': table_row(example['table'][0], row)
                  for i, row in enumerate(example['table'][1:], 1)})
    return units


def source_text(example):
    return '\n\n'.join(example['pre_text'] +
        [table_row(example['table'][0], row) for row in example['table'][1:]] + example['post_text'])


def sample_companies(examples):
    """One question per company, selected without labels, scores or difficulty."""
    chosen = {}
    for example in sorted(examples, key=lambda x: hashlib.sha256(x['id'].encode()).hexdigest()):
        chosen.setdefault(example['id'].split('/')[0], example)
    return sorted(chosen.values(), key=lambda x: x['id'])


def bm25_rank(query, documents, k=5):
    """Fixed k1=1.2, b=.75; standalone full-corpus lexical baseline."""
    tokenize = lambda text: re.findall(r'\w+', text.lower())
    counts = [Counter(tokenize(text)) for text in documents]
    lengths = [sum(c.values()) for c in counts]
    average = sum(lengths) / max(1, len(lengths)) or 1
    scores = [0.] * len(documents)
    for term in set(tokenize(query)):
        df = sum(term in c for c in counts)
        idf = math.log(1 + (len(documents)-df+.5)/(df+.5))
        for i, count in enumerate(counts):
            tf = count[term]
            scores[i] += idf * tf * 2.2 / (tf + 1.2 * (.25 + .75 * lengths[i]/average))
    return sorted(range(len(scores)), key=lambda i: (scores[i], i), reverse=True)[:k]
