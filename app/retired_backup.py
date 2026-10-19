"""One-time, non-destructive retirement of the pre-sync folder backup engine."""
def retire(store):
    for row in store.all('backup'):
        if row.get('retired'):continue
        row.update(enabled=False,retired=True,status='此旧版功能已移除；两端文件与历史副本保留。')
        store.put('backup',row['id'],row)
