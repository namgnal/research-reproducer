"""Independent numerical evaluator for a declared mechanism, not full-paper BLEU."""
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
from attention import attention


def evaluate(seed, phase):
    rng = np.random.default_rng(seed)
    mode = json.loads(Path('config.json').read_text(encoding='utf-8'))['mode']
    dimensions = [16, 64, 256] if phase == 'development' else [32, 128, 512]
    rows = []
    checks = {'finite_arrays': True, 'correct_shapes': True, 'normalized_weights': True,
              'weights_match_softmax': True, 'output_matches_weights': True}
    for dim in dimensions:
        q = rng.normal(size=(24, 16, dim))
        k = rng.normal(size=(24, 64, dim))
        v = rng.normal(size=(24, 64, 8))
        logits, weights, output = attention(q, k, v, mode)
        reference = (q @ np.swapaxes(k, -1, -2)) / np.sqrt(dim)
        checks['finite_arrays'] &= all(bool(np.isfinite(a).all()) for a in [logits, weights, output])
        checks['correct_shapes'] &= logits.shape == weights.shape == (24, 16, 64) and output.shape == (24, 16, 8)
        checks['normalized_weights'] &= bool(np.all(weights >= 0) and np.allclose(weights.sum(-1), 1, rtol=0, atol=1e-12))
        exponential = np.exp(logits - logits.max(axis=-1, keepdims=True))
        checks['weights_match_softmax'] &= bool(np.allclose(weights, exponential / exponential.sum(axis=-1, keepdims=True), rtol=0, atol=1e-12))
        checks['output_matches_weights'] &= bool(np.allclose(output, weights @ v, rtol=0, atol=1e-12))
        variance = float(np.var(logits))
        entropy = float(np.mean(-np.sum(weights * np.log(np.maximum(weights, 1e-300)), axis=-1)) / np.log(64))
        rows.append({'dimension': dim, 'logit_variance': variance, 'variance_error': abs(variance - 1),
                     'normalized_entropy': entropy, 'equation_max_error': float(np.max(np.abs(logits - reference))),
                     'input_sha256': hashlib.sha256(q.tobytes() + k.tobytes() + v.tobytes()).hexdigest()})
    Path('measurements.json').write_text(json.dumps({'seed': seed, 'phase': phase, 'mode': mode, 'numpy_version': np.__version__,
                                                  'input_distribution': 'Independent N(0, 1) Q/K/V', 'dimensions': rows}, indent=2), encoding='utf-8')
    metrics = {key: float(np.mean([r[key] for r in rows])) for key in ['variance_error', 'normalized_entropy', 'equation_max_error']}
    metrics['equation_max_error'] = max(r['equation_max_error'] for r in rows)
    return {'metrics': metrics, 'checks': checks, 'artifacts': ['measurements.json']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--seed', type=int, required=True)
    parser.add_argument('--phase', choices=['development', 'confirmation'], required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = evaluate(args.seed, args.phase)
    args.output.write_text(json.dumps(result, allow_nan=False), encoding='utf-8')
    print(json.dumps(result, allow_nan=False))
