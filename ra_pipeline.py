#!/usr/bin/env python3
"""Read-only House Republic Acts extraction and exact (Congress, HB) master linkage.

No title matching, no inferred enactment dates, no edits to the House master.
Supports official House HTML saved by the optional polite fetcher or browser Save As.
"""
from __future__ import annotations
import argparse
import csv
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import time
from urllib.parse import urljoin, urlsplit, parse_qs

from bs4 import BeautifulSoup, NavigableString

BASE = 'https://www.congress.gov.ph/legislative-documents/republic-acts'
RA_HEAD = re.compile(r'^\s*(?:RA\s*0*(\d{3,5})|Republic\s+Act\s+(?:No\.?\s*)?0*(\d{3,5}))\s*\.?\s*$', re.I)
BILL_RE = re.compile(r'\b(?:H\s*\.?\s*B\s*\.?\s*(?:N(?:o|um(?:ber)?)\s*\.?\s*)?|H\s*\.?\s*(?:No|N)\s*\.?\s*|HBN\s*|HOUSE\s+BILL\s*(?:NO\.?)?\s*)(\d{1,6})\b', re.I)
SEN_RE = re.compile(r'\b(?:S\s*\.?\s*B\s*\.?\s*(?:N(?:o|um(?:ber)?)\s*\.?\s*)?|S\s*\.?\s*(?:No|N)\s*\.?\s*|SBN\s*|SENATE\s+BILL\s*(?:NO\.?)?\s*)(\d{1,6})\b', re.I)
CONGRESS_RE = re.compile(r'\b(\d{1,2})\s*(?:st|nd|rd|th)?\s*Congress\b', re.I)
LABEL_RE = re.compile(r'^(Title|House\s+Bill\s+No\.?|Senate\s+Bill\s+No\.?|Congress|Origin)\s*:\s*(.*)$', re.I | re.S)
LABELS = {'title':'title','house bill no':'hb','senate bill no':'sb','congress':'congress','origin':'origin'}
RAW_COLS = ['ra_number','ra_title','house_bill_numbers','senate_bill_numbers','congress','origin','history_urls','attachment_urls','source_page_url','source_file','source_sha256','source_record_ordinal','record_sha256','parse_flags']
LAW_COLS = ['ra_number','ra_title','congress','origin','n_listing_records','house_bill_numbers','senate_bill_numbers','history_urls','attachment_urls','source_page_urls','source_sha256s','metadata_conflict']
EDGE_COLS = ['ra_number','congress','house_bill_no','measure_id','match_status','evidence_status','source_page_urls','source_sha256s','n_archive_mentions','ra_title','ra_origin','linked_bill_title','bill_cap_major','bill_cap_subtopic','bill_cap_pair','bill_cap_assignment_status','bill_cap_validation_status','linked_bill_gender_3cat','bill_gender_production_eligible','bill_primary_committee','bill_primary_committee_gender_3cat','law_domain_status']
AUDIT_COLS = ['audit_type','ra_number','congress','house_bill_no','detail','source_page_urls']
LINK_STATUS = 'OFFICIAL_HOUSE_ARCHIVE_LISTING'


def sha(b): return hashlib.sha256(b).hexdigest()
def uniq(seq): return list(dict.fromkeys(x for x in seq if x))
def join(seq): return ' | '.join(uniq(seq))
def norm_ra(s):
    m=RA_HEAD.fullmatch(str(s or '').strip())
    return f'RA{int(m.group(1) or m.group(2)):05d}' if m else ''
def norm_bill(s):
    s=str(s or '').strip()
    if re.fullmatch(r'\d{1,6}',s): return f'HB{int(s):05d}'
    m=BILL_RE.search(s)
    return f'HB{int(m.group(1)):05d}' if m else ''
def extract_numbers(s, pattern, prefix):
    return uniq(f'{prefix}{int(x):05d}' for x in pattern.findall(s or ''))
