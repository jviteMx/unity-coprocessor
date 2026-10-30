"""Flash the packaged images after checking their hashes; no rebuild required."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from configure import ROOT, config_id


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--port',required=True)
    p.add_argument('--artifacts',type=Path,default=ROOT/'artifacts')
    p.add_argument('--esptool',type=Path,help='Path to esptool.py (e.g. PlatformIO packages/tool-esptoolpy/esptool.py)')
    p.add_argument('--dry-run',action='store_true')
    a=p.parse_args();folder=a.artifacts.resolve()
    manifest=json.loads((folder/'manifest.json').read_text())
    if manifest['config_id']!=config_id(json.loads((folder/'config.json').read_text())):
        raise ValueError('Packaged config hash mismatch')
    images=[]
    for image in sorted(manifest['images'],key=lambda x:int(x['offset'],16)):
        path=(folder/image['file']).resolve()
        if path.parent!=folder or hashlib.sha256(path.read_bytes()).hexdigest()!=image['sha256']:
            raise ValueError(f'Image hash/path mismatch: {image["file"]}')
        images.extend([image['offset'],str(path)])
    cmd=[sys.executable,str(a.esptool)] if a.esptool else [sys.executable,'-m','esptool']
    cmd+=['--chip',manifest['chip'],'--port',a.port,'--baud','460800','--before','default_reset','--after','hard_reset',
          'write_flash','-z','--flash_mode',manifest['flash_mode'],'--flash_freq',manifest['flash_freq'],'--flash_size',manifest['flash_size']]+images
    print('Verified image hashes; config_id='+manifest['config_id'])
    if a.dry_run:print(json.dumps(cmd,indent=2))
    else:subprocess.run(cmd,check=True)


if __name__=='__main__':main()
