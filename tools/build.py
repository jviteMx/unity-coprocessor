"""Generate a config, build the MatrixPortal firmware, and optionally upload it."""
import argparse
from pathlib import Path
import subprocess
import sys
from configure import ROOT, load, generate


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config',type=Path,default=ROOT/'config/eeg19-existing.json')
    p.add_argument('--upload-port',help='Optional serial port, e.g. /dev/ttyACM0 or COM5')
    a=p.parse_args();c=load(a.config)
    generate(c,ROOT/'firmware/include/generated_config.h')
    cmd=[sys.executable,'-m','platformio','run','-d',str(ROOT/'firmware'),'-e','matrixportal_s3']
    if a.upload_port:cmd+=['-t','upload','--upload-port',a.upload_port]
    subprocess.run(cmd,check=True)


if __name__=='__main__':main()
