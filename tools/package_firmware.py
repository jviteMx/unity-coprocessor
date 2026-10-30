"""PlatformIO post-build hook: export images and a reproducible flash manifest."""
Import('env')
import hashlib
import json
from pathlib import Path
import shutil


def package(source, target, env):
    project = Path(env.subst('$PROJECT_DIR')).parent
    out = project / 'artifacts'
    out.mkdir(exist_ok=True)
    config = json.loads((project/'firmware/include/generated_config.json').read_text())
    identity = hashlib.sha256(json.dumps(config, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    images=[]
    for offset, path in env.get('FLASH_EXTRA_IMAGES', []):
        src=Path(env.subst(path))
        name = 'bootloader.bin' if int(offset,16)==0 else src.name
        shutil.copyfile(src,out/name)
        images.append({'offset':offset,'file':name,'sha256':hashlib.sha256(src.read_bytes()).hexdigest()})
    app=Path(env.subst('$BUILD_DIR/${PROGNAME}.bin'))
    shutil.copyfile(app,out/'firmware.bin')
    shutil.copyfile(app.with_suffix('.elf'),out/'firmware.elf')
    images.append({'offset':env.subst('$ESP32_APP_OFFSET'),'file':'firmware.bin','sha256':hashlib.sha256(app.read_bytes()).hexdigest()})
    (out/'config.json').write_text(json.dumps(config,indent=2)+'\n')
    sources={str(p.relative_to(project)):hashlib.sha256(p.read_bytes()).hexdigest() for p in (project/'firmware/include/pipeline.h',project/'firmware/include/wire.h',project/'firmware/src/main.cpp',project/'firmware/include/generated_config.h',project/'firmware/platformio.ini')}
    manifest={'board':'adafruit_matrixportal_esp32s3','chip':'esp32s3','platform':'espressif32@6.9.0','config_id':identity,
              'flash_mode':env.subst('${__get_board_flash_mode(__env__)}'),'flash_freq':env.subst('${__get_board_f_image(__env__)}'),
              'flash_size':'8MB','images':images,'sources':sources}
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(f'Packaged firmware: {out}; config_id={identity}')


env.AddPostAction('$BUILD_DIR/${PROGNAME}.bin',package)
