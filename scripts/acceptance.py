"""Public sample acceptance: independently checked facts, generated HTML, provenance.

No refresh or corporate inputs. The paired thin report is deliberately synthetic;
it demonstrates linkage to a real Microsoft backup, not a discovered live pairing.
"""
from pathlib import Path
import json
import tempfile
import zipfile
import html

from fetch_samples import ROOT, fetch
from pbidocgen.portable import load_model
from pbidocgen.model_parser import parse_model
from bidoc_generator.extract import extract_pbix
from bidoc_engines.generate import GenerateRequest, generate
from bidoc_contracts import validate_artifact
from bidoc_engines.power_bi import VIEW_IDS
from bidoc_engines import power_bi, adf


def main():
    cache=fetch();out=ROOT/'samples/output';out.mkdir(parents=True,exist_ok=True)
    results=[]
    # Extract exactly the desired members; never trust archive paths.
    def member(archive, suffix, name):
        with zipfile.ZipFile(cache/archive) as z:
            matches=[n for n in z.namelist() if n.endswith(suffix)]
            assert len(matches)==1,matches
            dest=cache/name;dest.write_bytes(z.read(matches[0]));return dest
    abf=member('adventure-works-tabular-model-1200-full-database-backup.zip','.abf','AdventureWorks-Tabular.abf')
    bim=member('adventure-works-tabular-model-1200-project.zip','/Model.bim','AdventureWorks-reference.bim')
    actual,expected=load_model(abf),parse_model(bim)
    assert (len(actual['tables']),len(actual['measures']),len(actual['relationships']),len(actual['roles']))==(7,19,8,3)
    assert sorted(actual['roles'],key=lambda r:r['name']) == sorted(expected['roles'],key=lambda r:r['name']), 'role permissions and filters'
    for field in ('expression','formatString','isHidden','description','displayFolder'):
        def measures(m):return {(r['table'],r['name']):r.get(field) for r in m['measures']}
        assert measures(actual)==measures(expected),field
    for field in ('sortByColumn','isHidden','dataType','expression'):
        def columns(m):return {(t['name'],c['name']):c.get(field) for t in m['tables'] for c in t['columns']}
        assert columns(actual)==columns(expected),field

    def run(path,kind,question,check,model=None,synthetic=False):
        with tempfile.TemporaryDirectory() as d:
            model = str(model) if model else None
            extract=None
            if kind in ('pbix','abf'):
                extract=extract_pbix(path,Path(d)/'extract','pbixray',timeout=120)
                payload=power_bi.load(extract,'extracted',path.stem,pbix=path,model_path=model)
            else:
                payload=(adf.load(path,kind) if kind.startswith('adf') else power_bi.load(path,kind,model_path=model))
            check(payload)
            for profile in ('local','shared'):
                req=GenerateRequest(engine='adf' if kind.startswith('adf') else 'power_bi',source_path=str(path),source_kind=kind,
                    output_dir=str(out),profile=profile,extracted_path=str(extract) if extract else None,model_path=str(model) if model else None)
                result=generate(req)
                assert result.status in ('completed','local_only'),result.errors
                file=Path(result.artifact_path)
                if result.status=='completed':
                    validate_artifact(file.read_bytes(),view_ids=adf.VIEW_IDS if kind.startswith('adf') else VIEW_IDS)
                if profile=='shared':
                    assert 'D:\\\\DP500' not in file.read_text()
                results.append({'sample':path.name,'profile':profile,'status':result.status,'file':file.name,
                                'question':question,'synthetic':synthetic,'warnings':result.warnings})
            return payload

    def modes(expected):
        def check(p):
            found={t['name']:{x['mode'] for x in t['partitions']} for t in p['model']['tables']}
            for name,mode in expected.items():assert found[name]=={mode},(name,found[name])
            assert p['summary']['deletionCandidates']==0
            assert any(s['database']=='AdventureWorksDW2022-DP500' for s in p['summary']['sources'])
            assert p['report']['pages']
        return check
    run(cache/'DP500 04 DirectQuery SQL Server.pbix','pbix','Which SQL objects supply the five DirectQuery tables?',modes(dict.fromkeys(['Date','Product','Reseller','Territory','Sales'],'directQuery')))
    run(cache/'DP500 08 Composite model.pbix','pbix','Which tables use DirectQuery and which depend on the Excel workbook?',modes({'Sales':'directQuery','Targets':'import'}))
    run(cache/'DP500 11 Dual storage mode.pbix','pbix','Which dimensions are Dual, and where do Sales and Targets originate?',modes({'Order Date':'dual','Sales Territory':'dual','Product':'dual','Sales':'directQuery','Targets':'import'}))
    def backup(p):
        assert p['mode']=='semantic-only' and len(p['model']['measures'])==19
        assert any('encrypted' in w for w in p['extraction']['warnings'])
        assert any('DimCustomer' in (s.get('object') or '') for s in p['sourceObjects'])
    run(abf,'abf','What are the 19 measures, relationships, roles and upstream SQL objects? Why is the server unknown?',backup)
    for name,pages,visuals in [('live-connection-ssas.pbix',1,3),('live-connection-pbiservice.pbix',2,5)]:
        def thin(p):
            assert p['mode']=='report-only' and p['liveSource']
            assert len(p['report']['pages'])==pages
            assert sum(len(x['visuals']) for x in p['report']['pages'])==visuals
        run(cache/name,'pbix','Which remote model does this report require, and which visuals/fields are visible without it?',thin)
    def imported(p):
        assert p['model']['measures'] and p['report']['pages']
        assert p['summary']['deletionCandidates']==0
    run(cache/'Adventure Works, Internet Sales.pbix','pbix','Which measures and model objects do the report pages reference?',imported)
    def advanced(p):
        assert p['model']['measures'] and not p['extraction']['complete']
        assert p['summary']['deletionCandidates']==0
    run(cache/'AdventureWorks Sales.pbix','pbix','Are calculation-group coverage limits visible rather than silently discarded?',advanced)
    def remote(p):
        parts=[q for t in p['model']['tables'] for q in t['partitions'] if q['mode']=='directQuery']
        assert len(parts)==9
        assert all(q['source']['sourceType']=='Power BI semantic model (XMLA endpoint)' for q in parts)
    sample=ROOT/'components/power-bi/pbip-samples/directquery-to-analysis-services'
    run(next(sample.glob('*.pbip')),'pbip','Which nine local DirectQuery tables read the remote semantic model?',remote)
    thin_root=ROOT/'components/power-bi/pbip-samples/thin-report-live-connection'
    def pbir(p):
        assert len(p['report']['pages'])==2
        assert not any('missing from the extract' in w['message'] for w in p['report']['warnings'])
    run(next(thin_root.rglob('definition.pbir')).parent,'pbir','Are both real PBIR pages present and correctly ordered?',pbir)
    def factory(p):
        assert len(p['pipelines'])==17
        assert any(x['name']=='StartingPipeline' for x in p['pipelines'])
    run(cache/'Adventure Works Demo Pipeline.json','adf_arm','What does StartingPipeline orchestrate, and which sources remain parameterized?',factory)

    # Synthetic report referencing the actual backup's first measure. Endpoint is
    # intentionally fictional; name matching is not evidence of server identity.
    report=cache/'Synthetic-AdventureWorks.Report';defs=report/'definition';pg=defs/'pages'/'overview'
    vis=pg/'visuals'/'sales';vis.mkdir(parents=True,exist_ok=True)
    def put(p,v):p.write_text(json.dumps(v),encoding='utf-8')
    measure=actual['measures'][0]
    put(report/'definition.pbir',{'version':'4.0','datasetReference':{'byConnection':{'connectionString':f'Data Source=synthetic-test-server;Initial Catalog={actual["name"]};Cube=Model'}}})
    put(defs/'report.json',{'themeCollection':{}})
    put(defs/'pages'/'pages.json',{'pageOrder':['overview'],'activePageName':'overview'})
    put(pg/'page.json',{'name':'overview','displayName':'Synthetic pairing demonstration','width':1280,'height':720})
    put(vis/'visual.json',{'name':'sales','visual':{'visualType':'card','query':{'queryState':{'Values':{'projections':[{'field':{'Measure':{'Expression':{'SourceRef':{'Entity':measure['table']}},'Property':measure['name']}}}]}}}}})
    with tempfile.TemporaryDirectory() as d:
        extracted=extract_pbix(abf,d,'pbixray')
        def paired(p):
            assert p['mode']=='combined' and p['livePairing']['status']=='Supplied separately'
            assert 'not verified' in p['livePairing']['note']
            assert len(p['model']['measures'])==19 and p['linked']
        run(report,'pbir','Can an explicitly paired backup resolve a thin report measure, with the pairing clearly unverified?',paired,
            model=extracted/'Model/database.json',synthetic=True)
    (out/'results.json').write_text(json.dumps(results,indent=2))
    cards=''.join(f'<li><a href="{html.escape(r["file"],quote=True)}">{html.escape(r["sample"])} — {r["profile"]}</a><p>{html.escape(r["question"])} {"SYNTHETIC PAIRING DEMO" if r["synthetic"] else "Public sample"}. Status: {r["status"]}.</p></li>' for r in results)
    (out/'index.html').write_text('<!doctype html><meta charset="utf-8"><title>BI documentation acceptance gallery</title><style>body{max-width:1000px;margin:48px auto;font:16px system-ui;line-height:1.6;background:#f6f7fa;color:#182235}li{background:white;padding:20px;margin:16px 0;border-radius:12px}a{color:#185abc}p{margin-bottom:0}</style><h1>Public sample documentation</h1><p>Offline snapshots; no server refresh. Each link is generated output checked against an explicit question. The pairing demo is synthetic and is labelled as such.</p><ul>'+cards+'</ul>')
    print(f'Passed {len(results)} generated outputs. Open {out / "index.html"}')


if __name__=='__main__':main()
