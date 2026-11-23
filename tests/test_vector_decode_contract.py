"""Codec specialization must retain every byte-container/numeric guard."""
import numpy as np
import pytest
from vectors import deserialize_vector,validate_vector

@pytest.mark.parametrize('dimension',[1,4,512,768,1024])
@pytest.mark.parametrize('container',[bytes,bytearray,memoryview])
def test_int8_decode_is_bit_identical_to_float_validation(dimension,container):
    rng=np.random.default_rng(735)
    values=rng.integers(-128,128,size=dimension,dtype=np.int16).astype(np.int8)
    values[0]=-128
    expected=validate_vector(values.astype(np.float32),dimension)
    actual=deserialize_vector(container(values.tobytes()),dimension)
    assert actual.dtype==np.float32
    assert np.array_equal(actual,expected)

@pytest.mark.parametrize('blob',[b'bad',bytes(4),'abcd',[0,1,2,3],None,b''])
def test_decode_still_rejects_zero_wrong_length_and_nonbyte_data(blob):
    with pytest.raises(ValueError):deserialize_vector(blob,4)

@pytest.mark.parametrize('dimension',[0,-1,1.5,'4'])
def test_decode_still_rejects_invalid_dimensions(dimension):
    with pytest.raises(ValueError):deserialize_vector(b'abcd',dimension)

@pytest.mark.parametrize('dtype',[np.int16,np.float32])
def test_typed_memoryview_length_is_not_its_byte_count(dtype):
    blob=memoryview(np.array([1,2,3,4],dtype=dtype))
    assert len(blob)==4 and blob.nbytes>4
    with pytest.raises(ValueError):deserialize_vector(blob,4)
