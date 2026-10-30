"""Publish synthetic mixed EEG for exercising transport; not physiological validation."""
import argparse
import math
from pathlib import Path
import random
import sys
import time
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from configure import load


def main():
    from pylsl import StreamInfo, StreamOutlet, local_clock
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config', type=Path, default=ROOT / 'config/eeg19-existing.json')
    a = p.parse_args();c = load(a.config);fs=c['sample_rate_hz']
    info = StreamInfo(c['lsl']['stream_name'], 'EEG', len(c['channels']), fs, 'float32', 'unity-coprocessor-synthetic')
    channels = info.desc().append_child('channels')
    for label in c['channels']:
        node=channels.append_child('channel');node.append_child_value('label',label);node.append_child_value('unit',c['input_unit'])
    outlet = StreamOutlet(info, chunk_size=16)
    random.seed(42);i=0;t0=local_clock();scale={'V':1e-6,'mV':1e-3,'uV':1}[c['input_unit']]
    print(f'Synthetic {len(c["channels"])}-channel {fs} Hz stream: {info.name()}',flush=True)
    while True:
        now=local_clock()
        while t0+i/fs<=now:
            t=i/fs
            sample=[scale*(10*math.sin(2*math.pi*6*t+j*0.1)+20*math.sin(2*math.pi*20*t+j*0.2)+random.gauss(0,3)) for j in range(len(c['channels']))]
            outlet.push_sample(sample,timestamp=t0+t);i+=1
        time.sleep(0.002)


if __name__ == '__main__':
    try: main()
    except KeyboardInterrupt: pass
