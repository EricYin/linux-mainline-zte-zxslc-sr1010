#!/usr/bin/env python3
"""Cross-check saved PPU replay records against firmware and captured headers."""
import collections
import json
from pathlib import Path
import re
import struct


ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'out/acceptance/tso-trace'
CASES = ('ordinary', 'repeat-two', 'fork-two', 'repeat-entry-two',
         'repeat-entry-tail-retry', 'ipv6-repeat-entry-tail')


def main():
    firmware = (ROOT / 'out/mcode_intel.bin').read_bytes()
    results = {}
    for name in CASES:
        record = json.loads((OUT / name / 'record.json').read_text())
        rows = record['trace']
        summary = record['trace_summary']
        before = int(re.search(r'done_before=(\d+)', record['probe'])[1])
        source = int(re.search(r' dma=0x([0-9a-f]+)', record['probe'])[1], 16)
        groups = collections.defaultdict(list)
        for row in rows:
            groups[row.get('me')].append(row)
        contexts = []
        for me, trace in groups.items():
            final = trace[-1]
            header = b''.join(struct.pack('!I', word) for word in final['packet'][32:])
            length = 54 if record['version'] == 4 else 74
            matches = [i for i, frame in enumerate(record['frames'])
                       if bytes.fromhex(frame['frame'])[:length] == header[:length]]
            contexts.append({'me': me, 'steps': len(trace), 'entry': trace[0]['pc'],
                             'last_pc': final['pc'], 'wire_header_matches': matches,
                             'sequence_add_executions': sum(row['pc'] == 2091 for row in trace),
                             'internal_handle': f"{trace[0]['packet'][9]:08x}",
                             'packet28': f"{trace[0]['packet'][28]:08x}",
                             'packet29': f"{trace[0]['packet'][29]:08x}",
                             'pc_path': [row['pc'] for row in trace]})
        checks = {
            'wire_checks': all(record['checks'].values()),
            'trace_not_truncated': 0 < len(rows) == summary['steps'] < summary['requested'],
            'debugger_clean_exit': summary['result'] == summary['stopped'] == summary['busy'] == 0,
            'binary_record_size': len((OUT / name / 'trace.bin').read_bytes()) in
                                  (len(rows) * 536, len(rows) * 544),
            'replayed_original_instructions': all(int(row['instruction'], 16) ==
                struct.unpack_from('!Q', firmware, 16 + row['pc'] * 8)[0] for row in rows),
            'descriptor_done_before_traced_processing': all(row['done'] == before + 1 for row in rows),
            'each_context_terminated': all(c['last_pc'] in (2020, 2172) for c in contexts),
            'each_terminal_header_matches_one_wire_frame': all(len(c['wire_header_matches']) == 1
                                                               for c in contexts),
            'internal_handle_not_source_dma': all(row['packet'][9] != source for row in rows),
            'sampled_internal_handle_stable': all(len({row['packet'][9]
                                                     for row in trace}) == 1
                                                for trace in groups.values()),
        }
        if name in ('repeat-entry-tail-retry', 'ipv6-repeat-entry-tail'):
            checks['both_followup_headers_captured'] = sorted(
                i for context in contexts for i in context['wire_header_matches']) == [1, 2]
            checks['sequence_add_once_and_twice'] = sorted(
                c['sequence_add_executions'] for c in contexts) == [1, 2]
        if name in ('repeat-two', 'fork-two', 'repeat-entry-two'):
            checks['distinct_source_repeats_first_payload'] = (
                record['pattern'] and record['frames'][0]['payload'] == record['frames'][1]['payload'])
        results[name] = {'result': 'PASS' if all(checks.values()) else 'FAIL',
                         'checks': checks, 'contexts': contexts}
    report = {'result': 'PASS' if all(r['result'] == 'PASS' for r in results.values()) else 'FAIL',
              'passed': sum(sum(r['checks'].values()) for r in results.values()),
              'total': sum(len(r['checks']) for r in results.values()), 'cases': results,
              'excluded': {'child-two': 'PC2054 did not match a context; wire-only pass is not a trace pass.',
                           'repeat-entry-tail': 'Serial base64 export failed; reboot completed; replaced by retry.'},
              'scope': 'Template behavior and sampled PPU contexts only; no general TSO or final DMA-release proof.'}
    (OUT / 'audit.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({key: report[key] for key in ('result', 'passed', 'total')}))
    if report['result'] != 'PASS':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
