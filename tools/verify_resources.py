"""Generated large-document extraction resource smoke check (no inference)."""
import argparse,ctypes,json,os
from pathlib import Path
import sys,time,tracemalloc
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

def peak_rss():
    if os.name!='nt':
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024
    class PMC(ctypes.Structure):
        _fields_=[('cb',ctypes.c_ulong),('PageFaultCount',ctypes.c_ulong)]+[(name,ctypes.c_size_t) for name in ['PeakWorkingSetSize','WorkingSetSize','QuotaPeakPagedPoolUsage','QuotaPagedPoolUsage','QuotaPeakNonPagedPoolUsage','QuotaNonPagedPoolUsage','PagefileUsage','PeakPagefileUsage']]
    kernel=ctypes.WinDLL('kernel32');kernel.GetCurrentProcess.restype=ctypes.c_void_p
    psapi=ctypes.WinDLL('psapi');psapi.GetProcessMemoryInfo.argtypes=[ctypes.c_void_p,ctypes.POINTER(PMC),ctypes.c_ulong]
    counter=PMC();counter.cb=ctypes.sizeof(counter)
    if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(),ctypes.byref(counter),counter.cb):raise OSError('Cannot read process memory')
    return counter.PeakWorkingSetSize

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True);args=parser.parse_args()
    root=Path(args.output).resolve()
    if root.exists():raise SystemExit('Choose a new evidence directory')
    root.mkdir(parents=True)
    import yaml,fitz
    cfg=root/'config.yaml';cfg.write_text(yaml.safe_dump({'database':{'path':str(root/'index.db')},'scanner':{'scan_paths':[str(root)]}}),encoding='utf8')
    from config_loader import load_config
    load_config(cfg)
    text=root/'large.txt';text.write_text('A generated paragraph about bounded document extraction.\n'*18000,encoding='utf8')
    pdf=root/'fifty-pages.pdf'
    with fitz.open() as doc:
        for i in range(50):doc.new_page().insert_text((50,60),f'Page {i+1}: bounded text PDF extraction resource fixture.')
        doc.save(pdf)
    from extractors import extract
    report={'inference':False,'budget_bytes':512*1024*1024,'budget_scope':'This controlled smoke fixture only; no OS memory quota is installed','cases':[]}
    tracemalloc.start()
    for path,kind in [(text,'text'),(pdf,'pdf_text')]:
        start=time.perf_counter();chunks=extract(1,str(path),path.suffix,kind)
        report['cases'].append({'file':path.name,'source_bytes':path.stat().st_size,'chunks':len(chunks),'max_chunk_chars':max(map(lambda c:len(c.text),chunks)),'seconds':time.perf_counter()-start})
        del chunks
    report['python_peak_bytes']=tracemalloc.get_traced_memory()[1];tracemalloc.stop();report['process_peak_rss_bytes']=peak_rss()
    report['passed']=report['process_peak_rss_bytes']<report['budget_bytes'] and all(c['max_chunk_chars']<=6000 for c in report['cases'])
    (root/'results.json').write_text(json.dumps(report,indent=2),encoding='utf8');print(json.dumps(report,indent=2))
    return 0 if report['passed'] else 1
if __name__=='__main__':raise SystemExit(main())
