"""Build a source-controlled PBIP from Phase 7 aggregate reporting marts.
No real NHS records. The generated PBIP requires visual inspection in Power BI Desktop.
"""
from __future__ import annotations
import argparse
import csv
import getpass
import hashlib
import json
import os
import re
import shutil
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT_SCHEMA = 'https://developer.microsoft.com/json-schemas/fabric/'
VISUAL_SCHEMA = ROOT_SCHEMA + 'item/report/definition/visualContainer/2.7.0/schema.json'
REPORT_SCHEMA = ROOT_SCHEMA + 'item/report/definition/report/3.2.0/schema.json'
PAGE_SCHEMA = ROOT_SCHEMA + 'item/report/definition/page/2.1.0/schema.json'
BASE = 'NHS_Primary_Care_Analytics'
MARTS = [
 'mart_p7_executive', 'mart_p7_demographics', 'mart_p7_registrations',
 'mart_p7_appointments', 'mart_p7_record_quality', 'mart_p7_inbox_aging',
 'mart_p7_admin_tasks', 'mart_p7_workflow_queues', 'mart_p7_qof_demo',
 'mart_p7_data_quality', 'mart_p7_audit_activity', 'mart_p7_demo_cohorts',
]
# Exact columns of existing dbt marts; prevent surprising new fields from being imported.
COLUMNS = {
 'mart_p7_executive':['reporting_date','registered_patients','appointments_total','correspondence_backlog','pathology_pending_review','tasks_outstanding','open_data_quality_issues','intake_rows','accepted_rows','rejected_rows','intake_acceptance_pct','total_demo_cases','awaiting_admin','awaiting_clinician','demo_approved','demo_rejected','pct_awaiting_review'],
 'mart_p7_demographics':['area_group','sex_at_registration','age_band','patient_count','pct_population'],
 'mart_p7_registrations':['registration_month','status','registrations'],
 'mart_p7_appointments':['appointment_month','status','appointment_count'],
 'mart_p7_record_quality':['record_type','sub_type','status','record_count'],
 'mart_p7_inbox_aging':['workstream','record_category','status','age_band','record_count'],
 'mart_p7_admin_tasks':['task_type','task_status','due_bucket','task_count'],
 'mart_p7_workflow_queues':['source_kind','case_status','case_count'],
 'mart_p7_qof_demo':['measurement_month','indicator_id','indicator_name','denominator_count','numerator_count','demo_percent','indicator_classification'],
 'mart_p7_data_quality':['detected_date','rule_code','severity','issue_status','issue_count'],
 'mart_p7_audit_activity':['event_day','actor_type','action','event_count'],
 'mart_p7_demo_cohorts':['code_system','concept_label','simulated_patient_count','suppressed_small_cell']
}
INTEGER = set(('registered_patients appointments_total correspondence_backlog pathology_pending_review tasks_outstanding '
'open_data_quality_issues intake_rows accepted_rows rejected_rows total_demo_cases awaiting_admin awaiting_clinician '
'demo_approved demo_rejected patient_count registrations appointment_count record_count task_count case_count '
'denominator_count numerator_count issue_count event_count simulated_patient_count').split())
DECIMAL = {'intake_acceptance_pct','pct_awaiting_review','pct_population','demo_percent'}
DATE = {'reporting_date','registration_month','appointment_month','measurement_month','detected_date','event_day'}
BOOLEAN = {'suppressed_small_cell'}
MEASURES = {
 'mart_p7_executive':[
  ('Registered Patients','MAX(mart_p7_executive[registered_patients])','#,0'),
  ('Total Appointments','MAX(mart_p7_executive[appointments_total])','#,0'),
  ('Outstanding Tasks','MAX(mart_p7_executive[tasks_outstanding])','#,0'),
  ('Awaiting Clinician','MAX(mart_p7_executive[awaiting_clinician])','#,0'),
  ('Backlogged Letters','MAX(mart_p7_executive[correspondence_backlog])','#,0'),
  ('Pending Pathology','MAX(mart_p7_executive[pathology_pending_review])','#,0'),
  ('Open Quality Issues','MAX(mart_p7_executive[open_data_quality_issues])','#,0'),
  ('Acceptance Rate','MAX(mart_p7_executive[intake_acceptance_pct])','0.0"%"'),
 ],
 'mart_p7_demographics':[('Population by Segment','SUM(mart_p7_demographics[patient_count])','#,0')],
 'mart_p7_registrations':[('Total Registrations','SUM(mart_p7_registrations[registrations])','#,0')],
 'mart_p7_appointments':[('Appointments','SUM(mart_p7_appointments[appointment_count])','#,0')],
 'mart_p7_record_quality':[('Record Count','SUM(mart_p7_record_quality[record_count])','#,0')],
 'mart_p7_inbox_aging':[('Inbox Records','SUM(mart_p7_inbox_aging[record_count])','#,0')],
 'mart_p7_admin_tasks':[('Task Count','SUM(mart_p7_admin_tasks[task_count])','#,0')],
 'mart_p7_workflow_queues':[('Workflow Cases','SUM(mart_p7_workflow_queues[case_count])','#,0')],
 'mart_p7_qof_demo':[('Mean Demo Percentage','AVERAGE(mart_p7_qof_demo[demo_percent])','0.0"%"')],
 'mart_p7_data_quality':[('Quality Flags','SUM(mart_p7_data_quality[issue_count])','#,0')],
 'mart_p7_audit_activity':[('Audit Events','SUM(mart_p7_audit_activity[event_count])','#,0')],
 'mart_p7_demo_cohorts':[('Cohort Records','SUM(mart_p7_demo_cohorts[simulated_patient_count])','#,0')],
}
PAGES = [
 {'title':'01  Executive Practice Overview','subtitle':'8,000 fictional patients  |  operational performance at a glance','cards':['Registered Patients','Total Appointments','Outstanding Tasks','Acceptance Rate'],
  'charts':[('Monthly registrations','mart_p7_registrations','registration_month','Total Registrations','lineChart'),('Appointments by status','mart_p7_appointments','status','Appointments','columnChart'),('Workflow review status','mart_p7_workflow_queues','case_status','Workflow Cases','barChart'),('Administrative tasks','mart_p7_admin_tasks','task_type','Task Count','donutChart')],
  'slicers':[('mart_p7_appointments','status')]},
 {'title':'02  Patient Demographics','subtitle':'Population profile by age, registration sex and broad area','cards':['Registered Patients','Total Appointments','Open Quality Issues','Acceptance Rate'],
  'charts':[('Age distribution','mart_p7_demographics','age_band','Population by Segment','columnChart'),('Sex at registration','mart_p7_demographics','sex_at_registration','Population by Segment','donutChart'),('Area groups','mart_p7_demographics','area_group','Population by Segment','barChart'),('Registration history','mart_p7_registrations','registration_month','Total Registrations','lineChart')],
  'slicers':[('mart_p7_demographics','age_band'),('mart_p7_demographics','area_group')]},
 {'title':'03  Clinical Coding & Record Quality','subtitle':'Illustrative terminology and administrative record completeness','cards':['Open Quality Issues','Registered Patients','Awaiting Clinician','Acceptance Rate'],
  'charts':[('Coding review states','mart_p7_record_quality','status','Record Count','columnChart'),('Record types','mart_p7_record_quality','record_type','Record Count','donutChart'),('Data quality rules','mart_p7_data_quality','rule_code','Quality Flags','barChart'),('Issue severity','mart_p7_data_quality','severity','Quality Flags','columnChart')],
  'slicers':[('mart_p7_record_quality','record_type'),('mart_p7_data_quality','severity')]},
 {'title':'04  Pathology & Correspondence','subtitle':'Fictional result routing, correspondence and age-band queues','cards':['Pending Pathology','Backlogged Letters','Awaiting Clinician','Outstanding Tasks'],
  'charts':[('Inbox by workstream','mart_p7_inbox_aging','workstream','Inbox Records','columnChart'),('Review state','mart_p7_inbox_aging','status','Inbox Records','barChart'),('Age buckets','mart_p7_inbox_aging','age_band','Inbox Records','columnChart'),('Task due buckets','mart_p7_admin_tasks','due_bucket','Task Count','donutChart')],
  'slicers':[('mart_p7_inbox_aging','workstream'),('mart_p7_inbox_aging','status')]},
 {'title':'05  Clinical Searches & QOF-style Intelligence','subtitle':'Demonstration cohorts only  |  NOT official QOF indicators','cards':['Registered Patients','Mean Demo Percentage','Open Quality Issues','Acceptance Rate'],
  'charts':[('Cohort categories','mart_p7_demo_cohorts','concept_label','Cohort Records','barChart'),('QOF-style indicators','mart_p7_qof_demo','indicator_name','Mean Demo Percentage','columnChart'),('Demo trends by month','mart_p7_qof_demo','measurement_month','Mean Demo Percentage','lineChart'),('Code systems','mart_p7_demo_cohorts','code_system','Cohort Records','donutChart')],
  'slicers':[('mart_p7_qof_demo','indicator_name'),('mart_p7_demo_cohorts','code_system')]},
 {'title':'06  Data Governance & Operations','subtitle':'Data-quality flags, administrative workflows and audit patterns','cards':['Open Quality Issues','Outstanding Tasks','Awaiting Clinician','Acceptance Rate'],
  'charts':[('Quality issue trends','mart_p7_data_quality','detected_date','Quality Flags','lineChart'),('Audit event activity','mart_p7_audit_activity','event_day','Audit Events','columnChart'),('Tasks by status','mart_p7_admin_tasks','task_status','Task Count','donutChart'),('Queue workstream','mart_p7_workflow_queues','source_kind','Workflow Cases','barChart')],
  'slicers':[('mart_p7_admin_tasks','task_status'),('mart_p7_data_quality','severity')]},
]


