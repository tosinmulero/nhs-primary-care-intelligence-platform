"""Non-destructive Phase 8 PBIR visual polish for NHS synthetic portfolio.

Uses VS Code source edits only; leaves PostgreSQL, CSV snapshots, DAX,
semantic-model tables and visual query bindings unchanged.
"""
from __future__ import annotations
import argparse
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sys

BASE='NHS_Primary_Care_Analytics'
PALETTE=['#168BDF','#11AF9E','#8254D8','#F2AD3A','#F16B7A','#4574C7']
CARD_TINT=['#ECF5FF','#E9FBF7','#F3EEFF','#FFF5E4']
PAGE_NAMES=['01 Executive','02 Demographics','03 Coding Quality','04 Pathology & Inbox','05 Clinical & QOF','06 Governance']


def literal(s):
    return {'expr':{'Literal':{'Value':s}}}


def color(c):
    return {'solid':{'color':literal("'"+c+"'")}}


def style(props):
    return [{'properties':props}]


def encoded(data):
    return (json.dumps(data,ensure_ascii=False,indent=2)+'\n').encode('utf-8')


def validate_model(root:Path):
    model_path=root/'powerbi'/f'{BASE}.SemanticModel'/'model.bim'
    if not model_path.is_file(): raise FileNotFoundError(f'Semantic model not found: {model_path}')
    model=json.loads(model_path.read_text(encoding='utf-8-sig'))
    tables=model['model']['tables']
    if len(tables)!=12:raise ValueError(f'Expected 12 aggregate marts, got {len(tables)}')
    m={t['name']:{z['name'] for z in t.get('measures',[])} for t in tables}
    cols={t['name']:{z['name'] for z in t['columns']} for t in tables}
    for table in m:
        if {x.casefold() for x in m[table]} & {x.casefold() for x in cols[table]}:
            raise ValueError(f'DAX measure/column collision in {table}')
    return model,m,cols


def projection_check(vis,measures,columns):
    def scan(node):
        if isinstance(node,dict):
            fld=node.get('field')
            if isinstance(fld,dict):
                for kind,names in [('Measure',measures),('Column',columns)]:
                    if kind in fld:
                        target=fld[kind]
                        table=target['Expression']['SourceRef']['Entity']
                        name=target['Property']
                        if name not in names.get(table,set()):
                            raise ValueError(f'Bad visual binding: {table}[{name}] ({kind})')
            for val in node.values():scan(val)
        elif isinstance(node,list):
            for val in node:scan(val)
    scan(vis)


