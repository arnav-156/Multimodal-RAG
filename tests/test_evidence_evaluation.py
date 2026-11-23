"""Scientific measurement regressions: source authenticity must not certify meaning."""
import copy
import pytest
from tools.evaluate_evidence import evaluate, answer_digest, sign_test_design


def fixture():
    text = 'Copy to a new path. Do not overwrite the only backup.'
    spans = [{'doc': 'X', 'start': a, 'end': b, 'quote': text[a:b]} for a,b in [(0,19),(20,len(text))]]
    query = {'id':'probe','should_abstain':False,'evidence_units':[{'accepted_evidence':[s]} for s in spans]}
    context = {'passages':[{'source_id':'X:0','doc':'X','start':0,'end':len(text),'text':text}]}
    return query, context, {'X':text}


def test_negation_clip_is_literal_but_not_complete_evidence_or_correct_answer():
    query,context,sources=fixture()
    answer={'abstain':False,'claims':[{'source_id':'X:0','quote':'Copy to a new path.'},
                                   {'source_id':'X:0','quote':'overwrite the only backup'}]}
    result=evaluate(query,context,answer,sources)
    assert result['literal_quote_precision']==1
    assert result['evidence_unit_coverage']==.5
    assert not result['extractive_evidence_complete']
    assert result['semantic_answer_correct'] is None and result['semantic_review']=='required'


def test_complete_positive_control_and_independent_answer_bound_review():
    query,context,sources=fixture()
    answer={'abstain':False,'claims':[{'source_id':'X:0','quote':sources['X']}]}
    result=evaluate(query,context,answer,sources)
    assert result['extractive_evidence_complete'] and result['evidence_unit_coverage']==1
    assert result['semantic_answer_correct'] is None
    review={'reviewer':'independent reviewer','verdict':'correct','answer_sha256':answer_digest(query,context,answer,sources)}
    assert evaluate(query,context,answer,sources,review=review)['semantic_answer_correct'] is True
    modified=copy.deepcopy(answer);modified['claims'][0]['quote']='Copy to a new path.'
    with pytest.raises(ValueError,match='exact answer digest'):evaluate(query,context,modified,sources,review=review)


def test_wrong_existing_source_cannot_cover_label_and_fabrication_is_not_literal():
    query,context,sources=fixture();sources['Y']='Unrelated text.'
    context['passages'].append({'source_id':'Y:0','doc':'Y','start':0,'end':len(sources['Y']),'text':sources['Y']})
    answer={'abstain':False,'claims':[{'source_id':'Y:0','quote':sources['X']}]}
    result=evaluate(query,context,answer,sources)
    assert result['literal_quote_precision']==0 and result['evidence_unit_coverage']==0


@pytest.mark.parametrize('tamper',['source','offset','duplicate','label'])
def test_source_context_or_label_corruption_is_rejected(tamper):
    query,context,sources=fixture()
    if tamper=='source':sources['X']='Different contents'
    if tamper=='offset':context['passages'][0]['start']=1
    if tamper=='duplicate':context['passages']*=2
    if tamper=='label':query['evidence_units'][0]['accepted_evidence'][0]['quote']='Not in source'
    with pytest.raises(ValueError):evaluate(query,context,{'abstain':True,'claims':[]},sources)


def test_abstention_has_no_vacuous_precision_or_semantic_score():
    query,context,sources=fixture();query.update(should_abstain=True,evidence_units=[])
    result=evaluate(query,context,{'abstain':True,'claims':[]},sources)
    assert result['abstention_matches_label']
    assert result['literal_quote_precision'] is None and result['semantic_answer_correct'] is None
    assert not result['extractive_evidence_complete']


def test_model_cannot_supply_its_own_correctness_review_or_legacy_labels():
    query,context,sources=fixture()
    with pytest.raises(ValueError):evaluate(query,context,{'abstain':False,'claims':[],'review':'correct'},sources)
    del query['evidence_units']
    with pytest.raises(ValueError,match='explicit complete evidence_units'):evaluate(query,context,{'abstain':False,'claims':[]},sources)


@pytest.mark.parametrize('sentence,fragment',[
    ('Use only the signed recovery image.','Use'),
    ('Do not disable the alarm.','disable the alarm'),
    ('Transfer 25 euros, not 25 dollars.','25 dollars'),
    ('Unless the operator approves, keep the valve closed.','keep the valve closed'),
])
def test_authentic_fragments_do_not_cover_units_or_their_qualifiers(sentence,fragment):
    query={'should_abstain':False,'evidence_units':[{'accepted_evidence':[{'doc':'X','start':0,'end':len(sentence),'quote':sentence}]}]}
    context={'passages':[{'source_id':'X:0','doc':'X','start':0,'end':len(sentence),'text':sentence}]}
    answer={'abstain':False,'claims':[{'source_id':'X:0','quote':fragment}]}
    result=evaluate(query,context,answer,{'X':sentence})
    assert result['literal_quote_precision']==1 and result['evidence_unit_coverage']==0
    assert not result['extractive_evidence_complete']


@pytest.mark.parametrize('groups,comparisons,possible,minimum',[(6,9,False,9),(7,14,False,10),(9,9,True,9),(10,14,True,10)])
def test_infeasible_sign_holm_designs_are_detected_before_inference(groups,comparisons,possible,minimum):
    result=sign_test_design(groups,comparisons)
    assert result['any_rejection_possible'] is possible and result['minimum_unanimous_non_tied_groups']==minimum
    assert not result['power_established']


def test_quality_ceiling_is_detected_even_with_enough_groups():
    result=sign_test_design(100,1,baseline_score=1.,minimum_gain=.02)
    assert result['any_rejection_possible'] and not result['gain_possible_on_observed_metric']
    assert sign_test_design(100,1,baseline_score=.7,minimum_gain=.02)['gain_possible_on_observed_metric']