def congress(s):
    s=str(s or '').strip()
    if re.fullmatch(r'\d{1,2}(?:\.0)?',s): return str(int(float(s)))
    m=CONGRESS_RE.search(s)
    return m.group(1) if m else ''
def read_csv(p):
    with Path(p).open('r',encoding='utf-8-sig',newline='') as f:
        r=csv.DictReader(f)
        if not r.fieldnames: raise ValueError(f'Empty CSV: {p}')
        return list(r), r.fieldnames
def write_csv(p, columns, rows):
    with Path(p).open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=columns,extrasaction='ignore'); w.writeheader(); w.writerows(rows)
def record_digest(r):
    return sha(json.dumps({k:v for k,v in r.items() if k!='record_sha256'},ensure_ascii=False,sort_keys=True).encode('utf-8'))


def parse_archive_page(html: bytes, url: str, file_name: str):
    """Extract RA blocks in DOM text order. No site-specific class names required.
    Every RA header must be followed by archive field labels to be accepted.
    """
    soup=BeautifulSoup(html,'html.parser')
    for tag in soup(['script','style','noscript','template']): tag.decompose()
    root=soup.find('main') or soup.find('body') or soup
    tokens=[]
    for node in root.descendants:
        if not isinstance(node,NavigableString): continue
        s=' '.join(str(node).split())
        if not s: continue
        a=node.parent.find_parent('a') if node.parent.name!='a' else node.parent
        href=urljoin(url,a.get('href','')) if a and a.get('href') else ''
        tokens.append((s,href))
    records=[]; cur=None; field=None
    def finish():
        nonlocal cur
        if cur is None: return
        if not cur['parts']['title'] and not (cur['parts']['hb'] or cur['parts']['sb']): return
        p=cur['parts']; flags=[]
        hb=extract_numbers(' '.join(p['hb']),BILL_RE,'HB')
        sb=extract_numbers(' '.join(p['sb']),SEN_RE,'SB')
        # A source may print a bare numeric bill number directly underneath the heading.
        if not hb:
            hb=uniq([norm_bill(' '.join(p['hb']))])
        con=uniq([congress(' '.join(p['congress']))])
        if len(con)>1: flags.append('CONFLICTING_CONGRESS_WITHIN_CARD')
        title=' '.join(p['title']).strip()
        origin=' '.join(p['origin']).strip()
        if not title: flags.append('MISSING_TITLE')
        if not hb: flags.append('MISSING_HOUSE_BILL_NUMBER')
        if not con: flags.append('MISSING_CONGRESS')
        if origin and 'Senate' not in origin and 'House' not in origin: flags.append('UNKNOWN_ORIGIN_TEXT')
        r=dict(ra_number=cur['ra'],ra_title=title,house_bill_numbers=join(hb),senate_bill_numbers=join(sb),
               congress=con[0] if len(con)==1 else '',origin=origin,history_urls=join(cur['history']),
               attachment_urls=join(cur['attachment']),source_page_url=url,source_file=file_name,
               source_sha256=sha(html),source_record_ordinal=str(len(records)+1),parse_flags=join(flags))
        r['record_sha256']=record_digest(r); records.append(r)
    for s,href in tokens:
        m=RA_HEAD.fullmatch(s)
        if m:
            finish(); cur={'ra':f'RA{int(m.group(1) or m.group(2)):05d}',
                          'parts':{k:[] for k in LABELS.values()},'history':[],'attachment':[]}
            field=None; continue
        if cur is None: continue
        m=LABEL_RE.match(s)
        if m:
            label=re.sub(r'\s+',' ',m.group(1).strip().lower().rstrip('.'))
            field=LABELS.get(label)
            if field and m.group(2): cur['parts'][field].append(m.group(2).strip())
            continue
        low=s.lower()
        if href and ('history' in low or 'legislative history' in low):cur['history'].append(href)
        if href and ('attachment' in low or 'full text' in low or 'download' in low):cur['attachment'].append(href)
        if low in ('history','attachment','history attachment','view history','view attachment'):
            field=None; continue
        if field and not href: cur['parts'][field].append(s)
        elif field and href and field in ('hb','sb'):
            cur['parts'][field].append(s) # bill number may itself be linked
    finish()
    if not records: raise ValueError(f'No parseable Republic Act cards in {file_name}: STOP and inspect HTML/site markup')
    return records


