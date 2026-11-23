import math
import pytest
from tools.evaluate_relevance import metrics


def test_file_relevance_handles_multiple_labels_cutoff_and_misses():
    result=metrics(['negative','a','other','b','last','c'],['a','b','c'])
    assert result['relevant_ranks']==[2,4]
    assert result['recall_at_k']==2/3 and result['precision_at_k']==2/5
    assert result['mrr_at_k']==.5 and not result['query_failure']
    assert result['ndcg_at_k']==pytest.approx((1/math.log2(3)+1/math.log2(5))/(1+1/math.log2(3)+.5))
    empty=metrics([],['a'])
    assert empty['query_failure'] and all(empty[key]==0 for key in ['recall_at_k','precision_at_k','mrr_at_k','ndcg_at_k'])


def test_duplicate_ranked_files_cannot_inflate_metrics():
    with pytest.raises(ValueError):metrics(['a','a'],['a'])
