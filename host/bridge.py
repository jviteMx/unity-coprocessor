"""LSL inlet -> framed USB -> firmware JSON -> Unity UDP. No host EEG processing."""
from __future__ import annotations
import argparse
import json
import math
from pathlib import Path
import socket
import struct
import sys
import time
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from configure import load, config_id

HEADER = struct.Struct('<2sBBHI')


def packet(kind, sequence, payload):
    data = HEADER.pack(b'UC', kind, 1, len(payload), sequence) + payload
    return data + struct.pack('<I', zlib.crc32(data))


def data_packet(sequence, samples, timestamps, nch):
    if not 1 <= len(samples) <= 32 or len(samples) != len(timestamps):
        raise ValueError('Expected 1–32 timestamped samples')
    payload = bytearray(struct.pack('<HH', nch, len(samples)))
    row = struct.Struct('<d' + 'f' * nch)
    for sample, ts in zip(samples, timestamps):
        if len(sample) != nch or not math.isfinite(ts) or not all(math.isfinite(x) and abs(x) <= 1e7 for x in sample):
            raise ValueError('Malformed or nonfinite sample')
        payload.extend(row.pack(ts, *sample))
    return packet(1, sequence, payload)


def mapping(config, labels, units, rate):
    if abs(rate - config['sample_rate_hz']) > 0.01:
        raise ValueError(f'LSL rate {rate} does not match configured {config["sample_rate_hz"]}; no implicit resampling')
    expected = [x.upper() for x in config['channels']]
    if not labels or any(not x for x in labels):
        if not config['lsl']['allow_missing_labels'] or len(labels) != len(expected):
            raise ValueError('LSL channel labels missing; explicit positional opt-in and exact shape required')
        indices = list(range(len(expected)))
    else:
        normalized = [x.upper() for x in labels]
        if len(set(normalized)) != len(normalized):
            raise ValueError('Duplicate LSL channel labels')
        if any(x not in normalized for x in expected):
            raise ValueError(f'Missing EEG labels; expected={expected}, got={labels}')
        indices = [normalized.index(x) for x in expected]
    aliases = {'v':'V', 'volt':'V', 'volts':'V', 'mv':'mV', 'millivolts':'mV',
               'uv':'uV', 'µv':'uV', 'μv':'uV', 'microvolt':'uV', 'microvolts':'uV'}
    for i in indices:
        if units[i] and aliases.get(units[i].lower()) != config['input_unit']:
            raise ValueError(f'Unit mismatch for {labels[i]}: LSL={units[i]}, config={config["input_unit"]}')
    return indices


class Device:
    def __init__(self, serial_port, emit):
        self.serial = serial_port
        self.emit = emit
        self.pending = bytearray()

    def wait(self, kind, seq=None, timeout=20):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            chunk = self.serial.read(self.serial.in_waiting or 1)
            if chunk:
                self.pending.extend(chunk)
            if len(self.pending) > 65536:
                raise RuntimeError('Device response exceeds 64 KiB')
            while b'\n' in self.pending:
                line, _, rest = self.pending.partition(b'\n'); self.pending = bytearray(rest)
                try:
                    msg = json.loads(line)
                except (ValueError, UnicodeDecodeError):
                    print(f'Ignoring boot output: {line[:120]!r}', file=sys.stderr); continue
                if msg.get('type') == 'error':
                    raise RuntimeError(f'Firmware error: {msg}')
                if msg.get('type') in ('features', 'status'):
                    self.emit(msg)
                if msg.get('type') == kind:
                    if seq is not None and msg.get('seq') != seq:
                        raise RuntimeError('ACK sequence mismatch')
                    return msg
        raise TimeoutError(f'No {kind} response from ESP32; check USB port, firmware and reset state')

    def hello(self, identity):
        self.serial.write(packet(2, 0, identity.encode('ascii')))
        response = self.wait('hello')
        if response['config_id'] != identity:
            raise RuntimeError('Firmware/host configuration mismatch')
        return response


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config', type=Path, default=ROOT / 'config/eeg19-existing.json')
    p.add_argument('--serial-port', required=True)
    p.add_argument('--baud', type=int, default=921600)
    p.add_argument('--no-udp', action='store_true')
    p.add_argument('--max-samples', type=int, default=0)
    a = p.parse_args()
    import serial
    from pylsl import StreamInlet, resolve_byprop, local_clock
    c = load(a.config)
    streams = resolve_byprop('name', c['lsl']['stream_name'], timeout=10)
    if len(streams) != 1:
        raise RuntimeError(f'Expected exactly one named LSL stream, found {len(streams)}')
    inlet = StreamInlet(streams[0], max_buflen=2, processing_flags=0, recover=False)
    info = inlet.info(timeout=10)
    labels, units = [], []
    channel = info.desc().child('channels').child('channel')
    for _ in range(info.channel_count()):
        labels.append(channel.child_value('label'))
        units.append(channel.child_value('unit'))
        channel = channel.next_sibling()
    indices = mapping(c, labels, units, info.nominal_srate())
    if any(not units[i] for i in indices):
        print(f'LSL units absent: using explicit input_unit={c["input_unit"]}', file=sys.stderr)
    scale = {'V': 1e6, 'mV': 1e3, 'uV': 1}[c['input_unit']]
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    destination = (c['unity']['host'], c['unity']['port'])
    def emit(msg):
        data = json.dumps(msg, separators=(',', ':'), allow_nan=False)
        print(data, flush=True)
        if not a.no_udp:
            encoded = data.encode()
            if len(encoded) > 65000:
                raise RuntimeError('Unity datagram too large; reduce feature count')
            udp.sendto(encoded, destination)
    try:
        with serial.Serial(a.serial_port, a.baud, timeout=0.1, write_timeout=5) as ser:
            time.sleep(2)
            ser.reset_input_buffer()
            device = Device(ser, emit)
            hello = device.hello(config_id(c));emit(dict(hello, type='configuration', config=c))
            # Open only after handshake so no EEG backlog accumulates during board startup.
            inlet.open_stream(timeout=10)
            correction = inlet.time_correction(timeout=10)
            inlet.flush()
            count = seq = 0; previous_state = None; last_report = time.monotonic()
            while not a.max_samples or count < a.max_samples:
                limit = min(32, a.max_samples-count) if a.max_samples else 32
                rows, timestamps = inlet.pull_chunk(timeout=0.1, max_samples=limit)
                if not rows:
                    continue
                if local_clock() - (timestamps[-1] + correction) > 1:
                    raise RuntimeError('EEG backlog exceeds one second; device cannot keep up or timestamps are stale')
                selected = [[row[i]*scale for i in indices] for row in rows]
                ser.write(data_packet(seq, selected, timestamps, len(indices)))
                ack = device.wait('ack', seq)
                if ack['state'] != previous_state:
                    previous_state = ack['state'];emit(dict(ack, type='status'))
                seq = (seq+1) & 0xffffffff;count += len(rows)
                if time.monotonic()-last_report > 5:
                    correction = inlet.time_correction(timeout=0.0)
                    print(f'Forwarded {count} samples; state={ack["state"]}; batch_compute_us={ack["batch_compute_us"]}', file=sys.stderr)
                    last_report = time.monotonic()
    finally:
        inlet.close_stream(); udp.close()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print(f'ERROR: {e}', file=sys.stderr);sys.exit(1)
