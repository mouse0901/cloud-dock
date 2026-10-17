"""Validated local checkpoints; never persist tickets, cookies or headers."""
from pathlib import Path
import re
from app.direct_download import DownloadError

def checkpoint(job):
    if job.state not in ('paused','failed') or job.requires_restart or not getattr(job,'part',None) or not job.part.exists():return None
    try:
        job._check_part();st=job.part.stat()
    except (OSError,DownloadError):return None
    return {'version':1,'part':job.part.name,'target':job.target.name,'done':job.done,'total':job.total,
            'identity':[st.st_dev,st.st_ino],'mtime':st.st_mtime_ns,'length':st.st_size,
            'validator':list(job._validator) if job._validator else None,'md5':job._source_md5,
            'md5_content':job._md5_is_content,'content_type':job._content_type,
            'segments':[[s.start,s.end,s.written] for s in (getattr(job,'segments',None) or [])]}

def restore(job,value):
    def fail():raise DownloadError('download_checkpoint','断点文件已变化或不可用，请取消旧任务后重新下载。')
    if not isinstance(value,dict) or value.get('version')!=1 or value.get('total')!=job.total:fail()
    part=value.get('part','');target=value.get('target','')
    if not isinstance(part,str) or not re.fullmatch(r'\.cloud-dock-[a-f0-9]{32}\.part',part):fail()
    if not isinstance(target,str) or Path(target).name!=target or target in ('','.','..') or any(c in target for c in '/\\:'):fail()
    root=job.destination
    if any(p.is_symlink() or getattr(p,'is_junction',lambda:False)() for p in [root,*root.parents]):fail()
    tmp=root/part;end=root/target
    if tmp.is_symlink() or not tmp.is_file() or end.exists():fail()
    st=tmp.stat()
    if [st.st_dev,st.st_ino]!=value.get('identity') or st.st_mtime_ns!=value.get('mtime') or st.st_size!=value.get('length'):fail()
    done=value.get('done');segments=value.get('segments',[])
    if type(done) is not int or not 0<=done<=job.total or not isinstance(segments,list) or len(segments)>4096:fail()
    parsed=[];offset=0
    if segments:
        if not hasattr(job,'segments'):fail()
        from app.parallel_download import Segment
        for entry in segments:
            if not isinstance(entry,list) or len(entry)!=3 or any(type(n) is not int for n in entry):fail()
            start,finish,written=entry
            if start!=offset or finish<start or not 0<=written<=finish-start+1:fail()
            parsed.append(Segment(start,finish,written));offset=finish+1
        if offset!=job.total or sum(s.written for s in parsed)!=done or st.st_size!=job.total:fail()
    elif st.st_size!=done:fail()
    validator=value.get('validator')
    if validator is not None and (not isinstance(validator,list) or len(validator)!=2 or validator[0] not in ('etag','last-modified') or not isinstance(validator[1],str) or len(validator[1])>1024 or any(c in validator[1] for c in '\r\n')):fail()
    md5=value.get('md5')
    if md5 is not None and (not isinstance(md5,str) or not re.fullmatch('[a-fA-F0-9]{32}',md5)):fail()
    job.part,job.target,job.done=tmp,end,done
    job._part_identity=(st.st_dev,st.st_ino);job._validator=tuple(validator) if validator else None
    job._source_md5=md5;job._md5_is_content=value.get('md5_content');job._content_type=str(value.get('content_type',''))[:200]
    if parsed:job.segments=parsed;job._probe_supported=True;job.transfer_mode='parallel'
    job._check_part()
