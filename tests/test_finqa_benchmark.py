from copy import deepcopy
from tools.finqa_benchmark import source_text, source_units, sample_companies, bm25_rank


def example(name='A/2020/page_1.pdf-1'):
    return {'id': name, 'pre_text': ['Revenue statement.'], 'post_text': ['Audited.'],
        'table': [['', '2020'], ['revenue', '$ 50']],
        'qa': {'answer': 'SECRET_GOLD', 'question': 'SECRET_QUERY'}, 'text_retrieved': ['LEAK']}


def test_source_conversion_cannot_leak_question_answer_or_retriever_labels():
    a = example()
    b = deepcopy(a)
    b['qa'] = {'gold_inds': {'table_1': 'OTHER_LEAK'}}
    assert source_text(a) == source_text(b) == 'Revenue statement.\n\nthe revenue of 2020 is $ 50 ;\n\nAudited.'
    assert source_units(a)['table_1'] == 'the revenue of 2020 is $ 50 ;'


def test_sampling_is_order_invariant_and_company_disjoint_without_gold():
    rows = [example('A/2020/page_1.pdf-1'), example('A/2021/page_2.pdf-1'), example('B/2020/page_1.pdf-1')]
    assert sample_companies(rows) == sample_companies(rows[::-1])
    assert len(sample_companies(rows)) == 2
    for row in rows:
        del row['qa']
    assert len(sample_companies(rows)) == 2


def test_uniform_table_prefix_does_not_depend_on_positive_labels():
    a = example()
    a['table'][0][0] = 'USD millions'
    assert source_units(a)['table_1'] == 'USD millions the revenue of 2020 is $ 50 ;'


def test_bm25_uses_document_text_and_returns_unique_ranks():
    ranks = bm25_rank('backup recovery', ['revenue growth', 'backup recovery procedure', 'backup archive'], k=3)
    assert ranks == [1, 2, 0]
