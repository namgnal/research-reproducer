"""Render portable HTML, CSV and JSON from verified experiment evidence."""
import csv
import html
import os
from pathlib import Path
from urllib.parse import urlparse
from assessment import assess_claims, conditions_met
from lab import VERSION, compare, contract, get_record, records, require, state_dir, summary, verify_record, write_json


def esc(value):
    return html.escape(str(value), quote=True)


def fmt(value):
    return '—' if value is None else f'{value:.6g}'


def csv_value(value):
    if isinstance(value, str) and value[:1] in ['=', '+', '-', '@', '\t', '\r', '\n']:
        return "'" + value
    return value


def evidence_link(output, target):
    try:
        return Path(os.path.relpath(target, output)).as_posix()
    except ValueError:
        return Path(target).resolve().as_uri()


def export_metrics(path, plan, all_records):
    fields = ['run_id', 'label', 'phase', 'trial_status', 'seed', 'repeat_status', 'metric', 'value', 'error']
    with path.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for run in all_records:
            seeds = plan['confirmation_seeds'] if run['phase'] == 'confirmation' else plan['seeds']
            repeats = {r['seed']: r for r in run['repeats']}
            for seed in seeds:
                repeat = repeats.get(seed, {})
                metrics = repeat.get('metrics') if repeat.get('status') == 'completed' else None
                for metric, value in (metrics or {plan['metric']['name']: None}).items():
                    row = {'run_id': run['id'], 'label': run['label'], 'phase': run['phase'],
                           'trial_status': run['status'], 'seed': seed, 'repeat_status': repeat.get('status', 'not_run'),
                           'metric': metric, 'value': value, 'error': repeat.get('error', run.get('error', ''))}
                    writer.writerow({key: csv_value(value) for key, value in row.items()})


