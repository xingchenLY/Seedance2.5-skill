// Reusable Artifact Tool exporter. Input is workbook_data.py output, never prompt overrides.
import fs from 'node:fs/promises';
import path from 'node:path';
import { constants } from 'node:fs';
import { createHash, randomUUID } from 'node:crypto';
import { createRequire } from 'node:module';
import { fileURLToPath, pathToFileURL } from 'node:url';

const args = process.argv.slice(2);
const overwrite = args.includes('--overwrite');
const positional = args.filter(arg => arg !== '--overwrite');
const [input, requestedOutput, runtimeRoot] = positional;
if (!input || !requestedOutput || positional.length > 3 || positional.some(arg => arg.startsWith('--')))
  throw new Error('Usage: node build_workbook.mjs data.json output.xlsx [node_modules] [--overwrite]');
const requireModule = createRequire(runtimeRoot ? path.join(path.resolve(runtimeRoot), '__pipeline__.cjs') : import.meta.url);
const { Workbook, SpreadsheetFile, FileBlob } = await import(pathToFileURL(requireModule.resolve('@oai/artifact-tool')).href);
const digest = bytes => createHash('sha256').update(bytes).digest('hex');
const exists = async p => { try { await fs.access(p); return true; } catch { return false; } };
const data = JSON.parse(await fs.readFile(input, 'utf8'));
if (!Array.isArray(data.sheets) || !data.sheets.length) throw new Error('Missing selected worksheet data');
if (data.legacy_prompt_verification_required?.length) throw new Error('Legacy images need final prompt verification before export');
const outputRoot = path.dirname(path.resolve(requestedOutput));
const cachePath = path.resolve(input) + '.export.json';
const previewDir = path.join(path.dirname(path.resolve(input)), 'workbook-previews');
const rendererHash = digest(await fs.readFile(fileURLToPath(import.meta.url)));
const groupedSheet = spec => ['人物资产','场景资产'].includes(spec.name);
const previewCount = spec => groupedSheet(spec) ? 3 : 2;
const old = await exists(cachePath) ? JSON.parse(await fs.readFile(cachePath, 'utf8')) : {};
const owned = old.output && (old.requested_output || old.output) === path.resolve(requestedOutput)
  && (!overwrite || path.resolve(old.output) === path.resolve(requestedOutput))
  && path.dirname(old.output) === outputRoot && await exists(old.output)
  && digest(await fs.readFile(old.output)) === old.file_hash;
// Explicit overwrite targets the supplied original path even when cache ownership was lost.
const overwriteHash = overwrite && await exists(path.resolve(requestedOutput))
  ? digest(await fs.readFile(path.resolve(requestedOutput))) : null;