def mutate(root:Path):
    model,measures,columns=validate_model(root)
    report=root/'powerbi'/f'{BASE}.Report'
    page_root=report/'definition'/'pages'
    pages_file=page_root/'pages.json'
    theme_file=report/'StaticResources'/'RegisteredResources'/'NHS_Primary_Care_Theme.json'
    if not pages_file.is_file() or not theme_file.is_file():
        raise FileNotFoundError('Phase 8 report pages or registered theme missing')
    meta=json.loads(pages_file.read_text(encoding='utf-8-sig'))
    ids=meta['pageOrder']
    if len(ids)!=6 or len(set(ids))!=6:
        raise ValueError('Expected six unique Phase 8 pages')
    theme=json.loads(theme_file.read_text(encoding='utf-8-sig'))
    desired={}
    counts={'cards':0,'charts':0,'slicers':0,'navigation':0,'total':0}
    for i,pid in enumerate(ids):
        page_file=page_root/pid/'page.json'
        page=json.loads(page_file.read_text(encoding='utf-8-sig'))
        if 'Executive' not in page.get('displayName','') and i==0:
            if page.get('displayName') != PAGE_NAMES[0]:
                raise ValueError('Unexpected first page; refusing layout changes')
        page['displayName']=PAGE_NAMES[i]
        # Native PBIR page background; do not change data or visuals.
        objects=page.setdefault('objects',{})
        objects['background']=style({'color':color('#F3F7FD'),'transparency':literal('0D')})
        desired[page_file]=encoded(page)

        paths=sorted((page_root/pid/'visuals').rglob('visual.json'))
        if not (12<=len(paths)<=16):raise ValueError(f'Unexpected visual count in page {pid}: {len(paths)}')
        cards=[];charts=[];slicers=[];nav=[]
        visuals={}
        for path in paths:
            obj=json.loads(path.read_text(encoding='utf-8-sig'))
            visual=obj.get('visual',{})
            vtype=visual.get('visualType')
            if vtype=='cardVisual':cards.append(path)
            elif vtype in {'lineChart','barChart','columnChart','donutChart'}:charts.append(path)
            elif vtype=='slicer':slicers.append(path)
            elif vtype=='pageNavigator':nav.append(path)
            projection_check(obj,measures,columns)
            visuals[path]=obj
        if len(cards)!=4 or len(charts)!=4 or len(nav)!=1 or len(slicers) not in (1,2):
            raise ValueError(f'Unexpected page components: page={i} cards={len(cards)} charts={len(charts)} slicers={len(slicers)} nav={len(nav)}')
        # Sort layout elements by original position to preserve intended order.
        cards.sort(key=lambda f:visuals[f]['position']['x'])
        charts.sort(key=lambda f:(visuals[f]['position']['y'],visuals[f]['position']['x']))
        slicers.sort(key=lambda f:visuals[f]['position']['x'])
        for j,path in enumerate(cards):
            item=visuals[path]
            item['position'].update({'x':32+j*308,'y':172,'width':290,'height':103})
            v=item['visual']; co=v.setdefault('visualContainerObjects',{})
            # Card already renders the measure label inside; hide redundant outer title.
            co['title']=style({'show':literal('false')})
            co['background']=style({'show':literal('true'),'color':color(CARD_TINT[j]),'transparency':literal('0D')})
            co['border']=style({'show':literal('true'),'color':color(PALETTE[j]),'radius':literal('12D'),'width':literal('1D')})
            desired[path]=encoded(item)
            counts['cards']+=1
        rects=[(32,290,594,175),(652,290,596,175),(32,480,594,175),(652,480,596,175)]
        for j,path in enumerate(charts):
            item=visuals[path]
            x,y,w,h=rects[j]; item['position'].update({'x':x,'y':y,'width':w,'height':h})
            v=item['visual']; co=v.setdefault('visualContainerObjects',{})
            # Preserve the title text; standardise title emphasis and card accents.
            for t in co.get('title',[]):
                if 'properties' in t:
                    props=t['properties'];props['fontSize']=literal('13D');props['fontColor']=color('#142E53');props['bold']=literal('true')
            co['background']=style({'show':literal('true'),'color':color('#FFFFFF'),'transparency':literal('0D')})
            co['border']=style({'show':literal('true'),'color':color('#DDE6F3'),'radius':literal('12D'),'width':literal('1D')})
            # A per-chart default data colour means four independent plot regions
            # use distinguishable colours, while donut keeps the full series palette.
            if v['visualType'] in ('barChart','columnChart','lineChart'):
                v.setdefault('objects',{}).setdefault('dataPoint',[])
                v['objects']['dataPoint']=style({'defaultColor':color(PALETTE[(i+j)%len(PALETTE)])})
            desired[path]=encoded(item)
            counts['charts']+=1
        for j,path in enumerate(slicers):
            item=visuals[path]
            item['position'].update({'x':(1020 if len(slicers)==1 else 855+j*197),'y':91,'width':190,'height':73})
            v=item['visual'];v.setdefault('objects',{})
            # PBIR slicer Dropdown setting: collapses unwieldy checkboxes and scrollbars.
            v['objects']['data']=style({'mode':literal("'Dropdown'")})
            v.setdefault('visualContainerObjects',{})['background']=style({'show':literal('true'),'color':color('#FFFFFF'),'transparency':literal('0D')})
            desired[path]=encoded(item)
            counts['slicers']+=1
        nav_item=visuals[nav[0]]
        nav_item['position'].update({'x':680,'y':20,'width':570,'height':61})
        desired[nav[0]]=encoded(nav_item)
        counts['navigation']+=1
        counts['total']+=len(paths)

    if counts['total']<80:raise ValueError(f'Unexpected total visual count {counts["total"]}')
    theme['name']='NHS_Primary_Care_Theme.json'
    theme['dataColors']=PALETTE+['#31C6B9','#657FA1']
    theme.update({'background':'#F3F7FD','foreground':'#142E53','tableAccent':'#168BDF','good':'#11AF9E','neutral':'#F2AD3A','bad':'#F16B7A'})
    desired[theme_file]=encoded(theme)
    # Re-read transformed files from memory and check binding/model integrity.
    for path,blob in desired.items():
        json.loads(blob)
        if path.name=='visual.json':projection_check(json.loads(blob),measures,columns)
    return desired,counts


def main():
    parser=argparse.ArgumentParser(description='Safely polish six NHS Power BI report pages')
    parser.add_argument('--project-root',required=True)
    parser.add_argument('--dry-run',action='store_true')
    args=parser.parse_args()
    root=Path(args.project_root).expanduser().resolve()
    if not root.is_dir():raise FileNotFoundError(f'Project root missing: {root}')
    desired,counts=mutate(root)
    actual={p:b for p,b in desired.items() if p.read_bytes()!=b}
    print('VALIDATED:',json.dumps(counts,sort_keys=True))
    print(f'FILES TO UPDATE: {len(actual)}')
    if args.dry_run:
        print('DRY RUN PASS - no files changed')
        return
    if not actual:
        print('PHASE 8 POLISH ALREADY APPLIED - no changes needed')
        return
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup=root/'data'/'processed'/('phase8_visual_polish_backup_'+stamp)
    if backup.exists():raise FileExistsError(f'Refusing to reuse backup folder: {backup}')
    # A complete pre-edit backup, preserving relative paths.
    for path in actual:
        saved=backup/path.relative_to(root)
        saved.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(path,saved)
    print('BACKUP:',backup)
    try:
        for path,blob in actual.items():
            temp=path.with_suffix(path.suffix+'.tmp-p8')
            temp.write_bytes(blob)
            temp.replace(path)
        new_desired, new_counts=mutate(root)
        if any(path.read_bytes()!=blob for path,blob in new_desired.items()):
            raise AssertionError('Post-write verification mismatch')
    except BaseException:
        for path in actual:
            saved=backup/path.relative_to(root)
            if saved.exists():shutil.copy2(saved,path)
        raise
    print(f'PHASE 8 VISUAL POLISH PASS - {len(actual)} report-source files updated')
    print('No patient data, CSV snapshots, semantic model tables, measures or SQL database modified.')
    print('Close Power BI Desktop and reopen the .pbip to visually validate all six pages.')


if __name__=='__main__':
    try:main()
    except Exception as exc:
        print('PHASE 8 VISUAL POLISH FAILED:',exc,file=sys.stderr)
        sys.exit(1)
