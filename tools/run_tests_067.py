from pathlib import Path
import os,sys,unittest
root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root))
(root/'work/test-runs').mkdir(parents=True,exist_ok=True)
os.environ['TEMP']=os.environ['TMP']=str(root/'work/test-runs')
pattern=sys.argv[1] if len(sys.argv)>1 else 'test_*.py'
class Tee:
 def write(self,s):sys.stdout.write(s);log.write(s)
 def flush(self):sys.stdout.flush();log.flush()
with (root/'work'/('tests-'+('full' if pattern=='test_*.py' else 'feedback')+'-result.log')).open('w',encoding='utf-8') as log:
 result=unittest.TextTestRunner(stream=Tee(),verbosity=1).run(unittest.defaultTestLoader.discover(str(root/'tests'),pattern=pattern))
 sys.exit(not result.wasSuccessful())
