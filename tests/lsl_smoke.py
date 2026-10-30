"""Exercise a live local LSL outlet/inlet and the bridge's actual channel mapping."""
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import json
import uuid
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'tools'),str(ROOT/'host')]
from configure import load
from bridge import mapping, data_packet
from pylsl import StreamInlet, resolve_byprop


def main():
    c=load(ROOT/'config/eeg19-existing.json')
    c['lsl']['stream_name']='CoprocessorTest-'+uuid.uuid4().hex
    with tempfile.TemporaryDirectory() as folder:
        path=Path(folder)/'config.json';path.write_text(json.dumps(c))
        publisher=subprocess.Popen([sys.executable,str(ROOT/'host/synthetic_lsl.py'),'--config',str(path)],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        inlet=None
        try:
            streams=resolve_byprop('name',c['lsl']['stream_name'],timeout=10)
            if len(streams)!=1:raise RuntimeError('Synthetic LSL stream was not discovered')
            inlet=StreamInlet(streams[0],max_buflen=2,processing_flags=0,recover=False)
            info=inlet.info(timeout=10);node=info.desc().child('channels').child('channel');labels=[];units=[]
            for _ in range(info.channel_count()):
                labels.append(node.child_value('label'));units.append(node.child_value('unit'));node=node.next_sibling()
            indices=mapping(c,labels,units,info.nominal_srate())
            inlet.open_stream(timeout=10);inlet.time_correction(timeout=10);inlet.flush()
            count=0;previous=0;deadline=time.monotonic()+5
            while count<500 and time.monotonic()<deadline:
                rows,ts=inlet.pull_chunk(timeout=.2,max_samples=32)
                if not rows:continue
                assert all(t>previous for t in ts);previous=ts[-1]
                selected=[[row[i]*1e6 for i in indices] for row in rows]
                data_packet(count,selected,ts,len(indices));count+=len(rows)
            assert count>=500
            print(json.dumps({'lsl_smoke':'passed','sample_rate_hz':info.nominal_srate(),'channels':len(indices),'received_samples':count}))
        finally:
            if inlet:inlet.close_stream()
            publisher.terminate()
            try:publisher.communicate(timeout=5)
            except subprocess.TimeoutExpired:publisher.kill();publisher.communicate()


if __name__=='__main__':main()
