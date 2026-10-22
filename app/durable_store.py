"""Local durable state contains stable identifiers only, never authentication tickets."""
import json,sqlite3
from pathlib import Path
class DurableStore:
    def __init__(self,path):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(self.path)
        self.db.execute('PRAGMA journal_mode=WAL');self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('CREATE TABLE IF NOT EXISTS records(kind TEXT,id TEXT,payload TEXT,PRIMARY KEY(kind,id))')
        self.db.execute("CREATE INDEX IF NOT EXISTS record_rule_state ON records(kind,json_extract(payload,'$.rule'),json_extract(payload,'$.state'))")
        self.db.execute("CREATE INDEX IF NOT EXISTS record_recent ON records(kind,CAST(json_extract(payload,'$.pinned') AS INTEGER) DESC,CAST(json_extract(payload,'$.at') AS REAL) DESC)")
        self.db.execute('CREATE TABLE IF NOT EXISTS link_search_docs(id TEXT PRIMARY KEY)')
        self.db.execute('CREATE TABLE IF NOT EXISTS link_search_terms(id TEXT,token BLOB,PRIMARY KEY(id,token))')
        self.db.execute('CREATE INDEX IF NOT EXISTS link_search_token ON link_search_terms(token,id)')
        self.db.commit()
    def put(self,kind,id,value):
        with self.db:self.db.execute('INSERT OR REPLACE INTO records VALUES(?,?,?)',(kind,id,json.dumps(value,ensure_ascii=False)))
    def get(self,kind,id):
        row=self.db.execute('SELECT payload FROM records WHERE kind=? AND id=?',(kind,id)).fetchone()
        return json.loads(row[0]) if row else None
    def all(self,kind):return [json.loads(x[0]) for x in self.db.execute('SELECT payload FROM records WHERE kind=?',(kind,))]
    def recent_transfers(self):
        # Older releases did not store creation times. Their row order is the
        # best available approximation of the last transfer activity.
        return [json.loads(x[0]) for x in self.db.execute("SELECT payload FROM records WHERE kind='transfer' ORDER BY rowid DESC")]
    def count(self,kind):return self.db.execute('SELECT COUNT(*) FROM records WHERE kind=?',(kind,)).fetchone()[0]
    def page(self,kind,offset=0,limit=50):
        return [json.loads(x[0]) for x in self.db.execute("SELECT payload FROM records WHERE kind=? ORDER BY CAST(json_extract(payload,'$.pinned') AS INTEGER) DESC,CAST(json_extract(payload,'$.at') AS REAL) DESC LIMIT ? OFFSET ?",(kind,limit,offset))]
    def count_provider(self,kind,provider):
        return self.db.execute("SELECT COUNT(*) FROM records WHERE kind=? AND json_extract(payload,'$.provider')=?",(kind,provider)).fetchone()[0]
    def page_provider(self,kind,provider,offset=0,limit=50):
        return [json.loads(x[0]) for x in self.db.execute("SELECT payload FROM records WHERE kind=? AND json_extract(payload,'$.provider')=? ORDER BY CAST(json_extract(payload,'$.pinned') AS INTEGER) DESC,CAST(json_extract(payload,'$.at') AS REAL) DESC LIMIT ? OFFSET ?",(kind,provider,limit,offset))]
    def put_link(self,id,value,tokens):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO records VALUES(?,?,?)',('link_inbox',id,json.dumps(value,ensure_ascii=False)))
            self.db.execute('DELETE FROM link_search_terms WHERE id=?',(id,))
            self.db.executemany('INSERT OR IGNORE INTO link_search_terms VALUES(?,?)',((id,token) for token in tokens))
            self.db.execute('INSERT OR IGNORE INTO link_search_docs VALUES(?)',(id,))
    def unindexed_links(self):
        return [json.loads(x[0]) for x in self.db.execute("SELECT payload FROM records WHERE kind='link_inbox' AND id NOT IN (SELECT id FROM link_search_docs)")]
    def search_link_ids(self,tokens):
        if not tokens:return []
        marks=','.join('?' for _ in tokens)
        return [row[0] for row in self.db.execute(f'SELECT id FROM link_search_terms WHERE token IN ({marks}) GROUP BY id HAVING COUNT(DISTINCT token)=?',(*tokens,len(tokens)))]
    def issues(self,rule):
        return [json.loads(x[0]) for x in self.db.execute("SELECT payload FROM records WHERE kind='backup_file' AND json_extract(payload,'$.rule')=? AND json_extract(payload,'$.state')='excluded' LIMIT 50",(rule,))]
    def remove(self,kind,id):
        with self.db:
            self.db.execute('DELETE FROM records WHERE kind=? AND id=?',(kind,id))
            if kind=='link_inbox':
                self.db.execute('DELETE FROM link_search_terms WHERE id=?',(id,))
                self.db.execute('DELETE FROM link_search_docs WHERE id=?',(id,))
    def remove_kind(self,kind):
        with self.db:
            self.db.execute('DELETE FROM records WHERE kind=?',(kind,))
            if kind=='link_inbox':
                self.db.execute('DELETE FROM link_search_terms')
                self.db.execute('DELETE FROM link_search_docs')
    def close(self):self.db.close()
