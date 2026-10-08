"""Candidate implementation. The initial ablation deliberately omits scaling."""
import numpy as np


def attention(q, k, v, mode):
    logits = q @ np.swapaxes(k, -1, -2)
    if mode == 'sqrt_dimension':
        logits = logits / np.sqrt(q.shape[-1])
    elif mode == 'dimension':
        logits = logits / q.shape[-1]
    elif mode != 'unscaled':
        raise ValueError('Unknown scaling mode')
    exp = np.exp(logits - np.max(logits, axis=-1, keepdims=True))
    weights = exp / exp.sum(axis=-1, keepdims=True)
    return logits, weights, weights @ v
