#!/usr/bin/env python3
"""A small, local experiment ledger. Python 3.10+, standard library only.

Candidate code runs as the current user; file snapshots are not an OS sandbox.
"""
import argparse
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import signal
import statistics
import subprocess
import sys
import time
import uuid
from assessment import assess_claims, conditions_met, summarize

VERSION = '0.2.0'


class LabError(Exception):
    pass


def read_json(path):
    def reject(value):
        raise LabError(f'Non-finite JSON constant: {value}')
    try:
        return json.loads(Path(path).read_text(encoding='utf-8-sig'), parse_constant=reject)
    except (OSError, ValueError) as exc:
        raise LabError(f'Cannot read JSON {path}: {exc}') from exc


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    os.replace(temporary, path)


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def contained(root, relative):
    if not isinstance(relative, str) or not relative or '\\' in relative:
        raise LabError('Use non-empty, project-relative paths with forward slashes')
    path = Path(relative)
    if path.is_absolute() or '..' in path.parts or ':' in relative:
        raise LabError(f'Path must stay inside the project: {relative}')
    root = Path(root).resolve()
    target = (root / path).resolve()
    if target == root or root not in target.parents:
        raise LabError(f'Path escapes project: {relative}')
    current = root
    for part in path.parts:
        current = current / part
        if current.is_symlink():
            raise LabError(f'Symlink not permitted in experiment files: {relative}')
    return target


def number(value):
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except OverflowError:
        return False


def require(condition, message):
    if not condition:
        raise LabError(message)


def validate_plan(plan, project):
    require(isinstance(plan, dict) and plan.get('schema_version') == 1, 'Expected schema_version 1')
    for field in ['title', 'question', 'scope']:
        require(isinstance(plan.get(field), str) and plan[field].strip(), f'Missing {field}')
    require(plan.get('reproduction_level') in ['mechanism', 'partial', 'full'], 'Invalid reproduction_level')
    files = plan.get('files')
    protected = plan.get('protected_files')
    require(isinstance(files, list) and files and all(isinstance(p, str) for p in files), 'files must list source files explicitly')
    require(len(set(files)) == len(files), 'Duplicate source file')
    require(isinstance(protected, list) and protected and all(isinstance(p, str) for p in protected)
            and set(protected).issubset(files), 'protected_files must be a non-empty subset of files')
    canonical = []
    for name in files:
        path = contained(project, name)
        normalized = os.path.normcase(str(path.relative_to(Path(project).resolve())))
        require(normalized not in canonical, 'Duplicate source path alias')
        canonical.append(normalized)
        require(path.relative_to(Path(project).resolve()).parts[0].lower() != '.research', 'The ledger cannot be a source input')
        require(normalized not in ['result.json', 'stdout.log', 'stderr.log'], 'Source conflicts with reserved output files')
        require(path.is_file(), f'Missing source file: {name}')
        require(path.stat().st_size <= 128 * 1024 * 1024, f'Use an external data manifest for large file: {name}')
    seeds = plan.get('seeds')
    confirm = plan.get('confirmation_seeds')
    for values in [seeds, confirm]:
        require(isinstance(values, list) and values and all(type(s) is int and s >= 0 for s in values), 'Seeds must be non-negative integers')
        require(len(set(values)) == len(values), 'Duplicate seeds')
    require(not set(seeds).intersection(confirm), 'Development and confirmation seeds must be disjoint')
    command = plan.get('command')
    require(isinstance(command, list) and command and all(isinstance(v, str) and v for v in command), 'command must be a non-empty argv list')
    metric = plan.get('metric', {})
    require(isinstance(metric, dict), 'metric must be an object')
    require(isinstance(metric.get('name'), str) and metric['name'], 'Missing metric name')
    require(metric.get('direction') in ['minimize', 'maximize'], 'Invalid metric direction')
    require(number(metric.get('min_delta', 0)) and metric.get('min_delta', 0) >= 0, 'Invalid min_delta')
    budget = plan.get('budget', {})
    require(isinstance(budget, dict), 'budget must be an object')
    require(type(budget.get('max_trials')) is int and budget['max_trials'] > 0, 'max_trials must be positive')
    for key in ['per_run_seconds', 'total_seconds']:
        require(number(budget.get(key)) and budget[key] > 0, f'Invalid budget {key}')
    checks = plan.get('required_checks', [])
    require(isinstance(checks, list) and checks and all(isinstance(c, str) and c for c in checks), 'Declare required_checks')
    claims = plan.get('claims')
    require(isinstance(claims, list) and claims, 'Declare paper claims and evidence criteria')
    ids = []
    for claim in claims:
        require(isinstance(claim, dict), 'Invalid claim')
        for key in ['id', 'statement', 'source_url', 'source_location']:
            require(isinstance(claim.get(key), str) and claim[key], f'Claim missing {key}')
        ids.append(claim['id'])
        rule = claim.get('criterion')
        if rule is not None:
            require(isinstance(rule, dict) and isinstance(rule.get('metric'), str), 'Invalid claim criterion')
            require(rule.get('operator') in ['le', 'ge'] and number(rule.get('threshold')), 'Invalid claim threshold')
            require(rule.get('aggregation', 'mean') in ['mean', 'all'], 'Claim aggregation must be mean or all')
    require(len(set(ids)) == len(ids), 'Duplicate claim id')
    return plan


