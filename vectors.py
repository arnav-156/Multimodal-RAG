"""Validated, normalized int8 vectors shared by every writer and search path."""
import numpy as np

def _dimension(dimension):
    if dimension is None:
        from config_loader import get_embedding_dim
        dimension=get_embedding_dim()
    if not isinstance(dimension,int) or dimension<1:raise ValueError('Vector dimension must be positive')
    return dimension

def validate_vector(vector,dimension=None):
    dim=_dimension(dimension)
    v=np.asarray(vector,dtype=np.float32)
    if v.shape!=(dim,) or not np.isfinite(v).all():raise ValueError('Invalid vector dimension or non-finite values')
    norm=float(np.linalg.norm(v))
    if not np.isfinite(norm) or norm<1e-8:raise ValueError('Vector must have a finite nonzero norm')
    return v/norm

def serialize_vector(vector,dimension=None):
    v=validate_vector(vector,dimension)
    quantized=np.clip(np.round(v*127),-127,127).astype(np.int8)
    if not np.any(quantized):raise ValueError('Quantization produced a zero vector')
    return quantized.tobytes()

def deserialize_vector(blob,dimension=None):
    dim=_dimension(dimension)
    if not isinstance(blob,(bytes,bytearray,memoryview)) or len(blob)!=dim:raise ValueError('Invalid vector blob length')
    # Signed-int8 bytes are finite by construction; preserve the same L2 math.
    v=np.frombuffer(blob,dtype=np.int8).astype(np.float32)
    if v.shape!=(dim,):raise ValueError('Invalid vector dimension or non-finite values')
    norm=float(np.linalg.norm(v))
    if not np.isfinite(norm) or norm<1e-8:raise ValueError('Vector must have a finite nonzero norm')
    return v/norm
