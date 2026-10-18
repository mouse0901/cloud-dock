"""Pure, bounded parsing of copied share messages; never follows arbitrary URLs."""
import re
from urllib.parse import urlsplit,parse_qs,urlencode

LINK=re.compile(r'(?<![\w@/=%.-])https?://[^\s<>"\'\)\]\}，。；]+',re.I)
CODE=re.compile(r'(?:提取码|提取密码|访问码|密码|code)\s*[:：=]?\s*([A-Za-z0-9]{4,8})(?![A-Za-z0-9])',re.I)
def parse_links(text):
    if not isinstance(text,str) or len(text)>16384:return []
    text=text.replace('\\:',':').replace('\\&','&').replace('\\/','/')
    matches=list(LINK.finditer(text));result=[];seen=set()
    for i,match in enumerate(matches):
        try:
            u=urlsplit(match[0]);host=(u.hostname or '').lower()
            if host not in ('pan.baidu.com','pan.quark.cn') or u.username or u.password or u.port not in (None,443 if u.scheme.lower()=='https' else 80):continue
            query=parse_qs(u.query);path=u.path.rstrip('/')
            if host=='pan.baidu.com' and path=='/share/init':
                short=query.get('surl',[''])[0]
                if not re.fullmatch(r'[A-Za-z0-9_-]{5,127}',short):continue
                path='/s/1'+short
            if not re.fullmatch(r'/s/[A-Za-z0-9_-]{6,128}',path):continue
            code=query.get('pwd',[''])[0]
            if not re.fullmatch('[A-Za-z0-9]{4,8}',code):
                end=matches[i+1].start() if i+1<len(matches) else len(text)
                found=CODE.search(text[match.end():min(end,match.end()+120)])
                code=found[1] if found else ''
            base='https://'+host+path
            if base in seen:continue
            seen.add(base)
            result.append({'provider':'baidu' if host=='pan.baidu.com' else 'quark','url':base+('?' + urlencode({'pwd':code}) if code else ''),'password':code})
            if len(result)==20:break
        except (ValueError,UnicodeError):continue
    return result