def state_dir(project):
    return Path(project).resolve() / '.research'


def records(project):
    result = [read_json(path) for path in sorted((state_dir(project) / 'trials').glob('*/record.json'))]
    return sorted(result, key=lambda r: (r['started_at'], r['id']))


def get_record(project, trial_id):
    require(isinstance(trial_id, str) and len(trial_id) == 12 and all(c in '0123456789abcdef' for c in trial_id), 'Invalid trial id')
    record = read_json(state_dir(project) / 'trials' / trial_id / 'record.json')
    require(record['id'] == trial_id, 'Trial id mismatch')
    return record


@contextmanager
def lock(project):
    path = state_dir(project) / 'lock.json'
    try:
        with path.open('x', encoding='utf-8') as stream:
            json.dump({'pid': os.getpid(), 'created_at': time.time()}, stream)
    except FileExistsError as exc:
        raise LabError('Study locked. Finish the running command, or inspect the PID before using recover.') from exc
    try:
        yield
    finally:
        path.unlink(missing_ok=True)


def contract(project):
    value = read_json(state_dir(project) / 'contract.json')
    plan_path = contained(project, value['plan_file'])
    require(sha(plan_path) == value['plan_sha256'], 'Plan changed after registration; create a new study for a changed protocol')
    require(read_json(plan_path) == value['plan'], 'Registered plan copy differs from original plan')
    require(set(value['protected_sha256']) == set(value['plan']['protected_files']), 'Protected file registry differs from plan')
    for name, digest in value['protected_sha256'].items():
        require(sha(contained(project, name)) == digest, f'Protected file changed: {name}')
    return value


def init(project, plan_file='plan.json'):
    project = Path(project).resolve()
    plan = validate_plan(read_json(contained(project, plan_file)), project)
    state = state_dir(project)
    require(not state.exists(), 'Study already exists; use status or create another project')
    value = {'version': VERSION, 'created_at': time.time(), 'plan_file': plan_file,
             'plan_sha256': sha(contained(project, plan_file)), 'plan': plan,
             'protected_sha256': {p: sha(contained(project, p)) for p in plan['protected_files']}}
    state.mkdir()
    write_json(state / 'contract.json', value)
    return {'study': str(project), 'registered': True, 'plan_sha256': value['plan_sha256']}


def stop_process(process):
    if process.poll() is not None:
        return
    if os.name == 'nt':
        subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True, timeout=10)
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if process.poll() is None:
        process.kill()
    process.wait(timeout=10)


