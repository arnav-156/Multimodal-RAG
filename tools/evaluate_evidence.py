"""Version 2 evidence evaluation: literal provenance is not semantic correctness.

Input context passages use source_id, doc, start, end, text. Query evidence_units
contain accepted_evidence with complete propositions at exact source offsets.
Human correctness reviews are separate, answer-bound artifacts, never model fields.
"""
import hashlib
import json
import math
import re

VERSION = 2


def normalized(text):
    return re.sub(r'\s+', ' ', text).strip()


def answer_digest(query, context, answer, sources):
    """Bind a review to this question, response, context and exact source contents."""
    payload = {'query': query, 'context': context, 'answer': answer,
               'source_hashes': {key: hashlib.sha256(value.encode('utf8')).hexdigest()
                                 for key, value in sorted(sources.items())}}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode('utf8')).hexdigest()


def evaluate(query, context, answer, sources, *, review=None):
    if not isinstance(answer, dict) or set(answer) != {'abstain', 'claims'} or type(answer['abstain']) is not bool or not isinstance(answer['claims'], list):
        raise ValueError('Expected only abstain boolean and claims list')
    passages = {}
    for p in context['passages']:
        if p['source_id'] in passages:
            raise ValueError('Ambiguous duplicate source_id')
        if (p['doc'] not in sources or type(p['start']) is not int or type(p['end']) is not int or
                not 0 <= p['start'] <= p['end'] <= len(sources[p['doc']]) or
                sources[p['doc']][p['start']:p['end']] != p['text']):
            raise ValueError('Passage does not match its exact source span')
        passages[p['source_id']] = p
    units = query.get('evidence_units')
    if units is None:
        raise ValueError('Version 2 requires explicit complete evidence_units, not legacy atom fragments')
    for unit in units:
        if not unit['accepted_evidence']:
            raise ValueError('Evidence unit has no accepted source')
        for span in unit['accepted_evidence']:
            if (span['doc'] not in sources or type(span['start']) is not int or type(span['end']) is not int or
                    not 0 <= span['start'] < span['end'] <= len(sources[span['doc']]) or
                    sources[span['doc']][span['start']:span['end']] != span['quote']):
                raise ValueError('Label does not match its exact source span')
    claims = []
    for claim in answer['claims']:
        if (not isinstance(claim, dict) or set(claim) != {'quote', 'source_id'} or
                not isinstance(claim['quote'], str) or not isinstance(claim['source_id'], str)):
            raise ValueError('Claims require string quote and source_id only')
        p = passages.get(claim['source_id'])
        quote = normalized(claim['quote'])
        literal = bool(quote and p and quote in normalized(p['text']))
        claims.append({**claim, 'literal_in_cited_passage': literal})
    # A fragment can be authentic without covering the governing condition or negation.
    covered = []
    for unit in units:
        covered.append(any(c['literal_in_cited_passage'] and
                           passages[c['source_id']]['doc'] == span['doc'] and
                           passages[c['source_id']]['start'] <= span['start'] and
                           passages[c['source_id']]['end'] >= span['end'] and
                           normalized(span['quote']) in normalized(c['quote'])
                           for c in claims for span in unit['accepted_evidence']))
    coverage = sum(covered) / len(covered) if covered else None
    literal_precision = sum(c['literal_in_cited_passage'] for c in claims) / len(claims) if claims else None
    digest = answer_digest(query, context, answer, sources)
    correctness = None
    if review is not None:
        if (review.get('answer_sha256') != digest or not isinstance(review.get('reviewer'), str) or
                not review['reviewer'].strip() or review.get('verdict') not in ('correct', 'incorrect', 'uncertain')):
            raise ValueError('Review must identify its reviewer, verdict and exact answer digest')
        correctness = {'correct': True, 'incorrect': False, 'uncertain': None}[review['verdict']]
    return {'evaluator_version': VERSION, 'answer_sha256': digest, 'claims': claims,
            'literal_quote_precision': literal_precision, 'evidence_unit_coverage': coverage,
            'extractive_evidence_complete': bool(units and coverage == 1 and claims and
                                                 all(c['literal_in_cited_passage'] for c in claims) and not answer['abstain']),
            'semantic_answer_correct': correctness,
            'semantic_review': 'provided' if review is not None else 'required',
            'abstained': answer['abstain'], 'abstention_matches_label': answer['abstain'] == query['should_abstain'],
            'label_review_status': query.get('label_review_status', 'unverified')}


def sign_test_design(groups, comparisons=1, alpha=.05, *, baseline_score=None, minimum_gain=0.):
    """Check feasibility before inference; the minimum is not a power calculation."""
    if type(groups) is not int or groups < 1 or type(comparisons) is not int or comparisons < 1 or not 0 < alpha < 1:
        raise ValueError('Positive integer groups/comparisons and alpha in (0,1) required')
    if not 0 <= minimum_gain <= 1 or baseline_score is not None and not 0 <= baseline_score <= 1:
        raise ValueError('Baseline and gain must describe a metric bounded in [0,1]')
    minimum = 2.0 ** (1-groups)
    return {'groups': groups, 'comparisons': comparisons, 'alpha': alpha,
            'smallest_two_sided_p': minimum, 'first_holm_threshold': alpha/comparisons,
            'any_rejection_possible': minimum <= alpha/comparisons,
            'gain_possible_on_observed_metric': None if baseline_score is None else baseline_score + minimum_gain <= 1,
            'minimum_unanimous_non_tied_groups': math.ceil(math.log2(2*comparisons/alpha)),
            'power_established': False}


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Reject mathematically infeasible sign/Holm designs before inference')
    parser.add_argument('--groups', type=int, required=True)
    parser.add_argument('--comparisons', type=int, default=1)
    parser.add_argument('--alpha', type=float, default=.05)
    parser.add_argument('--baseline-score', type=float)
    parser.add_argument('--minimum-gain', type=float, default=0.)
    args = parser.parse_args()
    design = sign_test_design(args.groups, args.comparisons, args.alpha,
                              baseline_score=args.baseline_score, minimum_gain=args.minimum_gain)
    print(json.dumps(design, indent=2))
    raise SystemExit(0 if design['any_rejection_possible'] and design['gain_possible_on_observed_metric'] is not False else 2)
