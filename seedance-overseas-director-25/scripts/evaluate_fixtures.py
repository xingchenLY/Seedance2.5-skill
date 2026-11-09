"""Maintenance-only rendering benchmark; does not measure model generation or video quality."""
import argparse
import json
from pathlib import Path
from time import perf_counter

from render_prompt import render_plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    cases = []
    for path in sorted((Path(__file__).resolve().parent.parent/'assets'/'evaluation').glob('*.json')):
        start = perf_counter()
        record = {'case': path.stem}
        try:
            authored = json.loads(path.read_text(encoding='utf-8'))
            plan, text = render_plan(authored)
            record.update(status='passed', prompt_characters=len(text), segments=len(plan['segments']),
                          content_review_status='not_run',
                          pending_content_checks=authored['evaluation']['manual_checks'])
        except (OSError, ValueError, KeyError, TypeError) as exc:
            record.update(status='failed', error=str(exc))
        record['render_seconds'] = round(perf_counter()-start, 6)
        cases.append(record)
    report = {'scope':'Local rendering and deterministic validation only; no model/video speed or quality measurement.',
              'cases':cases, 'manual_generation_comparison':'not_run'}
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n',encoding='utf-8')
    passed = bool(cases) and all(c['status']=='passed' for c in cases)
    print(f'{sum(c["status"]=="passed" for c in cases)}/{len(cases)} render fixtures passed; content review not run by this script')
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