def execute(command, directory, timeout, on_start=None):
    start = time.monotonic()
    environment = os.environ.copy()
    environment['PYTHONDONTWRITEBYTECODE'] = '1'
    environment['PYTHONUTF8'] = '1'
    kwargs = {'start_new_session': True} if os.name != 'nt' else {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP}
    process = None
    with (directory / 'stdout.log').open('wb') as out, (directory / 'stderr.log').open('wb') as err:
        try:
            process = subprocess.Popen(command, cwd=directory, stdout=out, stderr=err, env=environment, **kwargs)
            if on_start:
                on_start(process.pid)
            try:
                code = process.wait(timeout=timeout)
                status = 'completed' if code == 0 else 'failed'
            except subprocess.TimeoutExpired:
                stop_process(process)
                code, status = process.returncode, 'timeout'
        except BaseException:
            if process is not None:
                stop_process(process)
            raise
    return {'exit_code': code, 'status': status, 'pid': process.pid, 'seconds': time.monotonic() - start}


def validate_result(result, plan, directory):
    require(isinstance(result, dict), 'Result must be an object')
    metrics = result.get('metrics')
    require(isinstance(metrics, dict) and plan['metric']['name'] in metrics, 'Primary metric missing')
    require(all(isinstance(k, str) and number(v) for k, v in metrics.items()), 'Metrics must be finite numbers, not booleans')
    checks = result.get('checks')
    require(isinstance(checks, dict), 'Missing checks')
    for name in plan['required_checks']:
        require(checks.get(name) is True, f'Required check failed or missing: {name}')
    artifacts = result.get('artifacts', [])
    require(isinstance(artifacts, list) and all(isinstance(p, str) for p in artifacts), 'Invalid artifacts')
    hashes = {}
    for name in artifacts:
        path = contained(directory, name)
        require(path.is_file(), f'Missing artifact: {name}')
        hashes[name] = sha(path)
    return hashes


def verify_record(project, record):
    frozen = contract(project)
    plan = frozen['plan']
    require(record['plan_sha256'] == frozen['plan_sha256'], 'Trial protocol differs from registered plan')
    require(set(record['source_sha256']) == set(plan['files']), 'Trial snapshot file list differs from plan')
    require(record['phase'] in ['development', 'confirmation'], 'Invalid recorded phase')
    expected_seeds = plan['seeds'] if record['phase'] == 'development' else plan['confirmation_seeds']
    actual_seeds = [r['seed'] for r in record['repeats']]
    require(actual_seeds == expected_seeds[:len(actual_seeds)], 'Recorded repeat seeds differ from plan')
    if record['status'] == 'completed':
        require(actual_seeds == expected_seeds and all(r.get('status') == 'completed' for r in record['repeats']),
                'Completed trial is missing successful repeats')
    base = state_dir(project) / 'trials' / record['id']
    for name, digest in record['source_sha256'].items():
        require(sha(contained(base / 'snapshot', name)) == digest, f'Trial snapshot changed: {record["id"]}/{name}')
    for repeat in record['repeats']:
        for name, digest in repeat.get('evidence_sha256', {}).items():
            path = contained(base / 'repeats' / str(repeat['seed']), name)
            require(sha(path) == digest, f'Trial evidence changed: {record["id"]}/{repeat["seed"]}/{name}')
        if repeat.get('status') == 'completed':
            directory = base / 'repeats' / str(repeat['seed'])
            require(all(name in repeat.get('evidence_sha256', {}) for name in ['result.json', 'stdout.log', 'stderr.log']),
                    'Completed repeat is missing evidence hashes')
            raw = read_json(directory / 'result.json')
            artifacts = validate_result(raw, plan, directory)
            require(all(repeat['evidence_sha256'].get(name) == digest for name, digest in artifacts.items()),
                    'Artifact hashes differ from recorded evidence')
            require(raw['metrics'] == repeat['metrics'] and raw['checks'] == repeat['checks'], 'Cached metrics differ from original result')
    return True


