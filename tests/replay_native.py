"""Replay the saved .easy recording through the actual C++ DSP on Linux."""
import argparse
import json
from pathlib import Path
import numpy as np
from test_system import PipelineTests, ROOT, config_id


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--easy',type=Path,default=ROOT.parent/'Esp32ComputeModule/phase37_online_pipeline/results/patient01_seg.easy')
    a=p.parse_args();case=PipelineTests();case.setUp()
    x=np.loadtxt(a.easy,usecols=range(19))*1e-3 # Prior Enobio replay format: nV -> µV.
    out=case.run_core(case.c,x);n=len(case.c['channels'])
    summary={'test':'saved_real_EEG_native_replay','config_id':config_id(case.c),'input_samples':len(x),
             'input_seconds':len(x)/case.c['sample_rate_hz'],'feature_windows':len(out),'amplitude_valid_windows':int(out[:,1].sum()),
             'ica_ranks':sorted(set(map(int,out[:,2]))),'removed_components':np.where(out[0,3:3+n]>0)[0].tolist(),
             'first_window_source_offset_seconds':float(out[0,0]-1),'hardware':False,
             'note':'Runs firmware DSP on Linux; not ESP32 timing or physiological validation.'}
    (ROOT/'artifacts/native-replay-summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary))


if __name__=='__main__':main()