def dump(path:Path, obj):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(obj,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')

def stable_id(s):return hashlib.sha1(s.encode('utf-8')).hexdigest()[:20]

def lit(x):return {'expr':{'Literal':{'Value':x}}}

def field(table, column, measure=False):
    return {'Measure' if measure else 'Column':{'Expression':{'SourceRef':{'Entity':table}},'Property':column}}

def projection(table, col, measure=False):
    return {'field':field(table,col,measure),'queryRef':f'{table}.{col}','nativeQueryRef':col}

def container_title(title):
    return {'title':[{'properties':{'show':lit('true'),'text':lit("'"+title.replace("'","''")+"'"), 'fontSize':lit('13D'),'bold':lit('true'), 'fontColor':{'solid':{'color':lit("'#112744'")}}}}],
            'background':[{'properties':{'show':lit('true'),'color':{'solid':{'color':lit("'#FFFFFF'")}},'transparency':lit('0D')}}],
            'border':[{'properties':{'show':lit('true'),'color':{'solid':{'color':lit("'#DFE8F5'")}},'radius':lit('9D')}}]}

def visual_json(name,rect,vtype,roles=None,title=None,objects=None):
    x,y,w,h=rect
    v={'visualType':vtype,'drillFilterOtherVisuals':True}
    if roles:v['query']={'queryState':{role:{'projections':fields} for role,fields in roles.items()}}
    if title:v['visualContainerObjects']=container_title(title)
    if objects:v['objects']=objects
    return {'$schema':VISUAL_SCHEMA,'name':stable_id(name),'position':{'x':x,'y':y,'z':1000,'width':w,'height':h,'tabOrder':1000},'visual':v}

def textbox_json(name,rect,txt,fontsize=22,color='#112744',bold=True):
    d=visual_json(name,rect,'textbox')
    d['visual']['objects']={'general':[{'properties':{'paragraphs':[{'textRuns':[{'value':txt,'textStyle':{'fontFamily':'Segoe UI','fontSize':f'{fontsize}pt','fontWeight':'bold' if bold else 'normal','color':color}}]}]}}]}
    return d

def build_report(report:Path):
    definition=report/'definition'
    t_name='NHS_Primary_Care_Theme.json'
    color_palette=['#168BDF','#11AF9E','#8254D8','#F2AD3A','#F16B7A','#4574C7','#40CBB7']
    theme={'name':t_name,'dataColors':color_palette,'background':'#F4F8FE','foreground':'#152B4E','tableAccent':'#147DDB','good':'#10AA89','neutral':'#EEA42B','bad':'#E7656E',
           'textClasses':{'title':{'fontFace':'Segoe UI Semibold','fontSize':15,'color':'#112744'},'label':{'fontFace':'Segoe UI','fontSize':10,'color':'#53657D'}}}
    dump(report/'StaticResources'/'RegisteredResources'/t_name,theme)
    rv={'visual':'2.7.0','report':'3.2.0','page':'2.1.0'}
    rep={'$schema':REPORT_SCHEMA,
         'themeCollection':{'customTheme':{'name':t_name,'type':'RegisteredResources','reportVersionAtImport':rv}},
         'resourcePackages':[{'name':'RegisteredResources','type':'RegisteredResources','items':[{'name':t_name,'path':t_name,'type':'CustomTheme'}]}],
         'settings':{'defaultFilterActionIsDataFilter':True,'defaultDrillFilterOtherVisuals':True,'useEnhancedTooltips':True}}
    dump(definition/'report.json',rep)
    dump(definition/'version.json',{'$schema':ROOT_SCHEMA+'item/report/definition/versionMetadata/1.0.0/schema.json','version':'2.0.0'})
    order=[]
    for i,p in enumerate(PAGES):
        pid=stable_id('NHS_PHASE8_PAGE_'+str(i))
        order.append(pid)
        base=definition/'pages'/pid
        dump(base/'page.json',{'$schema':PAGE_SCHEMA,'name':pid,'displayName':p['title'],'displayOption':'FitToPage','height':720,'width':1280})
        def add(label,obj):
            file=base/'visuals'/stable_id(f'{pid}_{label}')/'visual.json'
            obj['name']=file.parent.name
            dump(file,obj)
        add('header',textbox_json('hdr',(32,20,620,50),p['title'],21))
        add('subtitle',textbox_json('sub',(32,74,750,28),p['subtitle'],10,'#526680',False))
        add('nav',visual_json('nav',(680,20,570,52),'pageNavigator'))
        add('disclaimer',textbox_json('disclaimer',(32,675,1190,28),'SYNTHETIC DEMONSTRATION  •  NO PATIENT DATA  •  NOT FOR CLINICAL DECISIONS',9,'#657B95',False))
        all_measures={n:t for t,ms in MEASURES.items() for n0,_,_ in ms for n in [n0]}
        for j,m in enumerate(p['cards']):
            t=all_measures[m]
            add(f'card{j}',visual_json(f'card{j}',(32+j*308,127,290,112),'cardVisual',{'Data':[projection(t,m,True)]},m))
        rects=[(32,257,594,191),(652,257,596,191),(32,465,594,191),(652,465,596,191)]
        for j,(title,t,cat,ms,vtype) in enumerate(p['charts']):
            roles={'Category':[projection(t,cat)],'Y':[projection(t,ms,True)]}
            add(f'chart{j}',visual_json(f'chart{j}',rects[j],vtype,roles,title))
        # Compact page-level slicers beneath subtitle / beside page navigator.
        for j,(t,c) in enumerate(p['slicers']):
            add(f'slicer{j}',visual_json(f'slicer{j}',(822+j*210,77,194,44),'slicer',{'Values':[projection(t,c)]}))
    dump(definition/'pages'/'pages.json',{'$schema':ROOT_SCHEMA+'item/report/definition/pagesMetadata/1.0.0/schema.json','pageOrder':order,'activePageName':order[0]})
    dump(report/'definition.pbir',{'$schema':ROOT_SCHEMA+'item/report/definitionProperties/2.0.0/schema.json','version':'4.0','datasetReference':{'byPath':{'path':'../'+BASE+'.SemanticModel'}}})
    return order


def data_type(c):
    if c in INTEGER:return 'int64'
    if c in DECIMAL:return 'double'
    if c in DATE:return 'dateTime'
    if c in BOOLEAN:return 'boolean'
    return 'string'

def build_model(model:Path,snapshots:Path):
    tables=[]
    for name in MARTS:
        cols=COLUMNS[name]
        csv_path=snapshots/(name+'.csv')
        # M strings use doubled quotes, not Windows backslash escaping.
        m_path=str(csv_path.resolve()).replace('"','""')
        m_lines=['let',f'  Source = Csv.Document(File.Contents("{m_path}"), [Delimiter=",", Columns={len(cols)}, Encoding=65001, QuoteStyle=QuoteStyle.Csv]),','  Promoted = Table.PromoteHeaders(Source, [PromoteAllScalars=true]),']
        changes=[]
        for c in cols:
            mtype={'int64':'Int64.Type','double':'type number','dateTime':'type date','boolean':'type logical','string':'type text'}[data_type(c)]
            changes.append('{"'+c+'", '+mtype+'}')
        m_lines.append('  Typed = Table.TransformColumnTypes(Promoted, {'+', '.join(changes)+'}, "en-GB")')
        m_lines+=['in','  Typed']
        table={'name':name,'columns':[{'name':c,'dataType':data_type(c),'sourceColumn':c} for c in cols],
               'partitions':[{'name':name,'mode':'import','source':{'type':'m','expression':m_lines}}]}
        if name in MEASURES:
            table['measures']=[{'name':mn,'expression':exp,'formatString':fmt} for mn,exp,fmt in MEASURES[name]]
        tables.append(table)
    bim={'name':BASE,'compatibilityLevel':1567,'model':{'culture':'en-GB','defaultPowerBIDataSourceVersion':'powerBI_V3','tables':tables}}
    dump(model/'model.bim',bim)
    dump(model/'definition.pbism',{'$schema':ROOT_SCHEMA+'item/semanticModel/definitionProperties/1.0.0/schema.json','version':'1.0','settings':{'qnaEnabled':False}})


def validate_snapshot(table,rows,fields):
    if fields != COLUMNS[table]:
        raise RuntimeError(f'Schema drift: {table}: {fields!r} expected {COLUMNS[table]!r}')
    if not rows:raise RuntimeError(f'Empty reporting mart: {table}')
    if any(len(r)!=len(fields) for r in rows):raise RuntimeError(f'Invalid row width: {table}')
    if any('patient_id' in col.lower() or 'nhs_number' in col.lower() for col in fields):
        raise RuntimeError('Patient identifier detected in reporting mart')


def export_db(root,output,host,port,user,dbname,fixture=False):
    manifest={}
    if fixture:
        # Unit-test fixture only, not installed or used for dashboard metrics.
        for table in MARTS:
            fields=COLUMNS[table]
            row=['1' if c in INTEGER else ('3.5' if c in DECIMAL else ('2026-10-01' if c in DATE else ('false' if c in BOOLEAN else 'example'))) for c in fields]
            rows=[row]
            validate_snapshot(table,rows,fields)
            with (output/(table+'.csv')).open('w',newline='',encoding='utf-8') as f:
                cw=csv.writer(f);cw.writerow(fields);cw.writerows(rows)
            manifest[table]={'rows':1,'columns':fields}
    else:
        import psycopg2
        from psycopg2 import sql
        password=os.environ.get('NHSP8_DB_PASSWORD') or getpass.getpass('Local PostgreSQL password (not saved): ')
        try:
            conn=psycopg2.connect(host=host,port=port,user=user,password=password,dbname=dbname,connect_timeout=15)
        finally:
            password=None
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute('SET TRANSACTION READ ONLY')
                    cur.execute("SELECT to_regclass('nhs_bi_marts.mart_p7_executive') IS NOT NULL")
                    if not cur.fetchone()[0]:raise RuntimeError('Phase 7 reporting marts missing')
                    for table in MARTS:
                        cur.execute('SELECT column_name FROM information_schema.columns WHERE table_schema=%s AND table_name=%s ORDER BY ordinal_position',('nhs_bi_marts',table))
                        fields=[r[0] for r in cur.fetchall()]
                        cur.execute(sql.SQL('SELECT * FROM {}.{}').format(sql.Identifier('nhs_bi_marts'),sql.Identifier(table)))
                        data=cur.fetchall()
                        rows=[[v.isoformat() if isinstance(v,(date,datetime)) else v for v in r] for r in data]
                        validate_snapshot(table,rows,fields)
                        with (output/(table+'.csv')).open('w',newline='',encoding='utf-8') as f:
                            cw=csv.writer(f);cw.writerow(fields);cw.writerows(rows)
                        manifest[table]={'rows':len(rows),'columns':fields}
            conn.rollback()
        finally:conn.close()
        # Minimal aggregate reconciliation - no personal records involved.
        with (output/'mart_p7_executive.csv').open(newline='',encoding='utf-8') as f:
            erows=list(csv.DictReader(f))
        if len(erows)!=1 or int(erows[0]['registered_patients'])!=8000 or int(erows[0]['total_demo_cases'])!=4749:
            raise RuntimeError('Executive mart totals inconsistent with validated Phase 7')
    return manifest


def verify_build(stage:Path,manifest):
    report=stage/(BASE+'.Report')
    model=stage/(BASE+'.SemanticModel')
    pbip=stage/(BASE+'.pbip')
    assert pbip.is_file()
    b=json.loads((model/'model.bim').read_text(encoding='utf-8'))
    assert len(b['model']['tables'])==12
    pages=json.loads((report/'definition/pages/pages.json').read_text(encoding='utf-8'))
    assert len(pages['pageOrder'])==6
    counts=[]
    for pid in pages['pageOrder']:
        root=report/'definition/pages'/pid
        visuals=list((root/'visuals').rglob('visual.json'))
        assert len(visuals)>=11, (pid,len(visuals))
        for p in visuals:
            obj=json.loads(p.read_text(encoding='utf-8'))
            assert 'visualType' in obj['visual']
        counts.append(len(visuals))
    assert len(manifest)==12
    return counts


def run(root:Path,host,port,user,dbname,fixture=False):
    root=root.expanduser().resolve()
    assert root.is_dir(),f'Project folder missing: {root}'
    if not fixture:
        required=[root/'data/processed/phase7_dbt_report.json',root/'docs/PHASE7_DBT.md']
        for file in required:
            if not file.is_file():raise RuntimeError(f'Phase 7 proof missing: {file}')
    destination=root/'powerbi'
    destination.mkdir(exist_ok=True)
    final_names=[BASE+'.Report',BASE+'.SemanticModel',BASE+'.pbip']
    for name in final_names:
        if (destination/name).exists():raise RuntimeError(f'Output already exists. Refusing to overwrite: {destination/name}')
    snapshot_dir=root/'data/processed/powerbi_marts'
    if snapshot_dir.exists():raise RuntimeError('Existing reporting snapshots found; refuse to overwrite: '+str(snapshot_dir))
    # Stage wholly within the same drive for atomic moves and cleanup on failure.
    with TemporaryDirectory(prefix='nhs_phase8_',dir=str(destination)) as tmp:
        stage=Path(tmp)
        snapshots=stage/'snapshots';snapshots.mkdir()
        manifest=export_db(root,snapshots,host,port,user,dbname,fixture)
        # Model reads future snapshot paths: set up final snapshots in a temp layout using an override.
        # Stable absolute paths are required by File.Contents on the Windows workstation.
        report=stage/(BASE+'.Report');model=stage/(BASE+'.SemanticModel')
        pages=build_report(report)
        model.mkdir()
        build_model(model,snapshot_dir)
        dump(stage/(BASE+'.pbip'),{'$schema':ROOT_SCHEMA+'pbip/pbipProperties/1.0.0/schema.json','version':'1.0','artifacts':[{'report':{'path':BASE+'.Report'}}],'settings':{'enableAutoRecovery':True}})
        counts=verify_build(stage,manifest)
        if fixture:
            return {'result':'fixture_pass','pages':len(pages),'visuals_per_page':counts,'mart_tables':12}
        snapshot_dir.mkdir(parents=True,exist_ok=False)
        try:
            for file in snapshots.glob('*.csv'):shutil.copy2(file,snapshot_dir/file.name)
            for name in final_names:shutil.move(str(stage/name),str(destination/name))
        except Exception:
            # Preserve anything created for debugging rather than auto-deleting user files.
            raise
    doc={'source':'nhs_bi_marts','classification':'SIMULATION_ONLY_AGGREGATE','snapshot_directory':str(snapshot_dir),'marts':manifest,
         'pages':[p['title'] for p in PAGES],'visuals_per_page':counts,'desktop_render_verified':False,
         'notes':'Visual definitions and semantic model are structurally checked; Power BI Desktop rendering is not verified by this installer.'}
    dump(root/'data/processed/phase8_powerbi_manifest.json',doc)
    print('PHASE 8 SOURCE BUILD PASS - 12 aggregate marts, 6 pages, ',sum(counts),'PBIR visual definitions')
    print('PROJECT:',destination/(BASE+'.pbip'))
    print('NEXT: Open this .pbip in Power BI Desktop to validate rendering and refresh. Do not claim visual verification yet.')
    return doc


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--project-root',required=True)
    p.add_argument('--host',default='localhost');p.add_argument('--port',type=int,default=5432)
    p.add_argument('--user',default='postgres');p.add_argument('--dbname',default='nhs_primarycare_demo')
    p.add_argument('--fixture-test',action='store_true',help=argparse.SUPPRESS)
    a=p.parse_args()
    result=run(Path(a.project_root),a.host,a.port,a.user,a.dbname,a.fixture_test)
    if a.fixture_test:print('PHASE 8 FIXTURE PASS',result)

if __name__=='__main__':main()
