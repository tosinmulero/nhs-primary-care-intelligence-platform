"""Fix case-insensitive Power BI measure/column collision in Phase 8.

Run from the project's existing virtualenv. This script does not touch PostgreSQL
or any user data, and makes timestamped backups before editing the PBIP.
"""
from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sys

TABLE = 'mart_p7_registrations'
OLD = 'Registrations'
NEW = 'Total Registrations'
MODEL_REL = Path('powerbi/NHS_Primary_Care_Analytics.SemanticModel/model.bim')
GEN_REL = Path('scripts/phase8_powerbi.py')
VIS_REL = Path('powerbi/NHS_Primary_Care_Analytics.Report/definition/pages')


def json_bytes(data: object) -> bytes:
    return (json.dumps(data, indent=2, ensure_ascii=False) + '\n').encode('utf-8')


def walk_modify_visual(obj: object) -> int:
    """Update only measure projections, not column or title text."""
    updated = 0
    if isinstance(obj, dict):
        proj = obj.get('field')
        if isinstance(proj, dict):
            m = proj.get('Measure')
            if isinstance(m, dict) and m.get('Expression', {}).get('SourceRef', {}).get('Entity') == TABLE:
                if m.get('Property') == OLD:
                    m['Property'] = NEW
                    updated += 1
                if m.get('Property') == NEW:
                    old_ref = f'{TABLE}.{OLD}'
                    if obj.get('queryRef') == old_ref:
                        obj['queryRef'] = f'{TABLE}.{NEW}'
                    if obj.get('nativeQueryRef') == OLD:
                        obj['nativeQueryRef'] = NEW
        for item in obj.values():
            updated += walk_modify_visual(item)
    elif isinstance(obj, list):
        for item in obj:
            updated += walk_modify_visual(item)
    return updated


def check_model(model: dict) -> None:
    for table in model['model']['tables']:
        columns = {str(col['name']).casefold() for col in table.get('columns', [])}
        measures = [str(m['name']) for m in table.get('measures', [])]
        if len({m.casefold() for m in measures}) != len(measures):
            raise ValueError(f'Duplicate measure names in {table["name"]}')
        for name in measures:
            if name.casefold() in columns:
                raise ValueError(f'Measure/column name conflict in {table["name"]}: {name}')


def check_visuals(visuals: dict[Path, dict], model: dict) -> None:
    tables = {table['name']:table for table in model['model']['tables']}
    measure_names = {
        t:{m['name'] for m in spec.get('measures', [])}
        for t,spec in tables.items()
    }
    def walk(obj):
        if isinstance(obj, dict):
            fld = obj.get('field')
            if isinstance(fld, dict) and 'Measure' in fld:
                m = fld['Measure']
                t = m['Expression']['SourceRef']['Entity']
                name = m['Property']
                if name not in measure_names.get(t, set()):
                    raise ValueError(f'Visual references missing measure: {t}.{name}')
                if obj.get('queryRef') not in (None, f'{t}.{name}'):
                    raise ValueError(f'Visual queryRef differs from measure {t}.{name}: {obj.get("queryRef")}')
            for x in obj.values():
                walk(x)
        elif isinstance(obj, list):
            for x in obj:
                walk(x)
    for obj in visuals.values():
        walk(obj)