def trial(project, label, hypothesis, baseline=False, parent=None, source_run=None, phase='development'):
    project = Path(project).resolve()
    require(label.strip() and hypothesis.strip(), 'Provide a label and a falsifiable hypothesis')
    require(phase in ['development', 'confirmation'], 'Invalid phase')
    with lock(project):
        frozen = contract(project)
        plan = frozen['plan']
        previous = records(project)
        require(not any(r['status'] == 'running' for r in previous), 'Interrupted record found; use recover first')
        require(len(previous) < plan['budget']['max_trials'], 'Trial budget exhausted')
        spent = sum(r.get('seconds', 0) for r in previous)
        remaining = plan['budget']['total_seconds'] - spent
        require(remaining > 0, 'Runtime budget exhausted')
        baselines = [r for r in previous if r['baseline'] and r['phase'] == 'development' and r['status'] == 'completed']
        if phase == 'development':
            require(not any(r['phase'] == 'confirmation' for r in previous), 'Confirmation has started; do not tune on confirmation results')
            if baseline:
                require(not baselines, 'A valid development baseline already exists; use source-run to repeat it')
            else:
                require(baselines, 'Establish a valid baseline first')
        else:
            require(source_run is not None, 'Confirmation must replay a frozen development source-run')
        source_record = get_record(project, source_run) if source_run else None
        if source_record:
            verify_record(project, source_record)
            require(source_record['status'] == 'completed' and source_record['phase'] == 'development', 'source-run must be a completed development trial')
            baseline = source_record['baseline']
            parent = parent or source_run
            confirmed = [r['source_run'] for r in previous if r['phase'] == 'confirmation' and not r['baseline']]
            if phase == 'confirmation' and not baseline:
                require(not confirmed or set(confirmed) == {source_run}, 'A candidate is already selected for confirmation; do not select on confirmation results')
        if parent:
            get_record(project, parent)
        seeds = plan['seeds'] if phase == 'development' else plan['confirmation_seeds']
        trial_id = uuid.uuid4().hex[:12]
        directory = state_dir(project) / 'trials' / trial_id
        snapshot = directory / 'snapshot'
        snapshot.mkdir(parents=True)
        source = state_dir(project) / 'trials' / source_run / 'snapshot' if source_run else project
        for name in plan['files']:
            target = contained(snapshot, name)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(contained(source, name).read_bytes())
        record = {'id': trial_id, 'label': label, 'hypothesis': hypothesis, 'baseline': baseline,
                  'parent': parent, 'source_run': source_run, 'phase': phase, 'status': 'running',
                  'started_at': time.time(), 'seconds': 0, 'plan_sha256': frozen['plan_sha256'],
                  'source_sha256': {p: sha(contained(snapshot, p)) for p in plan['files']},
                  'environment': {'python': sys.version, 'executable': sys.executable, 'platform': platform.platform()},
                  'repeats': []}
        write_json(directory / 'record.json', record)
        start = time.monotonic()
        try:
            for seed in seeds:
                available = remaining - (time.monotonic() - start)
                if available <= 0:
                    record['status'] = 'budget_exhausted'
                    break
                working = directory / 'repeats' / str(seed)
                working.mkdir(parents=True)
                for name in plan['files']:
                    dest = contained(working, name)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(contained(snapshot, name).read_bytes())
                result_path = working / 'result.json'
                substitutions = {'python': sys.executable, 'seed': str(seed), 'phase': phase,
                                 'result': str(result_path), 'workspace': str(working)}
                try:
                    command = [part.format(**substitutions) for part in plan['command']]
                except (KeyError, ValueError) as exc:
                    raise LabError(f'Unknown command placeholder: {exc}') from exc
                repeat = {'seed': seed, 'command': command}
                record['repeats'].append(repeat)
                write_json(directory / 'record.json', record)
                def save_pid(pid):
                    repeat['pid'] = pid
                    write_json(directory / 'record.json', record)
                repeat.update(execute(command, working, min(plan['budget']['per_run_seconds'], available), save_pid))
                repeat['evidence_sha256'] = {name: sha(working / name) for name in ['stdout.log', 'stderr.log']}
                if result_path.is_file():
                    repeat['evidence_sha256']['result.json'] = sha(result_path)
                try:
                    # Candidate code may not alter either protected files or its own recorded source while running.
                    for name, digest in record['source_sha256'].items():
                        require(sha(contained(working, name)) == digest, f'Input mutated during execution: {name}')
                    contract(project)
                    if repeat['status'] == 'completed':
                        result = read_json(result_path)
                        repeat['evidence_sha256'].update(validate_result(result, plan, working))
                        repeat['evidence_sha256']['result.json'] = sha(result_path)
                        repeat['metrics'] = result['metrics']
                        repeat['checks'] = result['checks']
                except (LabError, OSError) as exc:
                    repeat['status'], repeat['error'] = 'invalid', str(exc)
                write_json(directory / 'record.json', record)
                if repeat['status'] != 'completed':
                    record['status'] = repeat['status']
                    break
            else:
                record['status'] = 'completed'
        except BaseException as exc:
            record['status'] = 'interrupted' if isinstance(exc, KeyboardInterrupt) else 'failed'
            record['error'] = str(exc)
        finally:
            record['seconds'] = time.monotonic() - start
            record['finished_at'] = time.time()
            write_json(directory / 'record.json', record)
        return record


