"""Original Cloud Dock two-note cues. Generated locally; no system sound samples."""
from pathlib import Path
import math,struct,wave
root=Path(__file__).resolve().parents[1]/'desktop/sounds';root.mkdir(exist_ok=True)
rate=44100
for name,notes in {'download':[(0,880),(.16,659.255)],'upload':[(0,659.255),(.16,880)],'error':[(0,523.251),(.20,587.330)]}.items():
 samples=[]
 for i in range(int(rate*.78)):
  t=i/rate;sample=0
  for start,freq in notes:
   age=t-start
   if age<0:continue
   env=(1-math.exp(-age*150))*math.exp(-age*8)*(min(1,max(0,(.78-t)/.08)))
   sample+=env*(math.sin(2*math.pi*freq*age)+.16*math.sin(2*math.pi*freq*2*age)+.035*math.sin(2*math.pi*freq*3*age))
  samples.append(int(max(-1,min(1,sample*.37))*32767))
 with wave.open(str(root/(name+'.wav')),'wb') as wav:
  wav.setparams((1,2,rate,0,'NONE','not compressed'));wav.writeframes(struct.pack('<'+'h'*len(samples),*samples))
 print(name,len(samples),max(abs(x) for x in samples))
