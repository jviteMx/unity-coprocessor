"""Validate a declarative montage and generate the firmware's build-time config."""
from __future__ import annotations
import argparse
import ast
import hashlib
import json
import math
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def expression(source, bands):
    """Compile arithmetic to bounded postfix tokens; never evaluate user Python."""
    if not isinstance(source, str) or len(source) > 256:
        raise ValueError('Expected an arithmetic expression of at most 256 characters')
    try:
        root = ast.parse(source, mode='eval').body
    except SyntaxError as e:
        raise ValueError('Invalid expression') from e
    tokens = []
    def walk(node):
        if isinstance(node, ast.Name) and node.id in bands:
            tokens.append((0, list(bands).index(node.id)))
        elif isinstance(node, ast.Constant) and not isinstance(node.value, bool) and isinstance(node.value, (int, float)):
            tokens.append((1, number(node.value, 0, 1e6)))
        elif isinstance(node, ast.BinOp) and type(node.op) in (ast.Add, ast.Sub, ast.Mult, ast.Div):
            walk(node.left); walk(node.right)
            tokens.append(((ast.Add, ast.Sub, ast.Mult, ast.Div).index(type(node.op)) + 2, 0))
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            tokens.append((1, 0)); walk(node.operand); tokens.append((3, 0))
        else:
            raise ValueError('Expressions support band names, numeric constants, + - * / and parentheses only')
    walk(root)
    if len(tokens) > 63:
        raise ValueError('Expression exceeds 63 tokens')
    return tokens