def summary(record, metric):
    values = [r['metrics'][metric] for r in record['repeats'] if r.get('status') == 'completed' and metric in r.get('metrics', {})]
    return summarize(values)


def compare(project, baseline_id, candidate_id):
    frozen = contract(project)
    base, candidate = get_record(project, baseline_id), get_record(project, candidate_id)
    for record in [base, candidate]:
        verify_record(project, record)
        require(record['status'] == 'completed', 'Comparison requires complete successful trials')
    require(base['phase'] == candidate['phase'] and base['plan_sha256'] == candidate['plan_sha256'], 'Cannot compare different protocols or phases')
    require([r['seed'] for r in base['repeats']] == [r['seed'] for r in candidate['repeats']], 'Pair the same seeds')
    metric = frozen['plan']['metric']
    name = metric['name']
    sign = 1 if metric['direction'] == 'maximize' else -1
    deltas = [sign * (b['metrics'][name] - a['metrics'][name]) for a, b in zip(base['repeats'], candidate['repeats'])]
    mean = statistics.mean(deltas)
    claims = assess_claims(frozen['plan'], candidate)
    met = conditions_met(claims)
    improved = mean > metric.get('min_delta', 0)
    selection = 'no_observed_improvement'
    if improved:
        selection = 'score_improved_conditions_met' if met is True else ('score_improved_conditions_failed' if met is False else 'score_improved_conditions_inconclusive')
    return {'baseline': baseline_id, 'candidate': candidate_id, 'phase': candidate['phase'], 'metric': metric,
            'baseline_summary': summary(base, name), 'candidate_summary': summary(candidate, name),
            'paired_improvements': deltas, 'mean_improvement': mean,
            'decision': 'observed_improvement' if improved else 'no_observed_improvement',
            'selection_status': selection, 'candidate_conditions_met': met, 'candidate_claims': claims,
            'interpretation': 'Descriptive paired results only; not a significance test or proof of full-paper replication.'}


def recover(project, reason):
    require(isinstance(reason, str) and reason.strip(), 'Provide a recovery reason')
    path = state_dir(project) / 'lock.json'
    if path.exists():
        pid = read_json(path)['pid']
        if process_alive(pid):
            raise LabError('Lock owner is alive; recovery refused')
    for record in records(project):
        if record['status'] == 'running':
            for repeat in record['repeats']:
                if repeat.get('pid') and repeat.get('status') not in ['completed', 'failed', 'invalid', 'timeout']:
                    require(not process_alive(repeat['pid']), f'Experiment process {repeat["pid"]} is alive; recovery refused')
    if path.exists():
        path.unlink()
    with lock(project):
        frozen = contract(project)
        changed = []
        for record in records(project):
            if record['status'] == 'running':
                record.update(status='interrupted', recovery_reason=reason, finished_at=time.time(),
                              seconds=min(time.time() - record['started_at'], frozen['plan']['budget']['total_seconds']))
                write_json(state_dir(project) / 'trials' / record['id'] / 'record.json', record)
                changed.append(record['id'])
        return {'recovered': changed, 'reason': reason}