def fetch(args):
    import requests
    dest=Path(args.html_dir); dest.mkdir(parents=True,exist_ok=True)
    sess=requests.Session();sess.headers['User-Agent']=args.user_agent
    inventory=[]; seen=set()
    for page in range(args.start_page,args.start_page+args.max_pages):
        url=BASE if page==0 else BASE+'?page='+str(page)
        response=sess.get(url,timeout=45)
        if response.status_code==403:
            raise RuntimeError('STOP: House site returned HTTP 403. No pages saved. Do not repeat automated requests; if the site is accessible in your ordinary browser, use capture-browser to import a permitted browser snapshot. If blocked in the browser too, request official archive access.')
        if response.status_code!=200 or '<html' not in response.text[:1500].lower():
            raise RuntimeError(f'STOP: response status={response.status_code} page={page}; no claim of complete archive. URL={url}')
        data=response.content
        parsed=parse_archive_page(data,response.url,f'page_{page:04d}.html')
        sig=tuple(x['ra_number'] for x in parsed)
        if sig in seen: # repeated terminal pagination response, not another unique page
            inventory.append({'page':page,'url':response.url,'status':'REPEATED_PAGE_STOP','cards':len(parsed)})
            break
        seen.add(sig)
        path=dest/f'page_{page:04d}.html'
        if path.exists() and sha(path.read_bytes())!=sha(data) and not args.allow_snapshot_replacement:
            raise RuntimeError(f'STOP: snapshot already exists and content changed: {path}. Use a new HTML folder.')
        path.write_bytes(data)
        inventory.append({'page':page,'url':response.url,'status':'OK','cards':len(parsed),'sha256':sha(data)})
        time.sleep(args.delay)
    else:
        inventory.append({'status':'MAX_PAGES_REACHED_NOT_PROOF_OF_COMPLETENESS','page':args.start_page+args.max_pages-1})
    (dest/'fetch_inventory.json').write_text(json.dumps(inventory,indent=2),encoding='utf8')
    print('Fetched pages:',sum(x.get('status')=='OK' for x in inventory),'; STOP reason:',inventory[-1]['status'])