if (owned && old.content_hash === data.content_hash && old.renderer_hash === rendererHash
    && data.sheets.every(spec => old.previews?.[spec.name]?.length === previewCount(spec))
    && (await Promise.all(Object.values(old.previews).flat().map(exists))).every(Boolean)) {
  console.log(JSON.stringify({ok:true, status:'reused', output:old.output, rendered_sheets:[]}));
  process.exit(0);
}
const sameSheets = owned && JSON.stringify(old.sheets?.map(s => s.name)) === JSON.stringify(data.sheets.map(s => s.name));
const wb = sameSheets ? await SpreadsheetFile.importXlsx(await FileBlob.load(old.output)) : Workbook.create();
const previews = {}, rendered = [], sheetRecords = [];
await fs.mkdir(outputRoot, {recursive:true});
await fs.mkdir(previewDir, {recursive:true});
const units = text => [...String(text ?? '')].reduce((n,c) => n + (/^[\x00-\x7f]$/.test(c) ? 0.55 : 1), 0);
const linesFor = (text,width) => String(text ?? '').split('\n').reduce((n,s) => n + Math.max(1,Math.ceil(units(s)/(width * 0.47 - 1))),0);
for (const spec of data.sheets) {
  if (spec.headers.length !== 5 || spec.rows.some(r => r.length !== 5 || typeof r[4] !== 'string' || !r[4].trim()))
    throw new Error(`Incomplete five-column rows: ${spec.name}`);
  const prior = old.sheets?.find(s => s.name === spec.name);
  const cachedPreviews = old.previews?.[spec.name] || [];
  const unchanged = sameSheets && prior?.content_hash === spec.content_hash && old.renderer_hash === rendererHash;
  const sh = sameSheets ? wb.worksheets.getItem(spec.name) : wb.worksheets.add(spec.name);
  if (!unchanged) {
    if (sameSheets) { const used = sh.getUsedRange(); used.unmerge(); used.clear({applyTo:'all'}); }
    sh.showGridLines = false;
    const style = spec.style;
    const widths = style.widths.map((w,i) => Math.max(w,[20,25,25,28,75][i]));
    // Widen long prompt columns rather than silently clipping at Excel's row-height limit.
    while (spec.rows.some(r => linesFor(r[4],widths[4]) * 16 + 12 > 400) && widths[4] < 250) widths[4] += 5;
    sh.getRange('A1:E1').values = [spec.headers];
    sh.getRange('A1:E1').format = {
      fill:style.header_fill, font:{name:style.font_name,size:style.header_size,bold:true,color:'#0F1115'},
      horizontalAlignment:'center',verticalAlignment:'center',wrapText:true
    };
    sh.getRange('A1:E1').format.rowHeight = Math.max(style.header_height,29);
    widths.forEach((width,i) => { sh.getRange(`${'ABCDE'[i]}:${'ABCDE'[i]}`).format.columnWidth = width; });
    if (spec.rows.length) {
      const range = sh.getRange(`A2:E${spec.rows.length+1}`);
      range.values = spec.rows.map(r => r.map(v => typeof v === 'string' && v.startsWith('=') ? "'"+v : v));
      range.format.font = {name:style.font_name,size:11,color:'#0F1115'};
      range.format.wrapText = true;
      range.format.verticalAlignment = 'center';
      sh.getRange(`E2:E${spec.rows.length+1}`).format.verticalAlignment = 'top';
      for (let i=0;i<spec.rows.length;i++) {
        const height = Math.max(48, ...spec.rows[i].map((v,c) => linesFor(v,widths[c])*16+12));
        if (height > 409) throw new Error(`Row too tall; adjust layout before delivery: ${spec.name} row ${i+2}`);
        sh.getRange(`A${i+2}:E${i+2}`).format.rowHeight = height;
      }
      if (groupedSheet(spec)) {
        for (let start=0;start<spec.rows.length;) {
          let end=start+1;
          while (end<spec.rows.length && spec.rows[end][0] === spec.rows[start][0]) end++;
          const namedGroup = spec.rows[start][0] != null && String(spec.rows[start][0]).trim() !== '';
          if (end-start>1 && namedGroup) sh.mergeCells(`A${start+2}:A${end+1}`);
          if (namedGroup) {
            for (let labelStart=start;labelStart<end;) {
              let labelEnd=labelStart+1;
              while (labelEnd<end && spec.rows[labelEnd][1] === spec.rows[labelStart][1]) labelEnd++;
              const label = spec.rows[labelStart][1];
              if (labelEnd-labelStart>1 && label != null && String(label).trim() !== '')
                sh.mergeCells(`B${labelStart+2}:B${labelEnd+1}`);
              labelStart=labelEnd;
            }
          }
          start=end;
        }
      }
    }
    sh.freezePanes.freezeRows(1);
  }
  sheetRecords.push({name:spec.name,content_hash:spec.content_hash});
  previews[spec.name] = cachedPreviews;
  if (!unchanged || cachedPreviews.length !== previewCount(spec) || !(await Promise.all(cachedPreviews.map(exists))).every(Boolean))
    rendered.push(spec);
}
wb.recalculate();
for (const spec of rendered) {
  const longest = spec.rows.length ? spec.rows.reduce((b,r,i) => units(r[4])>units(spec.rows[b][4]) ? i : b,0)+2 : 1;
  previews[spec.name] = [];
  // Group labels span multiple rows; keep narrow previews outside partial merges.
  const views = groupedSheet(spec)
    ? [['header','A1:E1'],['longest',`C${longest}:E${longest}`]]
    : [['header',`A1:E${Math.min(spec.rows.length+1,3)}`],['longest',`A${longest}:E${longest}`]];
  if (groupedSheet(spec)) {
    // End at a complete parent group so the preview does not clip merged labels.
    let groupRows = Math.min(spec.rows.length,11);
    while (groupRows < spec.rows.length && spec.rows[groupRows][0] === spec.rows[groupRows-1][0]) groupRows++;
    views.push(['grouping',`A1:D${groupRows+1}`]);
  }
  for (const [suffix,range] of views) {
    const preview = await wb.render({sheetName:spec.name,range,scale:1,format:'png'});
    const target = path.join(previewDir,`${spec.name}-${suffix}.png`);
    await fs.writeFile(target,new Uint8Array(await preview.arrayBuffer()));
    previews[spec.name].push(target);
  }
}
const inspection = await wb.inspect({kind:'sheet',include:'id,name',maxChars:1600});
await fs.writeFile(path.resolve(input)+'.inspect.ndjson',inspection.ndjson);
let output = overwrite ? path.resolve(requestedOutput) : owned ? old.output : path.resolve(requestedOutput);
const temporary = path.join(outputRoot,`.workbook-${randomUUID()}.xlsx`);
try {
  const xlsx = await SpreadsheetFile.exportXlsx(wb);
  await xlsx.save(temporary);
  if (!(await fs.stat(temporary)).size) throw new Error('Empty workbook export');
  if (overwrite) {
    const currentHash = await exists(output) ? digest(await fs.readFile(output)) : null;
    if (currentHash !== overwriteHash) throw new Error('Workbook changed during export; reread before overwriting');
    // Replace only after a complete export; a locked target leaves the original intact.
    await fs.rename(temporary,output);
  } else if (owned) {
    if (digest(await fs.readFile(output)) !== old.file_hash) throw new Error('Workbook changed externally during export');
    await fs.rename(temporary,output);
  } else {
    const parsed = path.parse(path.resolve(requestedOutput));
    for (let v=1;;v++) {
      output = path.join(parsed.dir,`${parsed.name}${v===1?'':`-v${v}`}${parsed.ext}`);
      try { await fs.copyFile(temporary,output,constants.COPYFILE_EXCL); break; }
      catch (e) { if (e.code !== 'EEXIST') throw e; }
    }
  }
} finally { await fs.rm(temporary,{force:true}); }
await fs.writeFile(cachePath,JSON.stringify({output,requested_output:path.resolve(requestedOutput),file_hash:digest(await fs.readFile(output)),
  content_hash:data.content_hash,renderer_hash:rendererHash,sheets:sheetRecords,previews},null,2));
console.log(JSON.stringify({ok:true,status:'exported',output,rendered_sheets:rendered.map(s=>s.name),previews,
  unresolved_images:data.unresolved_images}));