def config_id(c):
    return hashlib.sha256(json.dumps(c, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def keys(obj, allowed, required=()):
    if not isinstance(obj, dict) or set(obj) - set(allowed) or set(required) - set(obj):
        raise ValueError(f"Invalid keys: {obj}; allowed={allowed}, required={required}")


def number(x, low, high):
    if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) or not low <= x <= high:
        raise ValueError(f"Expected finite number in [{low}, {high}], got {x}")
    return x


def integer(x, low, high):
    number(x, low, high)
    if int(x) != x:
        raise ValueError(f"Expected integer, got {x}")
    return int(x)


def samples(seconds, fs):
    value = number(seconds, 0.001, 600) * fs
    if abs(value - round(value)) > 1e-6:
        raise ValueError("Durations must resolve to an integer number of samples")
    return round(value)


def validate(c):
    keys(c, ['schema_version','sample_rate_hz','channels','input_unit','reference','filter','ica','welch','bands','ratios','quality','lsl','unity'],
         ['schema_version','sample_rate_hz','channels','input_unit','reference','filter','ica','welch','bands','ratios','quality','lsl','unity'])
    if c['schema_version'] != 1:
        raise ValueError('Unsupported schema_version')
    fs = number(c['sample_rate_hz'], 128, 2000)
    ch = c['channels']
    if not isinstance(ch, list) or not 2 <= len(ch) <= 32 or any(not isinstance(x, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,23}', x) for x in ch):
        raise ValueError('Expected 2–32 named channels')
    if len(set(x.upper() for x in ch)) != len(ch):
        raise ValueError('Channel labels must be unique (case insensitive)')
    def labels(xs):
        if not isinstance(xs, list) or any(x not in ch for x in xs) or len(set(xs)) != len(xs):
            raise ValueError(f'Unknown/duplicate channel in {xs}')
    if c['input_unit'] not in ('V', 'mV', 'uV'):
        raise ValueError('input_unit must be V, mV, or uV')
    ref = c['reference']
    keys(ref, ['mode','channel'], ['mode'])
    if ref['mode'] not in ('as_acquired','channel','average'):
        raise ValueError('Invalid reference mode')
    if ref['mode'] == 'channel':
        labels([ref.get('channel')])
    elif 'channel' in ref:
        raise ValueError('reference.channel only applies to channel mode')
    f = c['filter']
    keys(f, ['low_hz','high_hz','order','notch_hz','notch_q','settle_seconds'], ['low_hz','high_hz','order','settle_seconds'])
    number(f['low_hz'], 0.1, fs / 2 - 1)
    number(f['high_hz'], f['low_hz'] + 0.1, fs / 2 - 0.1)
    integer(f['order'], 1, 8)
    samples(f['settle_seconds'], fs)
    if f.get('notch_hz') is not None:
        number(f['notch_hz'], 1, fs / 2 - 0.1)
        number(f.get('notch_q', 30), 1, 100)
    ic = c['ica']
    keys(ic, ['mode','calibration_seconds','training_seconds','learning_rate','rank_tolerance','removal','frontal_channels','frontal_fraction_threshold','remove_components'],
         ['mode','calibration_seconds','training_seconds','learning_rate','rank_tolerance','removal','remove_components'])
    if ic['mode'] not in ('calibrate_then_freeze','disabled'):
        raise ValueError('Invalid ICA mode')
    if samples(ic['calibration_seconds'], fs) < len(ch) * 4 or samples(ic['training_seconds'], fs) < 16:
        raise ValueError('ICA calibration/training too short')
    number(ic['learning_rate'], 0.00001, 0.02)
    number(ic['rank_tolerance'], 0.000001, 0.1)
    if ic['removal'] not in ('none','components','frontal_projection'):
        raise ValueError('Invalid ICA removal policy')
    labels(ic.get('frontal_channels', []))
    if ic['removal'] == 'frontal_projection':
        if not ic.get('frontal_channels') or len(ic['frontal_channels']) >= len(ch):
            raise ValueError('Specify a proper subset of frontal channels')
        number(ic.get('frontal_fraction_threshold'), 0.01, 1)
    if not isinstance(ic['remove_components'], list) or len(set(ic['remove_components'])) != len(ic['remove_components']):
        raise ValueError('Invalid component list')
    for i in ic['remove_components']:
        integer(i, 0, len(ch) - 1)
    if ic['removal'] != 'components' and ic['remove_components']:
        raise ValueError('remove_components requires removal=components')
    w = c['welch']
    keys(w, ['window_seconds','segment_samples','overlap_samples','hop_seconds'], ['window_seconds','segment_samples','overlap_samples','hop_seconds'])
    win = samples(w['window_seconds'], fs)
    seg = integer(w['segment_samples'], 32, 4096)
    if seg & (seg - 1) or win < seg or win > 8192:
        raise ValueError('segment_samples must be a power of two <= window; window <=8192 samples')
    integer(w['overlap_samples'], 0, seg - 1)
    if samples(w['hop_seconds'], fs) > win:
        raise ValueError('hop_seconds must not exceed window_seconds')
    bands = c['bands']
    if not isinstance(bands, dict) or not 1 <= len(bands) <= 16:
        raise ValueError('Expected 1–16 frequency bands')
    for name, bounds in bands.items():
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,23}', name) or not isinstance(bounds, list) or len(bounds) != 2:
            raise ValueError('Invalid band')
        lo, hi = bounds
        number(lo, f['low_hz'], f['high_hz'])
        number(hi, lo + 0.01, f['high_hz'])
        if not any(lo <= k * fs / seg < hi for k in range(seg // 2 + 1)):
            raise ValueError(f'Band {name} contains no FFT bins')
    ratios = c['ratios']
    if not isinstance(ratios, list) or not 1 <= len(ratios) <= 16:
        raise ValueError('Expected 1–16 ratios')
    names = set()
    for r in ratios:
        keys(r, ['name','channels','expression','aggregate'], ['name','channels','expression'])
        if not isinstance(r['name'], str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,23}', r['name']) or r['name'] in names:
            raise ValueError('Invalid/duplicate ratio name')
        names.add(r['name'])
        labels(r['channels'])
        if not r['channels'] or (ref['mode'] == 'channel' and ref['channel'] in r['channels']):
            raise ValueError('Ratio channels cannot be empty or include the reference channel')
        if r.get('aggregate', 'none') not in ('none','mean'):
            raise ValueError('aggregate must be none or mean')
        expression(r['expression'], bands)
    keys(c['quality'], ['max_abs_uv','min_denominator_uv2'], ['max_abs_uv','min_denominator_uv2'])
    number(c['quality']['max_abs_uv'], 1, 1e6)
    number(c['quality']['min_denominator_uv2'], 1e-12, 1e3)
    keys(c['lsl'], ['stream_name','allow_missing_labels'], ['stream_name','allow_missing_labels'])
    if not isinstance(c['lsl']['stream_name'], str) or not c['lsl']['stream_name'] or not isinstance(c['lsl']['allow_missing_labels'], bool):
        raise ValueError('Invalid LSL selection')
    keys(c['unity'], ['host','port'], ['host','port'])
    if not isinstance(c['unity']['host'], str) or not c['unity']['host']:
        raise ValueError('Invalid Unity host')
    integer(c['unity']['port'], 1, 65535)
    return c


def load(path):
    return validate(json.loads(Path(path).read_text()))


def generate(c, destination):
    from scipy.signal import butter, iirnotch, tf2sos
    fs = c['sample_rate_hz']; f = c['filter']; ic = c['ica']; w = c['welch']; ch = c['channels']
    sos = list(butter(f['order'], [f['low_hz'], f['high_hz']], btype='bandpass', fs=fs, output='sos'))
    if f.get('notch_hz') is not None:
        sos.extend(tf2sos(*iirnotch(f['notch_hz'], f.get('notch_q', 30), fs)))
    def fl(x):
        s = format(float(x), '.10g')
        return s + ('f' if '.' in s or 'e' in s else '.0f')
    def arr(xs): return '{' + ','.join(map(str, xs)) + '}'
    band_names = list(c['bands'])
    rows = []
    for r in c['ratios']:
        tokens = expression(r['expression'], c['bands'])
        rows.append('{' + ','.join([json.dumps(r['name']), str(len(r['channels'])), arr([ch.index(x) for x in r['channels']]),
                                   str(len(tokens)), arr([arr([str(op), fl(value)]) for op, value in tokens]),
                                   str(r.get('aggregate', 'none') == 'mean').lower()]) + '}')
    ref = c['reference']
    out = f'''// Generated by tools/configure.py. Edit the JSON and regenerate; do not edit here.
#pragma once
namespace cfg {{
constexpr char ID[] = "{config_id(c)}";
constexpr int N={len(ch)}, BANDS={len(band_names)}, RATIOS={len(rows)};
constexpr float FS={fl(fs)};
constexpr int WINDOW={samples(w['window_seconds'], fs)}, FFT={w['segment_samples']}, OVERLAP={w['overlap_samples']}, HOP={samples(w['hop_seconds'], fs)};
constexpr int SOS_COUNT={len(sos)}, SETTLE={samples(f['settle_seconds'], fs)}, CAL={samples(ic['calibration_seconds'], fs)}, TRAIN={samples(ic['training_seconds'], fs)};
constexpr bool ICA_ENABLED={str(ic['mode'] != 'disabled').lower()};
constexpr int REF_MODE={['as_acquired','channel','average'].index(ref['mode'])}, REF_CH={ch.index(ref['channel']) if ref['mode']=='channel' else -1};
constexpr int REMOVE_MODE={['none','components','frontal_projection'].index(ic['removal'])};
constexpr float MU={fl(ic['learning_rate'])}, RANK_TOL={fl(ic['rank_tolerance'])}, FRONTAL_THRESHOLD={fl(ic.get('frontal_fraction_threshold', 1))};
constexpr float MAX_UV={fl(c['quality']['max_abs_uv'])}, MIN_DEN={fl(c['quality']['min_denominator_uv2'])};
constexpr const char* CHANNELS[N]={arr([json.dumps(x) for x in ch])};
constexpr const char* BAND_NAMES[BANDS]={arr([json.dumps(x) for x in band_names])};
constexpr float BAND_EDGES[BANDS][2]={arr([arr([fl(v) for v in bounds]) for bounds in c['bands'].values()])};
constexpr bool FRONTAL[N]={arr([str(x in ic.get('frontal_channels', [])).lower() for x in ch])};
constexpr bool REMOVE[N]={arr([str(i in ic['remove_components']).lower() for i in range(len(ch))])};
constexpr float SOS[SOS_COUNT][5]={arr([arr([fl(row[i]/row[3]) for i in [0,1,2,4,5]]) for row in sos])};
struct Token {{ int op; float value; }};
struct Ratio {{ const char* name; int count; int channels[N]; int token_count; Token tokens[63]; bool mean; }};
constexpr Ratio FEATURES[RATIOS]={arr(rows)};
}} // namespace cfg
'''
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(out)
    destination.with_suffix('.json').write_text(json.dumps(c, indent=2) + '\n')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('config', type=Path)
    p.add_argument('--output', type=Path, default=ROOT / 'firmware/include/generated_config.h')
    a = p.parse_args()
    c = load(a.config)
    generate(c, a.output)
    print(f'Generated {a.output}; config_id={config_id(c)}')


if __name__ == '__main__':
    main()