def capture_browser(args):
    """Accept visible DOM explicitly copied by a user from an authorized browser.

    On macOS, browser DevTools Console: copy(JSON.stringify({url:location.href,html:document.documentElement.outerHTML})).
    This is NOT a programmatic HTTP fetch, and fails if no archive cards are present.
    """
    import subprocess
    dest=Path(args.html_dir)
    if args.page<0: raise ValueError('Page number must be >= 0')
    url=BASE if args.page==0 else BASE+'?page='+str(args.page)
    if args.from_file:
        data=Path(args.from_file).read_bytes()
    else:
        if sys.platform!='darwin': raise RuntimeError('Clipboard capture uses macOS pbpaste. On other platforms supply --from-file.')
        data=subprocess.run(['pbpaste'],capture_output=True,check=True).stdout
    actual_url=url
    if data.lstrip().startswith(b'{'):
        try:
            bundle=json.loads(data.decode('utf-8'))
            actual_url=bundle['url']; data=bundle['html'].encode('utf-8')
            u=urlsplit(actual_url)
            if (u.scheme!='https' or u.hostname not in ('www.congress.gov.ph','congress.gov.ph')
                    or u.path.rstrip('/') not in ('/legislative-documents/republic-acts','/index.php/legislative-documents/republic-acts')):
                raise ValueError(f'Clipboard URL is not the official House RA archive: {actual_url}')
            actual_page=int(parse_qs(u.query).get('page',['0'])[0])
            if actual_page!=args.page:
                raise ValueError(f'Wrong browser page! Clipboard has page={actual_page}; --page={args.page}. Open the correct URL and recopy.')
        except (KeyError, json.JSONDecodeError, UnicodeError) as exc:
            raise ValueError('Clipboard is not valid snapshot JSON with url and html keys') from exc
    if b'<html' not in data[:2000].lower():
        raise ValueError('Clipboard/file is not HTML. In browser DevTools Console run: copy(JSON.stringify({url:location.href,html:document.documentElement.outerHTML}))')
    filename=f'page_{args.page:04d}.html'
    parsed=parse_archive_page(data,actual_url,filename)
    dest.mkdir(parents=True,exist_ok=True)
    target=dest/filename
    if target.exists():
        if sha(target.read_bytes())!=sha(data):
            raise RuntimeError(f'STOP: existing {target} has different content. Do not overwrite a prior snapshot; use a new folder.')
        print(f'Already captured same snapshot: {target} ({len(parsed)} records)')
        return
    target.write_bytes(data)
    (dest/f'page_{args.page:04d}.provenance.json').write_text(json.dumps({'url':actual_url,'sha256':sha(data),'source':'USER_BROWSER_CAPTURE' if not args.from_file else 'USER_SUPPLIED_HTML_FILE'},indent=2),encoding='utf-8')
    print(json.dumps({'file':str(target),'page':args.page,'records':len(parsed),'ra_first':parsed[0]['ra_number'],
                      'ra_last':parsed[-1]['ra_number'],'congress_detected':sorted(uniq(x['congress'] for x in parsed)),
                      'sha256':sha(data)},indent=2))


def inspect_html_dir(html_dir, strict=True):
    folder=Path(html_dir)
    files=sorted(folder.glob('page_*.html'))
    if not files:raise ValueError(f'No page_XXXX.html source pages in {folder}. The HTTP 403 prevented fetch from saving any. Capture one accessible browser page first.')
    indices=[]; sigs=set(); report=[]
    for file in files:
        m=re.fullmatch(r'page_(\d{4})\.html',file.name)
        if not m:raise ValueError(f'Unexpected page file: {file.name}')
        idx=int(m.group(1)); indices.append(idx)
        url=BASE if idx==0 else BASE+'?page='+str(idx)
        receipt=file.with_suffix('.provenance.json')
        if receipt.exists():
            proof=json.loads(receipt.read_text(encoding='utf-8'))
            if proof['sha256']!=sha(file.read_bytes()):raise ValueError(f'STOP: provenance SHA-256 mismatch for {file.name}')
            url=proof['url']
        rows=parse_archive_page(file.read_bytes(),url,file.name)
        sig=tuple((r['ra_number'],r['house_bill_numbers']) for r in rows)
        if sig in sigs:raise ValueError(f'Identical parsed archive page at {file.name}; check pagination and snapshots')
        sigs.add(sig)
        report.append({'page':idx,'file':file.name,'records':len(rows),'first_ra':rows[0]['ra_number'],
                       'last_ra':rows[-1]['ra_number'],'congresses':sorted(uniq(r['congress'] for r in rows)),
                       'missing_congress':sum(not r['congress'] for r in rows),
                       'missing_hb':sum(not r['house_bill_numbers'] for r in rows),
                       'sha256':sha(file.read_bytes())})
    missing=sorted(set(range(min(indices),max(indices)+1))-set(indices))
    if strict and (indices[0]!=0 or missing):
        raise ValueError(f'STOP: incomplete page sequence. Starts at {indices[0]}; missing={missing}. Use --allow-partial ONLY for inspection, not completeness claims.')
    return {'observed_pages':len(files),'page_numbers':indices,'missing_pages':missing,
            'snapshot_scope':'SAVED_PAGES_ONLY_NOT_ARCHIVE_COMPLETENESS', 'pages':report}


def check(args):
    print(json.dumps(inspect_html_dir(args.html_dir,strict=not args.allow_partial),indent=2))