def render_report(project, selected_id, output=None, baseline_id=None):
    frozen = contract(project)
    plan = frozen['plan']
    selected = get_record(project, selected_id)
    all_records = records(project)
    for record in all_records:
        verify_record(project, record)
    output = (Path(output) if output else state_dir(project) / 'report').resolve()
    trials_dir = state_dir(project) / 'trials'
    require(output != trials_dir and trials_dir not in output.parents, 'Report output cannot overwrite trial evidence')
    reserved = {(Path(project).resolve() / name).resolve() for name in [frozen['plan_file'], *plan['files']]}
    require(not any(output / name in reserved for name in ['index.html', 'report.json', 'metrics.csv']),
            'Report output conflicts with registered input files')
    claims = assess_claims(plan, selected)
    candidates = [r for r in all_records if r['baseline'] and r['status'] == 'completed' and r['phase'] == selected['phase']]
    comparison = None
    comparison_note = '所选记录尚未完成，或当前阶段没有唯一有效基线；可用 --baseline 明确指定对照。'
    if selected['baseline']:
        comparison_note = '所选记录是基线；本报告展示其条件验收结果。'
    if selected['status'] == 'completed':
        chosen = baseline_id or (candidates[0]['id'] if len(candidates) == 1 and not selected['baseline'] else None)
        if chosen:
            comparison = compare(project, chosen, selected_id)
    result = {'generator_version': VERSION, 'title': plan['title'], 'question': plan['question'], 'scope': plan['scope'],
              'reproduction_level': plan['reproduction_level'], 'selected_run': selected_id,
              'selected_phase': selected['phase'], 'plan_sha256': frozen['plan_sha256'],
              'claims': claims, 'conditions_met': conditions_met(claims), 'metric': plan['metric'],
              'comparison': comparison, 'metrics_csv': 'metrics.csv',
              'trials': [{**{k: r[k] for k in ['id', 'label', 'hypothesis', 'parent', 'source_run', 'phase', 'baseline', 'status', 'seconds']},
                          'summary': summary(r, plan['metric']['name']), 'source_sha256': r['source_sha256'],
                          'expected_repeats': len(plan['confirmation_seeds'] if r['phase'] == 'confirmation' else plan['seeds']),
                          'evidence': evidence_link(output, state_dir(project) / 'trials' / r['id'] / 'record.json')} for r in all_records],
              'limitations': [
                  'Claims apply only within the declared scope; this is not an official PaperBench score.',
                  'Mean aggregation and per-seed pass rates answer different questions; aggregation is frozen in the plan.',
                  'Repeated-seed summaries are descriptive, not a statistical significance test.',
                  'Snapshots and checksums are evidence checks, not an operating-system sandbox.',
                  'The host agent authors hypotheses and implementations; this helper executes and records them.',
              ]}
    output.mkdir(parents=True, exist_ok=True)
    export_metrics(output / 'metrics.csv', plan, all_records)
    write_json(output / 'report.json', result)
    labels = {'supported_in_scope': '范围内得到支持', 'not_supported_in_scope': '范围内未获支持',
              'not_tested': '未验证', 'inconclusive': '证据不足'}
    claim_rows = ''
    for claim in claims:
        url = claim['source_url']
        link = f'<a href="{esc(url)}">来源</a>' if urlparse(url).scheme in ['http', 'https'] else esc(url)
        criterion = claim['criterion']
        rule, measured, details = '未设验收规则', '—', ''
        if criterion:
            symbol = '≤' if criterion['operator'] == 'le' else '≥'
            aggregate = '每个种子均达标' if claim['aggregation'] == 'all' else '按种子均值判断'
            rule = f'{criterion["metric"]} {symbol} {criterion["threshold"]} · {aggregate}'
            observed = claim['observed']
            if observed:
                measured = f'均值 {fmt(observed["mean"])}；范围 {fmt(observed["min"])}–{fmt(observed["max"])}'
            seed_rows = ''
            for r in claim['per_seed']:
                verdict = '通过' if r['passed'] is True else ('未通过' if r['passed'] is False else '未评价')
                seed_rows += f'<tr><td>{r["seed"]}</td><td>{fmt(r["value"])}</td><td>{esc(r["run_status"])}</td><td>{verdict}</td></tr>'
            note = ''
            if claim['condition_met'] is True and claim['passed_seeds'] < claim['expected_seeds']:
                note = '<p class="attention">聚合条件通过，仍有种子未达阈值；请按预先声明的聚合规则解释。</p>'
            details = f'<details><summary>逐种子达标 {claim["passed_seeds"]}/{claim["expected_seeds"]}；已评价 {claim["evaluated_seeds"]}</summary>{note}<table><thead><tr><th>种子</th><th>值</th><th>运行状态</th><th>单种子条件</th></tr></thead><tbody>{seed_rows}</tbody></table></details>'
        claim_rows += f'<tr><td>{esc(claim["id"])}</td><td>{esc(claim["statement"])}<small>{link} · {esc(claim["source_location"])}</small></td><td>{esc(rule)}<small>{esc(measured)}</small>{details}</td><td>{labels[claim["status"]]}</td></tr>'
    run_rows = ''
    for run in result['trials']:
        value = run['summary']
        run_rows += f'<tr><td>{esc(run["label"])}<small><a href="{esc(run["evidence"])}">{esc(run["id"])}</a></small></td><td>{esc(run["phase"])}</td><td>{esc(run["status"])}</td><td>{fmt(value["mean"])}</td><td>{fmt(value["stdev"])}</td><td>{value["n"]}/{run["expected_repeats"]}</td><td>{run["seconds"]:.2f}s</td></tr>'
    compare_html = f'<p>{esc(comparison_note)}</p>'
    if comparison:
        met = comparison['candidate_conditions_met']
        condition_label = '满足本次数值条件' if met is True else ('未满足全部数值条件' if met is False else '条件证据不足')
        score_label = '主指标出现改善' if comparison['decision'] == 'observed_improvement' else '主指标未达到最小改善量'
        compare_html = f'<div class="comparison"><div><small>配对指标</small><strong>{score_label}</strong><p>基线 {fmt(comparison["baseline_summary"]["mean"])} → 候选 {fmt(comparison["candidate_summary"]["mean"])}</p></div><div><small>绝对条件</small><strong>{condition_label}</strong><p>开发阶段只作候选筛选，逐项结论仍需确认阶段证据。</p></div></div>'
    page = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(plan['title'])}</title><style>