def process_alive(pid):
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            if ctypes.get_last_error() == 87:
                return False
            raise LabError('Cannot check lock owner process; inspect it manually')
        try:
            code = wintypes.DWORD()
            require(bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))), 'Cannot read lock owner state')
            return code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError as exc:
        raise LabError('Cannot check lock owner process') from exc
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', required=True, type=Path)
    sub = parser.add_subparsers(dest='command', required=True)
    initialize = sub.add_parser('init'); initialize.add_argument('--plan', default='plan.json')
    run = sub.add_parser('run')
    run.add_argument('--label', required=True); run.add_argument('--hypothesis', required=True)
    run.add_argument('--baseline', action='store_true'); run.add_argument('--parent')
    run.add_argument('--source-run'); run.add_argument('--phase', choices=['development', 'confirmation'], default='development')
    sub.add_parser('status'); sub.add_parser('verify')
    comp = sub.add_parser('compare'); comp.add_argument('--baseline', required=True); comp.add_argument('--candidate', required=True)
    rep = sub.add_parser('report'); rep.add_argument('--run', required=True); rep.add_argument('--output', type=Path)
    rep.add_argument('--baseline')
    recovery = sub.add_parser('recover'); recovery.add_argument('--reason', required=True)
    args = parser.parse_args()
    try:
        if args.command == 'init': result = init(args.project, args.plan)
        elif args.command == 'run':
            record = trial(args.project, args.label, args.hypothesis, args.baseline, args.parent, args.source_run, args.phase)
            result = {k: record[k] for k in ['id', 'label', 'phase', 'status', 'seconds']}
            result['record'] = str(state_dir(args.project) / 'trials' / record['id'] / 'record.json')
            if record.get('error'): result['error'] = record['error']
            for repeat in record['repeats']:
                if repeat.get('status') != 'completed':
                    directory = state_dir(args.project) / 'trials' / record['id'] / 'repeats' / str(repeat['seed'])
                    result['failure'] = {'seed': repeat['seed'], 'status': repeat.get('status', record['status']),
                                         'exit_code': repeat.get('exit_code'), 'error': repeat.get('error', record.get('error')),
                                         'stdout': str(directory / 'stdout.log'), 'stderr': str(directory / 'stderr.log')}
                    break
        elif args.command == 'compare': result = compare(args.project, args.baseline, args.candidate)
        elif args.command == 'recover': result = recover(args.project, args.reason)
        elif args.command == 'report':
            from report import render_report
            result = render_report(args.project, args.run, args.output, args.baseline)
        else:
            frozen = contract(args.project)
            all_records = records(args.project)
            if args.command == 'verify':
                for record in all_records: verify_record(args.project, record)
            result = {'title': frozen['plan']['title'], 'budget': frozen['plan']['budget'],
                      'used_seconds': sum(r.get('seconds', 0) for r in all_records),
                      'trials': [{**{k: r[k] for k in ['id', 'label', 'phase', 'status', 'parent']},
                                  'summary': summary(r, frozen['plan']['metric']['name'])} for r in all_records]}
            result['remaining_trials'] = max(0, frozen['plan']['budget']['max_trials'] - len(all_records))
            result['remaining_seconds'] = max(0, frozen['plan']['budget']['total_seconds'] - result['used_seconds'])
            result['running'] = any(r['status'] == 'running' for r in all_records)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0 if args.command != 'run' or result['status'] == 'completed' else 2
    except (LabError, OSError) as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == '__main__':
    # report.py imports this module; retain the same LabError type when used as a CLI.
    sys.modules['lab'] = sys.modules[__name__]
    raise SystemExit(main())
