"""Batch PBIX extraction, documentation and a local catalogue. No shell execution."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
import zipfile

from .catalog import build_catalog, read_metadata, validate_metadata, describe_html
from .extracted_report import parse_extracted_report, parse_legacy_layout, is_legacy_layout
from .model_parser import parse_model
from .pbitools_folder import assemble, is_folder_model
from .custom_visuals import from_pbix as custom_visuals_from_pbix
from .report_parser import parse_report
from .renderer import build_payload, render_html
from .linker import link
from .live_connection import from_pbix as live_from_pbix, summary as live_summary


def resolve_tool(value):
    tool = shutil.which(value) if value else shutil.which('pbi-tools')
    if not tool and value and Path(value).is_file():
        tool = str(Path(value).resolve())
    if not tool and not value:
        from .portable import available
        if available():
            return "pbixray"
    if value == "pbixray":
        return "pbixray"
    if not tool:
        raise ValueError('pbi-tools Desktop was not found. Install the Windows Desktop edition from https://github.com/pbi-tools/pbi-tools/releases and pass --pbi-tools "C:\\Tools\\pbi-tools\\pbi-tools.exe". Power BI Desktop 64-bit must also be installed.')
    if Path(tool).suffix.lower() in {'.bat', '.cmd'}:
        raise ValueError('Supply the pbi-tools executable, not a shell wrapper (.bat/.cmd).')
    if 'pbi-tools.core' in Path(tool).name.lower():
        raise ValueError('PBIX extraction requires pbi-tools Desktop, not pbi-tools.core.')
    return str(Path(tool).resolve())


def extract_pbix(source, destination, tool, timeout, log):
    started = time.time()
    if tool == 'pbixray':
        import sys
        command = [sys.executable, '-m', 'pbidocgen.portable', str(source), str(destination)]
    else:
        command = [tool, 'extract', str(source), '-extractFolder', str(destination), '-modelSerialization', 'Raw']
    with log.open('w', encoding='utf-8') as stream:
        try:
            result = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT, timeout=timeout, shell=False)
        except subprocess.TimeoutExpired as exc:
            raise ValueError(f'Extraction timed out after {timeout}s; see {log.name}. Increase --extract-timeout if needed.') from exc
    if result.returncode:
        raise ValueError(f'pbi-tools exited with code {result.returncode}; see {log.name}. Check its compatibility with this PBIX/Power BI Desktop version.')
    beside = Path(source).with_suffix('')
    if not (Path(destination).is_dir() and any(Path(destination).iterdir())) and beside.is_dir() \
            and (beside / 'Model').exists() and beside.stat().st_mtime >= started - 5:
        # For older PBIX files pbi-tools ignores -extractFolder and writes next to
        # the PBIX. Move that output into the working folder so nothing is left behind.
        if Path(destination).exists():
            shutil.rmtree(destination)
        shutil.move(str(beside), str(destination))
        with log.open('a', encoding='utf-8') as stream:
            stream.write(f'\npbi-doc-gen: pbi-tools wrote to {beside} instead of the extract folder; moved it.\n')


def load_extracted(folder, source, has_embedded_model):
    # Raw serialization is Model/database.json. Accept TMDL and BIM too for
    # extractors which already produce project-format definitions.
    models = [folder / 'Model' / 'database.json', folder / 'Model' / 'model.bim', folder / 'model.bim']
    model_path = next((p for p in models if p.is_file()), None)
    if model_path is None and (folder / 'Model' / 'model.tmdl').is_file():
        model_path = folder / 'Model'
    if model_path is None:
        candidates = list(folder.glob('*.SemanticModel'))
        if len(candidates) == 1:
            model_path = candidates[0]
    if model_path is None:
        # pbi-tools falls back to its Default layout for some older PBIX files;
        # look for any model definition under Model/ (TMDL under definition/,
        # a database.json one level down, a .bim).
        model_root = folder / 'Model'
        found = sorted(model_root.rglob('model.tmdl')) + sorted(model_root.rglob('database.json')) \
            + sorted(model_root.rglob('*.bim')) if model_root.is_dir() else []
        if found:
            model_path = found[0] if found[0].suffix != '.tmdl' else found[0].parent
    if has_embedded_model and model_path is None:
        written = sorted(str(p.relative_to(folder)) for p in folder.glob('*/*'))[:25] if folder.is_dir() else []
        raise ValueError('PBIX contains an embedded model but extraction produced no readable model definition. '
                         'pbi-tools wrote: ' + (', '.join(written) or 'nothing'))
    raw_database = model_path is not None and model_path.name == 'database.json'
    if model_path is not None and model_path.name == 'database.json':
        model_dir = model_path.parent
        if is_folder_model(model_dir):
            assembled = folder / 'assembled-model.bim'
            assembled.write_text(json.dumps(assemble(model_dir)), encoding='utf-8')
            model_path = assembled
    model = parse_model(model_path) if model_path else None
    if model and raw_database and model.get('name') in (None, '', 'database'):
        # pbi-tools' raw database.json often carries no model name.
        model['name'] = source.stem
    if source.suffix.lower() == '.abf':
        return model, None
    report_root = folder / 'Report'
    if not report_root.exists():
        candidates = list(folder.glob('*.Report'))
        if len(candidates) == 1:
            report_root = candidates[0]
    legacy = (report_root / 'sections').is_dir() or (report_root / 'report.json').is_file()
    if not legacy and not (report_root / 'definition' / 'pages').is_dir():
        # Newer PBIX files store the report as PBIR (Report/definition/...),
        # which pbi-tools does not extract. Read it from the PBIX itself.
        pbir = extract_pbir(source, folder / 'pbir.Report')
        if pbir:
            report_root = pbir
    if (report_root / 'definition' / 'pages').is_dir() or (report_root / 'pages').is_dir():
        report = parse_report(report_root)
        report['name'] = source.stem
    elif is_legacy_layout(report_root):
        report = parse_legacy_layout(report_root, source.stem)
    else:
        report = parse_extracted_report(report_root, source.stem)
    if model is None:
        live = live_from_pbix(source)
        if live:
            report['liveConnection'] = live
            where = live_summary(live)
            message = ('This report has a live connection to a remote semantic model: ' + where + '. '
                       'It has no embedded model, so tables, measures and data sources live in that model and '
                       'are not available here. Supply the model (e.g. exported via XMLA or as a .SemanticModel) '
                       'to document them; the field manifest lists what the report needs from it.')
        else:
            message = ('No embedded semantic model was extracted. This is report-only documentation; '
                       'remote model tables, measures and data sources are not available.')
        report['warnings'].append(dict(severity='warning', category='External semantic model', message=message))
    # Custom visual display names ship inside the PBIX (Report/CustomVisuals/).
    report['customVisuals'] = {**custom_visuals_from_pbix(source), **report.get('customVisuals', {})}
    if source.suffix.lower() == '.abf':
        report = None
    coverage = folder / "extraction.json"
    if report is not None and coverage.is_file():
        report["extraction"] = json.loads(coverage.read_text(encoding="utf-8"))
    return model, report


PBIR_PREFIX = 'Report/definition/'
PBIR_LIMIT = 200 * 1024 * 1024  # uncompressed bytes; a report definition is far smaller


def extract_pbir(source, destination):
    """Copy Report/definition/** out of a PBIX (a zip) without executing anything.

    Returns the report root (containing definition/) or None when the PBIX has
    no PBIR report. Only plain relative paths under Report/definition are
    written, so a crafted archive cannot write elsewhere.
    """
    try:
        archive = zipfile.ZipFile(source)
    except (zipfile.BadZipFile, OSError):
        return None
    with archive:
        members = [m for m in archive.infolist() if m.filename.replace('\\', '/').startswith(PBIR_PREFIX)
                   and not m.is_dir()]
        if not any('/pages/' in m.filename.replace('\\', '/') for m in members):
            return None
        if sum(m.file_size for m in members) > PBIR_LIMIT:
            raise ValueError('PBIR report definition in the PBIX is unexpectedly large; refusing to extract it')
        root = Path(destination)
        for member in members:
            relative = Path(*member.filename.replace('\\', '/').split('/')[1:])
            if relative.is_absolute() or '..' in relative.parts or not relative.parts:
                raise ValueError(f'Unsafe path in PBIX report definition: {member.filename}')
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(member))
    return root


def output_name(source, root):
    relative = source.relative_to(root).as_posix()
    stem = re.sub(r'[^\w .-]', '_', source.stem, flags=re.UNICODE).strip(' .')[:100] or 'Report'
    digest = hashlib.sha256(relative.encode('utf-8')).hexdigest()[:12]
    return f'{stem}--{digest}.html'


def atomic_json(path, value):
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, suffix='.tmp', delete=False) as stream:
        tmp = Path(stream.name)
        json.dump(value, stream, indent=2, ensure_ascii=False)
    try:
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def run_batch(input_path, output_dir=None, recursive=False, tool=None, timeout=600, extractor=None, model_path=None):
    source_root = Path(input_path).resolve()
    if source_root.is_file():
        if source_root.suffix.lower() != '.pbix':
            raise ValueError('Expected a .pbix file')
        sources, root = [source_root], source_root.parent
    elif source_root.is_dir():
        root = source_root
        sources = sorted((p for p in (root.rglob('*') if recursive else root.iterdir())
                          if p.is_file() and not p.is_symlink() and p.suffix.lower() == '.pbix'),
                         key=lambda p: p.relative_to(root).as_posix())
    else:
        raise ValueError(f'PBIX input not found: {source_root}')
    if not sources:
        raise ValueError('No PBIX files found. Use --recursive to include subfolders.')
    if model_path and len(sources) != 1:
        raise ValueError('--model pairs one model with one report; use it with --pbix, not a folder')
    if model_path and not Path(model_path).exists():
        raise ValueError(f'Model not found: {model_path}')
    if timeout <= 0:
        raise ValueError('--extract-timeout must be greater than zero')
    executable = resolve_tool(tool) if extractor is None else tool
    extractor = extractor or extract_pbix
    output = Path(output_dir).resolve() if output_dir else root / 'documentation'
    output.mkdir(parents=True, exist_ok=True)
    home = output / 'pbi-home.html'
    if home.exists() and 'name="pbi-documentation-hub"' not in home.read_text(encoding='utf-8-sig'):
        raise ValueError('pbi-home.html already exists and is not a generated catalogue; choose another output folder')
    logs = output / 'pbix-logs'
    logs.mkdir(exist_ok=True)
    results = []
    for number, source in enumerate(sources, 1):
        name = output_name(source, root)
        target = output / name
        log = logs / (Path(name).stem + '.log')
        row = dict(source=str(source), filename=name, title=source.stem, status='failed',
                   previousHtmlRetained=target.exists(), log=str(log.relative_to(output)))
        print(f'[{number}/{len(sources)}] {source.relative_to(root)}', flush=True)
        try:
            metadata = read_metadata(target.read_text(encoding='utf-8-sig')) if target.exists() else None
            existing = describe_html(target.read_text(encoding='utf-8-sig'), name) if target.exists() else None
            if existing and existing.get('pbixSource') != str(source):
                raise ValueError('Existing HTML belongs to a different source; choose another output directory')
            if target.exists() and metadata is None:
                raise ValueError('Existing output is not a metadata-enabled generated document; refusing to overwrite it')
            metadata = validate_metadata(metadata or {})
            if not metadata['reportLocation']:
                metadata['reportLocation'] = str(source)
            with zipfile.ZipFile(source) as archive:
                has_model = any(n.replace('\\', '/').strip('/').casefold() == 'datamodel' for n in archive.namelist())
            with tempfile.TemporaryDirectory(prefix='pbi-doc-extract-') as temporary:
                work = Path(temporary)
                extracted = work / 'extracted'
                extractor(source, extracted, executable, timeout, log)
                model, report = load_extracted(extracted, source, has_model)
                if model is None and model_path:
                    model = parse_model(Path(model_path))
                linked = link(model, report) if model else None
                payload = build_payload(model, report, linked, source.stem)
                payload['documentation'] = metadata
                payload['pbixSource'] = str(source)
                # Write on the target filesystem, then replace in one operation.
                # A failed extraction/render never destroys a previous HTML.
                with tempfile.TemporaryDirectory(prefix='.pbi-stage-', dir=output) as stage:
                    rendered = render_html(payload, Path(stage) / name)
                    os.replace(rendered, target)
                warnings = [w['message'] for w in report['warnings']]
                row.update(status='generated', mode=payload['mode'], warnings=warnings,
                           previousHtmlRetained=False, pages=len(report['pages']))
                print(f'  Wrote {name} ({payload["mode"]})', flush=True)
        except Exception as exc:
            row['error'] = f'{type(exc).__name__}: {exc}'
            if log.exists():
                with log.open('a', encoding='utf-8') as stream:
                    stream.write('\nDocumentation failure: ' + row['error'] + '\n')
            print('  FAILED: ' + row['error'], flush=True)
        results.append(row)
    summary = dict(generatedAt=datetime.now(timezone.utc).isoformat(), input=str(source_root),
                   output=str(output), generated=sum(r['status'] == 'generated' for r in results),
                   failed=sum(r['status'] == 'failed' for r in results), files=results)
    atomic_json(output / 'pbix-batch-results.json', summary)
    build_catalog(output)
    print(f'Generated {summary["generated"]}; failed {summary["failed"]}. Open {home}', flush=True)
    return summary