body{{font:15px/1.7 system-ui,"Microsoft YaHei",sans-serif;color:#223743;background:#f5f7fa;margin:0}}main{{max-width:1120px;margin:32px auto;padding:36px;background:white}}h1{{font-size:29px;margin:0 0 12px}}h2{{font-size:21px;color:#236184;margin-top:30px}}p{{max-width:95ch}}.meta,small{{color:#697e8b}}small{{display:block;font-size:12px;overflow-wrap:anywhere}}.table-scroll{{overflow:auto}}table{{border-collapse:collapse;width:100%;font-size:13px}}th,td{{padding:11px 12px;text-align:left;vertical-align:top;border-bottom:1px solid #d9e3e9}}th{{background:#edf4f8}}a{{color:#22688e}}code{{overflow-wrap:anywhere}}details{{margin-top:8px}}summary{{cursor:pointer;color:#236184}}.attention{{color:#8a5b10}}.comparison{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}.comparison>div{{background:#f2f7fa;border-left:3px solid #397596;padding:16px}}.comparison p{{font-size:13px;margin:8px 0 0}}@media(max-width:700px){{main{{margin:0;padding:20px}}.comparison{{grid-template-columns:1fr}}h1{{font-size:24px}}}}@media print{{body{{background:white}}main{{margin:0;padding:0}}}}
</style><main><p class="meta">RESEARCH REPRODUCER {VERSION} · 实验证据报告</p><h1>{esc(plan['title'])}</h1>
<p>{esc(plan['question'])}</p><p><b>验证范围：</b>{esc(plan['scope'])}</p>
<p class="meta">复现层级：{esc(plan['reproduction_level'])} · 所选记录：{esc(selected_id)} · 阶段：{esc(selected['phase'])}</p>
<p><a href="metrics.csv">导出逐种子指标 CSV</a> · <a href="report.json">查看结构化报告 JSON</a></p>
<h2>相对改善与绝对条件</h2>{compare_html}
<h2>主张与验证结果</h2><div class="table-scroll"><table><thead><tr><th>ID</th><th>研究主张</th><th>预设条件与逐种子结果</th><th>判断</th></tr></thead><tbody>{claim_rows}</tbody></table></div>
<h2>完整实验记录</h2><p class="meta">主指标：{esc(plan['metric']['name'])} / {esc(plan['metric']['direction'])}。有效重复数同时显示计划总数；失败与未执行不会用零分替代。</p>
<div class="table-scroll"><table><thead><tr><th>试验 / 原始记录</th><th>阶段</th><th>状态</th><th>均值</th><th>标准差</th><th>有效重复</th><th>用时</th></tr></thead><tbody>{run_rows}</tbody></table></div>
<h2>复查方法</h2><p>点击每轮 ID 可查看记录，其中包含命令、Python 版本、种子和产物哈希。代码快照与逐种子原始结果保存在相邻的 snapshot 和 repeats 目录。CSV 可单独分析；复核原始证据需要同时保留实验工程。</p>
<p class="meta">协议 SHA-256：<code>{esc(frozen['plan_sha256'])}</code></p>
<h2>解释边界</h2><p>结论只覆盖声明范围与预设条件。按均值判断与要求每个种子达标是不同协议，聚合方式不能在看过结果后更改。多随机种子用于描述波动，不自动构成统计显著性。文件校验用于发现漂移，代码执行需要符合当前环境的授权与隔离要求。</p>
</main></html>'''
    (output / 'index.html').write_text(page, encoding='utf-8')
    return {'report': str(output / 'index.html'), 'json': str(output / 'report.json'),
            'csv': str(output / 'metrics.csv'), 'claims': claims, 'comparison': comparison}