def build(args):
    html_dir=Path(args.html_dir); out=Path(args.output_dir)
    if out.resolve()==html_dir.resolve(): raise ValueError('Output directory must differ from input HTML directory')
    if out.exists() and any(out.iterdir()): raise ValueError('STOP: output directory must be new/empty; do not overwrite existing artifacts')
    inventory=inspect_html_dir(html_dir, strict=True)
    files=sorted(html_dir.glob('page_*.html'))
    master_path=Path(args.master)
    if not master_path.is_file():
        raise FileNotFoundError(f'House master does not exist at: {master_path}. Run: find .. -type f -name \"{master_path.name}\" -print')
    master,cols=read_csv(master_path)
    required=[args.id_col,args.congress_col,args.bill_col]
    absent=[x for x in required if x not in cols]
    if absent: raise ValueError(f'Master missing required columns: {absent}. Available: {cols}')
    bykey={}; seen_ids=set(); master_keys=set()
    for rowno,r in enumerate(master,start=2):
        mid=r[args.id_col].strip(); co=congress(r[args.congress_col]); bn=norm_bill(r[args.bill_col])
        if not mid or mid in seen_ids: raise ValueError(f'STOP: duplicate/blank measure_id at CSV line {rowno}: {mid}')
        seen_ids.add(mid)
        if co and bn:
            key=(co,bn)
            if key in master_keys:raise ValueError(f'STOP: duplicate (congress, HB) key in master at line {rowno}: {key}')
            master_keys.add(key); bykey[key]=r
    raw=[]; sources=[]
    for f in files:
        data=f.read_bytes(); url=BASE
        m=re.search(r'(\d+)',f.stem)
        if m and int(m.group(1)):url+='?page='+str(int(m.group(1)))
        receipt=f.with_suffix('.provenance.json')
        if receipt.exists():url=json.loads(receipt.read_text(encoding='utf-8'))['url']
        page_rows=parse_archive_page(data,url,f.name)
        raw.extend(page_rows)
        sources.append(dict(file=f.name,url=url,sha256=sha(data),cards=len(page_rows)))
    if not raw:raise ValueError('No extracted RA rows')
    groups=defaultdict(list)
    for r in raw: groups[r['ra_number']].append(r)
    laws=[]; audits=[]; edge_groups=defaultdict(list)
    for ra,rows in sorted(groups.items()):
        titles=uniq(x['ra_title'] for x in rows if x['ra_title'])
        cs=uniq(x['congress'] for x in rows if x['congress'])
        origins=uniq(x['origin'] for x in rows if x['origin'])
        conflict=[]
        if len(titles)>1: conflict.append('TITLE_CONFLICT')
        if len(cs)>1: conflict.append('CONGRESS_CONFLICT')
        if len(origins)>1: conflict.append('ORIGIN_CONFLICT')
        hbs=uniq(x for r in rows for x in r['house_bill_numbers'].split(' | ') if x)
        sbs=uniq(x for r in rows for x in r['senate_bill_numbers'].split(' | ') if x)
        law=dict(ra_number=ra,ra_title=titles[0] if titles else '',congress=cs[0] if len(cs)==1 else '',
                 origin=origins[0] if len(origins)==1 else '',n_listing_records=len(rows),
                 house_bill_numbers=join(hbs),senate_bill_numbers=join(sbs),
                 history_urls=join(x for r in rows for x in r['history_urls'].split(' | ')),
                 attachment_urls=join(x for r in rows for x in r['attachment_urls'].split(' | ')),
                 source_page_urls=join(r['source_page_url'] for r in rows),source_sha256s=join(r['source_sha256'] for r in rows),
                 metadata_conflict=join(conflict))
        laws.append(law)
        if conflict:
            audits.append(dict(audit_type='RA_METADATA_CONFLICT',ra_number=ra,congress=join(cs),house_bill_no=join(hbs),
                               detail=join(conflict)+'; titles='+repr(titles)+'; origins='+repr(origins),source_page_urls=law['source_page_urls']))
        if not hbs:
            audits.append(dict(audit_type='NO_HOUSE_BILL_IN_LISTING',ra_number=ra,congress=law['congress'],house_bill_no='',
                               detail='Do not infer missing HB from title',source_page_urls=law['source_page_urls']))
        if 'CONGRESS_CONFLICT' in conflict:continue
        for hb in hbs:
            relevant=[r for r in rows if hb in r['house_bill_numbers'].split(' | ')]
            edge_groups[(ra,law['congress'],hb)].extend(relevant)
    edges=[]
    for (ra,co,hb),rlist in sorted(edge_groups.items()):
        law=next(x for x in laws if x['ra_number']==ra)
        match=bykey.get((co,hb)) if co else None
        status='MATCHED_UNIQUE' if match else ('MISSING_CONGRESS' if not co else 'NOT_IN_HOUSE_MASTER')
        if law['metadata_conflict']: status='RA_METADATA_CONFLICT_REVIEW' if status=='MATCHED_UNIQUE' else status
        def m(c):return match.get(c,'') if match else ''
        e=dict(ra_number=ra,congress=co,house_bill_no=hb,measure_id=m(args.id_col),match_status=status,
               evidence_status=LINK_STATUS,source_page_urls=join(x['source_page_url'] for x in rlist),
               source_sha256s=join(x['source_sha256'] for x in rlist),n_archive_mentions=len(rlist),
               ra_title=law['ra_title'],ra_origin=law['origin'],linked_bill_title=m('full_title'),
               bill_cap_major=m('cap_major'),bill_cap_subtopic=m('cap_subtopic'),bill_cap_pair=m('cap_pair'),
               bill_cap_assignment_status=m('cap_assignment_status'),bill_cap_validation_status=m('cap_validation_status'),
               linked_bill_gender_3cat=m('gender_assignment_3cat'),bill_gender_production_eligible=m('gender_production_eligible'),
               bill_primary_committee=m('primary_committee_canonical'),
               bill_primary_committee_gender_3cat=m('primary_committee_gender_3cat'),
               law_domain_status='NOT_ADJUDICATED_FROM_ENACTED_TEXT')
        edges.append(e)
        if status!='MATCHED_UNIQUE':
            audits.append(dict(audit_type='LINK_REVIEW',ra_number=ra,congress=co,house_bill_no=hb,
                               detail=status,source_page_urls=e['source_page_urls']))
    # A single law may have multiple House bills with contradictory preliminary classifications.
    for ra,associated in sorted(defaultdict_list(edges,'ra_number').items()):
        matched=[x for x in associated if x['match_status']=='MATCHED_UNIQUE']
        pairs=uniq(x['bill_cap_pair'] for x in matched if x['bill_cap_pair'])
        gen=uniq(x['linked_bill_gender_3cat'] for x in matched if x['linked_bill_gender_3cat'])
        if len(pairs)>1 or len(gen)>1:
            audits.append(dict(audit_type='MULTIPLE_LINKED_BILL_CLASSIFICATIONS',ra_number=ra,
                               congress=join(x['congress'] for x in matched),
                               house_bill_no=join(x['house_bill_no'] for x in matched),
                               detail='Bill CAP pairs: '+repr(pairs)+'; bill gender: '+repr(gen)+'; no automatic RA coding',
                               source_page_urls=join(x['source_page_urls'] for x in matched)))
    by_mid=defaultdict(list)
    for x in edges:
        if x['match_status']=='MATCHED_UNIQUE':by_mid[x['measure_id']].append(x['ra_number'])
    enriched=[]
    for r in master:
        row=dict(r); linked=uniq(by_mid.get(r[args.id_col],[]))
        row['linked_ra_numbers']=join(linked)
        row['n_linked_ra']=len(linked)
        row['ra_linkage_status']='ARCHIVE_LINK_OBSERVED' if linked else 'NOT_OBSERVED_IN_SNAPSHOT_NOT_PROOF_OF_FAILURE'
        enriched.append(row)
    out.mkdir(parents=True,exist_ok=True)
    write_csv(out/'01_RA_ARCHIVE_RECORDS.csv',RAW_COLS,raw)
    write_csv(out/'02_REPUBLIC_ACTS.csv',LAW_COLS,laws)
    write_csv(out/'03_RA_HOUSE_BILL_LINKS.csv',EDGE_COLS,edges)
    write_csv(out/'04_HOUSE_BILL_MASTER_RA_ENRICHED.csv',cols+['linked_ra_numbers','n_linked_ra','ra_linkage_status'],enriched)
    write_csv(out/'05_LINKAGE_AND_METADATA_AUDIT.csv',AUDIT_COLS,audits)
    stat=dict(raw_archive_cards=len(raw),distinct_ra=len(laws),raw_house_bill_links=len(edges),matched_unique=sum(x['match_status']=='MATCHED_UNIQUE' for x in edges),
              unmatched_or_held=sum(x['match_status']!='MATCHED_UNIQUE' for x in edges),master_rows=len(master),enriched_rows=len(enriched),
              linked_distinct_master_bills=len(by_mid),metadata_conflict_laws=sum(bool(x['metadata_conflict']) for x in laws),
              coverage_statement='Observed source HTML only. Unmatched bill != not enacted. No approval dates inferred.',
              master_sha256=sha(master_path.read_bytes()),source_snapshots=sources,snapshot_check=inventory,run_utc=datetime.now(timezone.utc).isoformat(),
              output_sha256={p.name:sha(p.read_bytes()) for p in sorted(out.glob('*.csv'))})
    (out/'06_VALIDATION_MANIFEST.json').write_text(json.dumps(stat,indent=2,ensure_ascii=False),encoding='utf8')
    assert len(enriched)==len(master) and len(seen_ids)==len(master)
    assert all(x['match_status']!='MATCHED_UNIQUE' or x['measure_id'] for x in edges)
    print(json.dumps({k:v for k,v in stat.items() if k not in ('source_snapshots','output_sha256')},indent=2))