def run(root: Path) -> None:
    root = root.expanduser().resolve()
    model_path = root / MODEL_REL
    gen_path = root / GEN_REL
    vis_root = root / VIS_REL
    for req in (model_path, gen_path, vis_root):
        if not req.exists():
            raise FileNotFoundError(f'Expected Phase 8 file/folder not found: {req}')

    model = json.loads(model_path.read_text(encoding='utf-8-sig'))
    table = next((t for t in model['model']['tables'] if t['name'] == TABLE), None)
    if table is None:
        raise ValueError('Expected registrations mart is missing')
    if not any(c['name'].casefold() == OLD.casefold() for c in table['columns']):
        raise ValueError('Expected registrations column is missing')
    old_measures = [m for m in table.get('measures',[]) if m['name'] == OLD]
    new_measures = [m for m in table.get('measures',[]) if m['name'] == NEW]
    if old_measures and new_measures:
        raise ValueError('Both old and new measure names exist: refusing ambiguous repair')
    if len(old_measures) > 1:
        raise ValueError('Duplicate source measure: refusing ambiguous repair')
    for m in old_measures:
        m['name'] = NEW

    gen = gen_path.read_text(encoding='utf-8-sig')
    old_def = "('Registrations','SUM(mart_p7_registrations[registrations])'"
    new_def = "('Total Registrations','SUM(mart_p7_registrations[registrations])'"
    old_chart = "'registration_month','Registrations','lineChart'"
    new_chart = "'registration_month','Total Registrations','lineChart'"
    if gen.count(old_def) not in (0,1) or gen.count(old_chart) not in (0,2):
        raise ValueError('Unexpected generator definitions; refusing to guess')
    if old_def not in gen and new_def not in gen:
        raise ValueError('Unable to locate original measure in generator')
    if old_chart not in gen and new_chart not in gen:
        raise ValueError('Unable to locate registration chart in generator')
    new_gen = gen.replace(old_def, new_def).replace(old_chart, new_chart)
    ast.parse(new_gen, filename=str(gen_path))

    visuals: dict[Path,dict] = {}
    changed_visual_paths: list[Path] = []
    total_bindings = 0
    files = sorted(vis_root.rglob('visual.json'))
    if len(files) < 50:
        raise ValueError(f'Expected six-page project, found only {len(files)} visuals')
    for path in files:
        vis = json.loads(path.read_text(encoding='utf-8-sig'))
        before = json_bytes(vis)
        changed = walk_modify_visual(vis)
        total_bindings += changed
        if json_bytes(vis) != before:
            changed_visual_paths.append(path)
        visuals[path] = vis

    check_model(model)
    check_visuals(visuals, model)
    # Ensure every old registration measure reference is gone.
    for path, vis in visuals.items():
        data = json.dumps(vis)
        if f'"Property": "{OLD}"' in data and f'"Entity": "{TABLE}"' in data:
            raise ValueError(f'Potential legacy registration measure reference in {path}')

    desired: dict[Path, bytes] = {}
    new_model = json_bytes(model)
    if model_path.read_bytes() != new_model:
        desired[model_path] = new_model
    if gen_path.read_text(encoding='utf-8-sig') != new_gen:
        desired[gen_path] = new_gen.encode('utf-8')
    for path in changed_visual_paths:
        desired[path] = json_bytes(visuals[path])

    if not desired:
        print('PHASE 8 REPAIR ALREADY APPLIED — no source files changed.')
    else:
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        backup_root = root / 'data' / 'processed' / ('phase8_registration_repair_backup_' + stamp)
        if backup_root.exists():
            raise FileExistsError(f'Backup directory already exists: {backup_root}')
        for path in desired:
            target = backup_root / path.relative_to(root)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
        for path, data in desired.items():
            tmp = path.with_name(path.name + '.repair-tmp')
            tmp.write_bytes(data)
            tmp.replace(path)
        print(f'BACKUP: {backup_root}')
        print(f'CHANGED: {len(desired)} files, {total_bindings} visual measure projections')

    # Read them again from disk and confirm no measure/column collisions.
    final_model = json.loads(model_path.read_text(encoding='utf-8'))
    check_model(final_model)
    for path in files:
        visuals[path] = json.loads(path.read_text(encoding='utf-8'))
    check_visuals(visuals, final_model)
    print('PHASE 8 REPAIR PASS — registration measure renamed, visual references consistent, no table measure/column collisions.')
    print('Next: reopen the .pbip in Power BI Desktop and check for any additional rendering errors.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--project-root', required=True)
    args = parser.parse_args()
    try:
        run(Path(args.project_root))
    except Exception as exc:
        print(f'PHASE 8 REPAIR FAILED: {exc}', file=sys.stderr)
        raise
