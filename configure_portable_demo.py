"""Configure a local, IGNORED copy of this public NHS Power BI template.

From the release candidate folder: python configure_portable_demo.py
Then open .local_demo/powerbi/NHS_Primary_Care_Analytics.pbip in Power BI Desktop.
This does NOT edit the publishable powerbi_template or aggregate CSVs.
"""
from pathlib import Path
import re, shutil, sys, json

BASE='NHS_Primary_Care_Analytics'
TOKEN='__NHS_DEMO_AGGREGATE_ROOT__'
REF=re.compile(r'File\.Contents\(\s*"' + re.escape(TOKEN) + r'\\([^"\\]+\.csv)"\s*\)', re.I)
MARTS={
'mart_p7_executive','mart_p7_demographics','mart_p7_registrations','mart_p7_appointments',
'mart_p7_record_quality','mart_p7_inbox_aging','mart_p7_admin_tasks','mart_p7_workflow_queues',
'mart_p7_qof_demo','mart_p7_data_quality','mart_p7_audit_activity','mart_p7_demo_cohorts'}

def main():
    root=Path(__file__).resolve().parent
    out=root/'.local_demo'
    src=root/'powerbi_template'
    if out.exists():
        raise RuntimeError('Local copy already exists. Delete .local_demo manually only if you want to rebuild it.')
    for m in MARTS:
        if not (root/'data'/'aggregate'/f'{m}.csv').is_file():
            raise RuntimeError(f'Missing aggregate file: {m}')
    if not (src/f'{BASE}.pbip').is_file():
        raise RuntimeError('PBIP template is missing')
    tmp=root/'.local_demo_building'
    if tmp.exists():
        raise RuntimeError('A previous temporary build exists; inspect it before retrying')
    try:
        shutil.copytree(src,tmp/'powerbi',ignore=shutil.ignore_patterns('.pbi','*.abf'))
        model=tmp/'powerbi'/f'{BASE}.SemanticModel'
        paths=[model/'model.bim'] if (model/'model.bim').is_file() else list(model.rglob('*.tmdl'))
        found=set(); count=0
        for path in paths:
            if not path.is_file():continue
            def swap(match):
                nonlocal count
                name=match.group(1)
                if Path(name).stem not in MARTS:raise ValueError('Unexpected CSV dependency')
                found.add(Path(name).stem); count+=1
                winpath=str((root/'data'/'aggregate'/name).resolve()).replace('"','""')
                return 'File.Contents("'+winpath+'")'
            if path.name=='model.bim':
                obj=json.loads(path.read_text(encoding='utf-8-sig'))
                for table in obj.get('model',{}).get('tables',[]):
                    for part in table.get('partitions',[]):
                        source=part.get('source',{})
                        expr=source.get('expression')
                        if isinstance(expr,list):
                            source['expression']=[REF.sub(swap,line) for line in expr]
                        elif isinstance(expr,str):
                            source['expression']=REF.sub(swap,expr)
                path.write_text(json.dumps(obj,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
            else:
                content=path.read_text(encoding='utf-8-sig')
                new=REF.sub(swap,content)
                if new!=content:path.write_text(new,encoding='utf-8')
        if found!=MARTS or count!=12:
            raise RuntimeError(f'Expected 12 configured marts, found {count} references in {len(found)} marts')
        (tmp/'LOCAL_ONLY.txt').write_text('Locally configured copy. Never commit this folder.\n',encoding='utf-8')
        tmp.rename(out)
    except Exception:
        if tmp.exists():shutil.rmtree(tmp)
        raise
    print('LOCAL POWER BI CONFIGURATION PASS - 12 aggregate sources rebound')
    print('OPEN:',out/'powerbi'/f'{BASE}.pbip')
    print('Do not commit .local_demo; it contains local absolute data paths.')

if __name__=='__main__':
    try:main()
    except Exception as e:
        print('PORTABLE DEMO CONFIGURATION FAILED:',e,file=sys.stderr)
        sys.exit(1)
