"""Behavior tests for experiment execution, evidence, and confirmation boundaries.

Run: python -m unittest discover -s <skill>/scripts -p test_lab.py -v
No third-party packages or network required.
"""
import json
import csv
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import lab
from report import render_report


EVALUATOR = '''import json, os, sys, time
from pathlib import Path
config = json.loads(Path('config.json').read_text())
seed = int(sys.argv[1])
output = Path(sys.argv[2])
mode = config.get('mode', 'ok')
print(os.getpid(), flush=True)
if mode == 'sleep':
    time.sleep(20)
if mode == 'crash':
    raise RuntimeError('Intentional failure')
if mode == 'mutate':
    Path('evaluate.py').write_text('changed')
if mode == 'absent':
    sys.exit(0)
metric = config.get('score', 5) + seed / 1000
metric = config.get('scores', {}).get(str(seed), metric)
if mode == 'bool': metric = True
if mode == 'nan': metric = float('nan')
if mode == 'infinity': metric = float('inf')
Path('measurement.json').write_text(json.dumps({'seed': seed, 'value': metric}))
result = {'metrics': {'score': metric}, 'checks': {'valid': mode != 'badcheck'}, 'artifacts': ['measurement.json']}
if mode == 'missing': result['metrics'] = {}
if mode == 'outside': result['artifacts'] = ['../outside.json']
output.write_text(json.dumps(result))
'''


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='research-reproducer-test-')
        self.root = Path(self.temporary.name)
        self.counter = 0

    def tearDown(self):
        self.temporary.cleanup()

    def project(self, changes=None, initialize=True):
        self.counter += 1
        project = self.root / str(self.counter)
        project.mkdir()
        (project / 'evaluate.py').write_text(EVALUATOR, encoding='utf-8')
        lab.write_json(project / 'config.json', {'score': 5})
        plan = {'schema_version': 1, 'title': 'Controlled fixture', 'question': 'Does a controlled change lower the score?',
                'scope': 'Synthetic runner test only', 'reproduction_level': 'mechanism',
                'files': ['evaluate.py', 'config.json'], 'protected_files': ['evaluate.py'],
                'command': ['{python}', 'evaluate.py', '{seed}', '{result}'],
                'seeds': [1, 2], 'confirmation_seeds': [10, 11],
                'metric': {'name': 'score', 'direction': 'minimize', 'min_delta': 0.1},
                'budget': {'max_trials': 20, 'per_run_seconds': 5, 'total_seconds': 60},
                'required_checks': ['valid'],
                'claims': [{'id': 'C1', 'statement': 'The changed score is at most two',
                            'source_url': 'https://example.org/paper', 'source_location': 'synthetic fixture',
                            'criterion': {'metric': 'score', 'operator': 'le', 'threshold': 2}},
                           {'id': 'C2', 'statement': 'Not tested', 'source_url': 'https://example.org/paper',
                            'source_location': 'fixture', 'criterion': None}]}
        if changes:
            plan.update(changes)
        lab.write_json(project / 'plan.json', plan)
        if initialize:
            lab.init(project)
        return project

    def run_trial(self, project, **kwargs):
        return lab.trial(project, kwargs.pop('label', 'trial'), 'A declared test hypothesis', **kwargs)

    def configure(self, project, score=1, mode='ok'):
        lab.write_json(project / 'config.json', {'score': score, 'mode': mode})

    def test_baseline_candidate_and_frozen_confirmation(self):
        project = self.project()
        baseline = self.run_trial(project, baseline=True)
        self.configure(project, score=1)
        candidate = self.run_trial(project, parent=baseline['id'])
        compared = lab.compare(project, baseline['id'], candidate['id'])
        self.assertEqual(compared['paired_improvements'], [4.0, 4.0])
        self.assertEqual(compared['decision'], 'observed_improvement')
        self.configure(project, score=999)
        confirm = self.run_trial(project, source_run=candidate['id'], phase='confirmation')
        self.assertEqual([r['metrics']['score'] for r in confirm['repeats']], [1.01, 1.011])
        baseline_confirm = self.run_trial(project, source_run=baseline['id'], phase='confirmation')
        self.assertTrue(lab.compare(project, baseline_confirm['id'], confirm['id'])['mean_improvement'] > 3.9)
        self.assertEqual(confirm['source_sha256'], candidate['source_sha256'])
        report = render_report(project, confirm['id'])
        self.assertEqual([c['status'] for c in report['claims']], ['supported_in_scope', 'not_tested'])

    def test_no_candidate_without_valid_baseline(self):
        project = self.project()
        with self.assertRaisesRegex(lab.LabError, 'valid baseline'):
            self.run_trial(project)
        self.assertEqual(lab.records(project), [])

    def test_failed_baseline_is_preserved_and_can_be_repaired(self):
        project = self.project()
        self.configure(project, mode='crash')
        failed = self.run_trial(project, baseline=True)
        self.assertEqual(failed['status'], 'failed')
        self.configure(project, score=5)
        repaired = self.run_trial(project, baseline=True, parent=failed['id'])
        self.assertEqual(repaired['status'], 'completed')
        self.configure(project, score=1)
        self.assertEqual(self.run_trial(project, parent=repaired['id'])['status'], 'completed')
        self.assertEqual(len(lab.records(project)), 3)

    def test_invalid_results_never_become_scores(self):
        for mode in ['absent', 'missing', 'bool', 'nan', 'infinity', 'badcheck', 'outside']:
            with self.subTest(mode=mode):
                project = self.project()
                self.configure(project, mode=mode)
                record = self.run_trial(project, baseline=True)
                self.assertEqual(record['status'], 'invalid')
                self.assertIsNone(lab.summary(record, 'score')['mean'])
                self.assertEqual(len(record['repeats']), 1)
                self.assertTrue(lab.verify_record(project, record))

    def test_timeout_stops_process_and_preserves_logs(self):
        project = self.project({'budget': {'max_trials': 3, 'per_run_seconds': 0.5, 'total_seconds': 10}})
        self.configure(project, mode='sleep')
        record = self.run_trial(project, baseline=True)
        self.assertEqual(record['status'], 'timeout')
        self.assertLess(record['seconds'], 10)
        log = lab.state_dir(project) / 'trials' / record['id'] / 'repeats' / '1' / 'stdout.log'
        pid = int(log.read_text().strip())
        self.assertFalse(lab.process_alive(pid))
        self.assertTrue(lab.verify_record(project, record))

    def test_failed_trials_consume_trial_budget(self):
        project = self.project({'budget': {'max_trials': 1, 'per_run_seconds': 5, 'total_seconds': 10}})
        self.configure(project, mode='crash')
        self.run_trial(project, baseline=True)
        with self.assertRaisesRegex(lab.LabError, 'Trial budget exhausted'):
            self.run_trial(project, baseline=True)
        self.assertEqual(len(lab.records(project)), 1)

    def test_runtime_budget_prevents_another_process(self):
        project = self.project({'budget': {'max_trials': 5, 'per_run_seconds': 5, 'total_seconds': 0.2}})
        self.configure(project, mode='sleep')
        self.run_trial(project, baseline=True)
        with self.assertRaisesRegex(lab.LabError, 'Runtime budget exhausted'):
            self.run_trial(project, baseline=True)
        self.assertEqual(len(lab.records(project)), 1)

    def test_protected_evaluator_change_is_rejected_before_launch(self):
        project = self.project()
        (project / 'evaluate.py').write_text(EVALUATOR + '\n# changed', encoding='utf-8')
        with self.assertRaisesRegex(lab.LabError, 'Protected file changed'):
            self.run_trial(project, baseline=True)
        self.assertEqual(lab.records(project), [])

    def test_mutating_inputs_inside_run_is_invalid(self):
        project = self.project()
        self.configure(project, mode='mutate')
        record = self.run_trial(project, baseline=True)
        self.assertEqual(record['status'], 'invalid')
        self.assertIn('Input mutated', record['repeats'][0]['error'])
        self.assertEqual((project / 'evaluate.py').read_text(), EVALUATOR)

    def test_plan_changes_require_new_study(self):
        project = self.project()
        plan = lab.read_json(project / 'plan.json')
        plan['metric']['direction'] = 'maximize'
        lab.write_json(project / 'plan.json', plan)
        with self.assertRaisesRegex(lab.LabError, 'Plan changed'):
            self.run_trial(project, baseline=True)

    def test_record_result_and_snapshot_tampering_are_detected(self):
        for mode in ['result', 'snapshot', 'cache', 'truncated']:
            with self.subTest(mode=mode):
                project = self.project()
                record = self.run_trial(project, baseline=True)
                directory = lab.state_dir(project) / 'trials' / record['id']
                if mode == 'result':
                    lab.write_json(directory / 'repeats/1/result.json', {'metrics': {'score': -100}})
                elif mode == 'snapshot':
                    lab.write_json(directory / 'snapshot/config.json', {'score': -100})
                elif mode == 'cache':
                    record['repeats'][0]['metrics']['score'] = -100
                else:
                    record['repeats'].pop()
                with self.assertRaises(lab.LabError):
                    lab.verify_record(project, record)

    def test_no_development_or_candidate_switch_after_confirmation(self):
        project = self.project()
        baseline = self.run_trial(project, baseline=True)
        self.configure(project, score=1)
        chosen = self.run_trial(project, parent=baseline['id'])
        self.configure(project, score=0.5)
        other = self.run_trial(project, parent=baseline['id'])
        self.run_trial(project, source_run=chosen['id'], phase='confirmation')
        with self.assertRaisesRegex(lab.LabError, 'Confirmation has started'):
            self.run_trial(project)
        with self.assertRaisesRegex(lab.LabError, 'already selected'):
            self.run_trial(project, source_run=other['id'], phase='confirmation')

    def test_confirmation_requires_frozen_source(self):
        project = self.project()
        with self.assertRaisesRegex(lab.LabError, 'frozen development'):
            self.run_trial(project, phase='confirmation')

    def test_cross_phase_and_failed_comparisons_rejected(self):
        project = self.project()
        baseline = self.run_trial(project, baseline=True)
        self.configure(project, mode='crash')
        failed = self.run_trial(project)
        with self.assertRaisesRegex(lab.LabError, 'successful'):
            lab.compare(project, baseline['id'], failed['id'])
        confirm = self.run_trial(project, source_run=baseline['id'], phase='confirmation')
        with self.assertRaisesRegex(lab.LabError, 'protocols or phases'):
            lab.compare(project, baseline['id'], confirm['id'])

    def test_development_report_is_inconclusive_and_escaped(self):
        project = self.project({'title': '<script>bad()</script>'})
        record = self.run_trial(project, baseline=True)
        report = render_report(project, record['id'])
        self.assertEqual(report['claims'][0]['status'], 'inconclusive')
        rendered = Path(report['report']).read_text(encoding='utf-8')
        self.assertNotIn('<script>bad()', rendered)
        self.assertIn('&lt;script&gt;', rendered)

    def test_confirmation_preserves_negative_findings(self):
        project = self.project()
        baseline = self.run_trial(project, baseline=True)
        confirm = self.run_trial(project, source_run=baseline['id'], phase='confirmation')
        result = render_report(project, confirm['id'])
        self.assertEqual(result['claims'][0]['status'], 'not_supported_in_scope')

    def test_live_lock_owner_cannot_be_recovered(self):
        project = self.project()
        path = lab.state_dir(project) / 'lock.json'
        lab.write_json(path, {'pid': os.getpid()})
        with self.assertRaisesRegex(lab.LabError, 'alive'):
            lab.recover(project, 'test')
        self.assertTrue(path.exists())
        path.unlink()

    def test_dead_lock_recovery_marks_record_interrupted(self):
        project = self.project()
        record = self.run_trial(project, baseline=True)
        record['status'] = 'running'
        lab.write_json(lab.state_dir(project) / 'trials' / record['id'] / 'record.json', record)
        process = subprocess.Popen([sys.executable, '-c', 'pass'])
        process.wait()
        lab.write_json(lab.state_dir(project) / 'lock.json', {'pid': process.pid})
        self.assertEqual(lab.recover(project, 'simulated interrupted ledger')['recovered'], [record['id']])
        self.assertEqual(lab.get_record(project, record['id'])['status'], 'interrupted')

    def test_invalid_paths_and_malformed_plan_rejected(self):
        for changes in [{'files': ['../outside.py']}, {'files': ['config.json', './config.json']},
                        {'protected_files': [{}]}, {'metric': []}, {'budget': []},
                        {'confirmation_seeds': [1, 9]}, {'seeds': [True]},
                        {'claims': [None]}]:
            with self.subTest(changes=changes):
                project = self.project(changes, initialize=False)
                with self.assertRaises(lab.LabError):
                    lab.init(project)
                self.assertFalse(lab.state_dir(project).exists())

    def test_symlink_and_traversal_containment(self):
        for name in ['../file', '/absolute', 'C:/absolute', 'folder/../../file', 'folder\\file']:
            with self.subTest(path=name), self.assertRaises(lab.LabError):
                lab.contained(self.root, name)
        target = self.root / 'real.txt'
        target.write_text('fixture')
        link = self.root / 'link.txt'
        try:
            link.symlink_to(target)
        except OSError:
            self.skipTest('Symlink creation unavailable for current Windows user')
        with self.assertRaisesRegex(lab.LabError, 'Symlink'):
            lab.contained(self.root, 'link.txt')

    def test_report_cli_errors_are_structured(self):
        project = self.project()
        result = subprocess.run([sys.executable, str(Path(lab.__file__)), '--project', str(project),
                                 'report', '--run', 'not-an-id'], capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 2)
        self.assertIn('Invalid trial id', json.loads(result.stderr)['error'])
        self.assertNotIn('Traceback', result.stderr)

    def test_relative_improvement_does_not_hide_failed_conditions(self):
        project = self.project()
        baseline = self.run_trial(project, baseline=True)
        self.configure(project, score=3)
        candidate = self.run_trial(project)
        result = lab.compare(project, baseline['id'], candidate['id'])
        self.assertEqual(result['decision'], 'observed_improvement')
        self.assertFalse(result['candidate_conditions_met'])
        self.assertEqual(result['selection_status'], 'score_improved_conditions_failed')
        self.assertEqual(result['candidate_claims'][0]['status'], 'inconclusive')

    def test_mean_and_all_aggregation_preserve_individual_failures(self):
        for aggregation, expected in [('mean', 'supported_in_scope'), ('all', 'not_supported_in_scope')]:
            with self.subTest(aggregation=aggregation):
                project = self.project(initialize=False)
                plan = lab.read_json(project / 'plan.json')
                plan['claims'][0]['criterion']['aggregation'] = aggregation
                lab.write_json(project / 'plan.json', plan)
                lab.init(project)
                baseline = self.run_trial(project, baseline=True)
                lab.write_json(project / 'config.json', {'score': 1, 'scores': {'10': 0, '11': 3}})
                candidate = self.run_trial(project, parent=baseline['id'])
                confirm = self.run_trial(project, source_run=candidate['id'], phase='confirmation')
                report = render_report(project, confirm['id'])
                claim = report['claims'][0]
                self.assertEqual(claim['status'], expected)
                self.assertEqual(claim['passed_seeds'], 1)
                self.assertEqual(claim['expected_seeds'], 2)
                self.assertEqual([s['passed'] for s in claim['per_seed']], [True, False])
                self.assertEqual(claim['observed']['mean'], 1.5)
                self.assertEqual((claim['observed']['min'], claim['observed']['max']), (0, 3))

    def test_invalid_aggregation_rejected_before_registration(self):
        project = self.project(initialize=False)
        plan = lab.read_json(project / 'plan.json')
        plan['claims'][0]['criterion']['aggregation'] = 'best_seed'
        lab.write_json(project / 'plan.json', plan)
        with self.assertRaisesRegex(lab.LabError, 'aggregation'):
            lab.init(project)

    def test_modified_registered_copy_and_protected_list_are_rejected(self):
        for mode in ['plan', 'protected_files']:
            with self.subTest(mode=mode):
                project = self.project()
                path = lab.state_dir(project) / 'contract.json'
                frozen = lab.read_json(path)
                if mode == 'plan':
                    frozen['plan']['metric']['direction'] = 'maximize'
                else:
                    frozen['protected_sha256'] = {}
                lab.write_json(path, frozen)
                with self.assertRaises(lab.LabError):
                    self.run_trial(project, baseline=True)
                self.assertEqual(lab.records(project), [])

    def test_child_pid_is_saved_and_matches_executed_process(self):
        project = self.project()
        record = self.run_trial(project, baseline=True)
        for repeat in record['repeats']:
            log = lab.state_dir(project) / 'trials' / record['id'] / 'repeats' / str(repeat['seed']) / 'stdout.log'
            self.assertEqual(repeat['pid'], int(log.read_text().strip()))

    def test_recovery_refuses_live_experiment_when_runner_is_gone(self):
        project = self.project()
        record = self.run_trial(project, baseline=True)
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(15)'])
        owner = subprocess.Popen([sys.executable, '-c', 'pass'])
        owner.wait()
        path = lab.state_dir(project) / 'trials' / record['id'] / 'record.json'
        record['status'] = 'running'
        record['repeats'][-1].update(pid=child.pid, status='running')
        lab.write_json(path, record)
        lock = lab.state_dir(project) / 'lock.json'
        lab.write_json(lock, {'pid': owner.pid})
        try:
            with self.assertRaisesRegex(lab.LabError, 'Experiment process'):
                lab.recover(project, 'runner ended')
            self.assertTrue(lock.exists())
            self.assertEqual(lab.get_record(project, record['id'])['status'], 'running')
        finally:
            child.terminate()
            child.wait(timeout=5)
        self.assertEqual(lab.recover(project, 'experiment also ended')['recovered'], [record['id']])

    def test_csv_keeps_failure_and_unexecuted_seed_without_fake_zero(self):
        project = self.project()
        self.configure(project, mode='badcheck')
        record = self.run_trial(project, baseline=True)
        report = render_report(project, record['id'])
        with Path(report['csv']).open(encoding='utf-8-sig', newline='') as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual([row['repeat_status'] for row in rows], ['invalid', 'not_run'])
        self.assertEqual([row['value'] for row in rows], ['', ''])
        self.assertEqual(report['claims'][0]['evaluated_seeds'], 0)
        self.assertEqual(report['claims'][0]['status'], 'inconclusive')

    def test_csv_preserves_numeric_values_and_quotes_formula_labels(self):
        project = self.project()
        self.configure(project, score=-3)
        record = self.run_trial(project, baseline=True, label='=1+2')
        report = render_report(project, record['id'])
        with Path(report['csv']).open(encoding='utf-8-sig', newline='') as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(rows[0]['label'], "'=1+2")
        self.assertAlmostEqual(float(rows[0]['value']), -2.999)

    def test_report_links_reach_evidence_in_a_separate_output_directory(self):
        project = self.project()
        baseline = self.run_trial(project, baseline=True)
        self.configure(project, score=1)
        selected = self.run_trial(project)
        report = render_report(project, selected['id'], self.root / 'report-export')
        payload = lab.read_json(report['json'])
        self.assertEqual(payload['comparison']['baseline'], baseline['id'])
        self.assertEqual(payload['generator_version'], lab.VERSION)
        for trial in payload['trials']:
            evidence = Path(report['json']).parent / trial['evidence']
            self.assertEqual(lab.read_json(evidence)['id'], trial['id'])

    def test_report_output_cannot_overwrite_trial_sources(self):
        project = self.project()
        record = self.run_trial(project, baseline=True)
        snapshot = lab.state_dir(project) / 'trials' / record['id'] / 'snapshot'
        with self.assertRaisesRegex(lab.LabError, 'cannot overwrite'):
            render_report(project, record['id'], snapshot)
        self.assertTrue(lab.verify_record(project, record))

    def test_cli_failure_points_to_seed_and_logs(self):
        project = self.project()
        self.configure(project, mode='badcheck')
        proc = subprocess.run([sys.executable, str(Path(lab.__file__)), '--project', str(project), 'run',
                               '--baseline', '--label', 'invalid', '--hypothesis', 'fixture'],
                              capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(proc.returncode, 2)
        result = json.loads(proc.stdout)
        self.assertEqual(result['failure']['seed'], 1)
        self.assertIn('Required check', result['failure']['error'])
        self.assertTrue(Path(result['failure']['stderr']).is_file())
        self.assertTrue(Path(result['record']).is_file())


if __name__ == '__main__':
    unittest.main()
