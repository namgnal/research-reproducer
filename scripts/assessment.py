"""Evaluate declared conditions separately from evidence-stage conclusions."""
import statistics


def summarize(values):
    return {'n': len(values), 'mean': statistics.mean(values) if values else None,
            'stdev': statistics.stdev(values) if len(values) > 1 else None,
            'min': min(values) if values else None, 'max': max(values) if values else None}


def assess_claims(plan, record):
    """A development condition may pass without being confirmation evidence."""
    expected = plan['confirmation_seeds'] if record['phase'] == 'confirmation' else plan['seeds']
    repeats = {r['seed']: r for r in record['repeats']}
    assessed = []
    for item in plan['claims']:
        rule = item.get('criterion')
        result = {key: item[key] for key in ['id', 'statement', 'source_url', 'source_location']}
        result.update(criterion=rule, status='not_tested', condition_met=None, observed=None,
                      aggregation=rule.get('aggregation', 'mean') if rule else None,
                      per_seed=[], passed_seeds=0, evaluated_seeds=0, expected_seeds=len(expected))
        if rule is None:
            assessed.append(result)
            continue
        for seed in expected:
            repeat = repeats.get(seed, {})
            value = repeat.get('metrics', {}).get(rule['metric']) if repeat.get('status') == 'completed' else None
            passed = None if value is None else (value <= rule['threshold'] if rule['operator'] == 'le' else value >= rule['threshold'])
            result['per_seed'].append({'seed': seed, 'value': value, 'passed': passed,
                                       'run_status': repeat.get('status', 'not_run')})
        available = [r for r in result['per_seed'] if r['value'] is not None]
        result['observed'] = summarize([r['value'] for r in available]) if available else None
        result['evaluated_seeds'] = len(available)
        result['passed_seeds'] = sum(r['passed'] is True for r in available)
        complete = record['status'] == 'completed' and len(available) == len(expected)
        if complete:
            if result['aggregation'] == 'all':
                result['condition_met'] = result['passed_seeds'] == len(expected)
            else:
                mean = result['observed']['mean']
                result['condition_met'] = mean <= rule['threshold'] if rule['operator'] == 'le' else mean >= rule['threshold']
        result['status'] = 'inconclusive'
        if complete and record['phase'] == 'confirmation':
            result['status'] = 'supported_in_scope' if result['condition_met'] else 'not_supported_in_scope'
        assessed.append(result)
    return assessed


def conditions_met(claims):
    measured = [c for c in claims if c['criterion'] is not None]
    if any(c['condition_met'] is False for c in measured):
        return False
    if not measured or any(c['condition_met'] is None for c in measured):
        return None
    return True