def defaultdict_list(rows,key):
    d=defaultdict(list)
    for row in rows:d[row[key]].append(row)
    return d

def main():
    p=argparse.ArgumentParser(description=__doc__); sub=p.add_subparsers(dest='command',required=True)
    f=sub.add_parser('fetch',help='Optional polite fetch on machine with access; fails closed on 403/parser change')
    f.add_argument('--html-dir',required=True);f.add_argument('--start-page',type=int,default=0)
    f.add_argument('--max-pages',type=int,default=30);f.add_argument('--delay',type=float,default=2)
    f.add_argument('--user-agent',default='AcademicResearch/1.0 (contact: add-your-email@example.org)')
    f.add_argument('--allow-snapshot-replacement',action='store_true');f.set_defaults(func=fetch)
    c=sub.add_parser('capture-browser',help='Import visible HTML copied from normal browser (macOS clipboard or --from-file)')
    c.add_argument('--html-dir',required=True);c.add_argument('--page',type=int,required=True)
    c.add_argument('--from-file',help='Alternative: source .html captured by browser Save As')
    c.set_defaults(func=capture_browser)
    k=sub.add_parser('check',help='Validate captured page sequence and show coverage before joining')
    k.add_argument('--html-dir',required=True);k.add_argument('--allow-partial',action='store_true');k.set_defaults(func=check)
    b=sub.add_parser('build',help='Parse saved official archive HTML and join to read-only House master')
    b.add_argument('--html-dir',required=True);b.add_argument('--master',required=True)
    b.add_argument('--output-dir',required=True);b.add_argument('--id-col',default='measure_id')
    b.add_argument('--congress-col',default='congress');b.add_argument('--bill-col',default='bill_no');b.set_defaults(func=build)
    args=p.parse_args(); args.func(args)
if __name__=='__main__':main()
