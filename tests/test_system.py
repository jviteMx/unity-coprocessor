"""Numerical and transport regressions using the actual portable firmware DSP core."""
import copy
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
import numpy as np
from scipy import signal
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'tools'),str(ROOT/'host')]
from configure import load, validate, generate, expression, config_id
from bridge import packet, data_packet, mapping, Device


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.c=load(ROOT/'config/eeg19-existing.json')

    def run_core(self,c,x):
        with tempfile.TemporaryDirectory() as name:
            d=Path(name)
            for file in ('pipeline.h','wire.h'):
                shutil.copy(ROOT/'firmware/include'/file,d/file)
            generate(validate(c),d/'generated_config.h')
            subprocess.run(['g++','-std=c++17','-O2','-Wall','-Wextra','-Werror','-fsanitize=undefined','-I',str(d),str(ROOT/'tests/native_runner.cpp'),'-o',str(d/'runner')],check=True)
            dtype=np.dtype([('timestamp','<f8'),('samples','<f4',(x.shape[1],))])
            data=np.empty(len(x),dtype=dtype);data['timestamp']=1+np.arange(len(x))/c['sample_rate_hz'];data['samples']=x
            p=subprocess.run([str(d/'runner')],input=data.tobytes(),capture_output=True,check=True)
            return np.loadtxt(p.stdout.decode().splitlines(),ndmin=2)

    def source(self,c,seconds=40):
        fs=c['sample_rate_hz'];t=np.arange(int(seconds*fs))/fs
        rng=np.random.default_rng(24)
        return np.stack([10*np.sin(2*np.pi*6*t+j*.2)+20*np.sin(2*np.pi*20*t+j*.3)+5*np.sin(2*np.pi*10*t+j*.1)+rng.normal(0,2,len(t)) for j in range(len(c['channels']))],axis=1)

    def test_welch_and_filter_match_scipy(self):
        c=self.c;c['ica']['mode']='disabled';x=self.source(c,9);out=self.run_core(c,x)
        f=c['filter'];fs=c['sample_rate_hz'];w=c['welch'];n=len(c['channels'])
        sos=signal.butter(f['order'],[f['low_hz'],f['high_hz']],fs=fs,btype='bandpass',output='sos')
        sos=np.vstack([sos,signal.tf2sos(*signal.iirnotch(f['notch_hz'],f['notch_q'],fs))])
        filtered=signal.sosfilt(sos,x,axis=0)
        for row in out:
            end=round((row[0]-1)*fs)+1
            freq,psd=signal.welch(filtered[end-round(w['window_seconds']*fs):end],fs=fs,window='hann',nperseg=w['segment_samples'],noverlap=w['overlap_samples'],detrend='constant',axis=0)
            powers=row[3+n:3+n+n*len(c['bands'])].reshape(n,-1)
            used={c['channels'].index(ch) for r in c['ratios'] for ch in r['channels']}
            for b,(lo,hi) in enumerate(c['bands'].values()):
                expected=psd[(freq>=lo)&(freq<hi)].sum(axis=0)*(freq[1]-freq[0])
                np.testing.assert_allclose(powers[list(used),b],expected[list(used)],rtol=0.002,atol=0.002)
            vals=row[3+n+n*len(c['bands']):]
            for j,ch in enumerate(c['ratios'][0]['channels']):
                p=powers[c['channels'].index(ch)]
                self.assertAlmostEqual(vals[j],p[2]/p[0],places=5)
        self.assertGreater(len(out),3)
        np.testing.assert_allclose(np.diff(out[:,0]),w['hop_seconds'],atol=1e-8)

    def test_ica_warmup_and_rank_for_all_montages(self):
        for filename in ('eeg19-existing.json','eeg20.json','eeg32.json'):
            c=load(ROOT/'config'/filename);x=self.source(c)
            # Add independent non-Gaussian sources to make calibration full rank.
            x+=np.random.default_rng(99).laplace(0,3,x.shape)
            out=self.run_core(c,x);n=len(c['channels'])
            self.assertTrue(np.isfinite(out).all())
            self.assertEqual(out[0,2],n-(c['reference']['mode']!='as_acquired'))
            # No windows from filter/ICA calibration or partially trained transforms.
            first=(c['filter']['settle_seconds']+c['ica']['calibration_seconds']+
                   np.ceil(c['ica']['training_seconds']*c['sample_rate_hz']/16)*16/c['sample_rate_hz']+c['welch']['window_seconds'])
            self.assertAlmostEqual(out[0,0],1+first-1/c['sample_rate_hz'],places=6)

    def test_flat_data_fails_ica(self):
        with self.assertRaises(subprocess.CalledProcessError):
            self.run_core(self.c,np.zeros((20000,len(self.c['channels']))))

    def test_frontal_projection_attenuates_synthetic_source(self):
        c=self.c;n=len(c['channels']);fs=c['sample_rate_hz'];t=np.arange(40*fs)/fs
        x=np.random.default_rng(73).laplace(0,2,(len(t),n))
        for ch in ('Fp1','Fp2'):
            x[:,c['channels'].index(ch)]+=40*np.sin(2*np.pi*6*t)*(1+0.5*np.sin(2*np.pi*0.7*t))
        cleaned=self.run_core(c,x)
        baseline=copy.deepcopy(c);baseline['ica']['removal']='none';original=self.run_core(baseline,x)
        self.assertTrue((cleaned[:,3:3+n].sum(axis=1)==1).all())
        index=3+n+c['channels'].index('Fp1')*len(c['bands'])
        self.assertLess(cleaned[:,index].mean(),original[:,index].mean()*0.2)

    def test_quality_gate_and_zero_denominator(self):
        c=self.c;c['ica']['mode']='disabled';c['quality']['max_abs_uv']=2
        out=self.run_core(c,self.source(c,7));self.assertTrue((out[:,1]==0).all())
        out=self.run_core(c,np.zeros((3500,len(c['channels']))))
        self.assertTrue(np.isnan(out[:,-5:]).all())

    def test_expressions_and_config_rejections(self):
        for source in ('beta/theta','beta/(alpha+theta)','(beta-alpha)*2 / theta','-alpha+beta'):
            self.assertTrue(expression(source,self.c['bands']))
        for source in ('__import__("os")','beta**2','beta//theta','beta[0]','tetha/alpha'):
            with self.assertRaises(ValueError): expression(source,self.c['bands'])
        for path,value in [(('welch','overlap_samples'),512),(('filter','high_hz'),300),(('ratios',0,'aggregate'),'median'),(('sample_rate_hz',),501)]:
            c=copy.deepcopy(self.c);obj=c
            for key in path[:-1]:obj=obj[key]
            obj[path[-1]]=value
            with self.assertRaises(ValueError):validate(c)
        c=copy.deepcopy(self.c);c['ratios'][0].pop('aggregate');validate(c)
        self.assertNotEqual(config_id(c),config_id(self.c))


class TransportTests(unittest.TestCase):
    def test_actual_firmware_handshake_features_gap_and_corruption(self):
        c=load(ROOT/'config/eeg19-existing.json');c['ica']['mode']='disabled'
        with tempfile.TemporaryDirectory() as name:
            d=Path(name)
            for file in ('pipeline.h','wire.h'):
                shutil.copy(ROOT/'firmware/include'/file,d/file)
            generate(c,d/'generated_config.h')
            subprocess.run(['g++','-std=c++17','-O2','-fsanitize=undefined','-I',str(ROOT/'tests/shims'),'-I',str(d),str(ROOT/'firmware/src/main.cpp'),str(ROOT/'tests/native_firmware.cpp'),'-o',str(d/'firmware')],check=True)
            def run(payload):
                p=subprocess.run([str(d/'firmware')],input=payload,capture_output=True,check=True)
                return [json.loads(line) for line in p.stdout.splitlines()]
            hello=packet(2,0,config_id(c).encode())
            bad_hello=run(packet(2,0,b'0'*64))
            self.assertEqual(bad_hello[-1]['message'],'config_mismatch')
            n=len(c['channels']);fs=c['sample_rate_hz'];frames=[]
            for start in range(0,4000,32):
                ts=1+np.arange(start,start+32)/fs
                rows=np.array([[10*np.sin(2*np.pi*6*t)+20*np.sin(2*np.pi*20*t)]*n for t in ts])
                frames.append(data_packet(start//32,rows,ts,n))
            out=run(hello+b''.join(frames))
            features=[m for m in out if m['type']=='features']
            self.assertGreater(len(features),1)
            first=features[0]
            self.assertTrue(first['valid']);self.assertEqual(first['values'][0]['channel'],'mean')
            self.assertAlmostEqual(first['values'][0]['value'],4,delta=.1)
            self.assertEqual([m['seq'] for m in out if m['type']=='ack'],list(range(len(frames))))
            # No feature window may span the missing time interval.
            gap=data_packet(len(frames),[[0]*n]*32,20+np.arange(32)/fs,n)
            out=run(hello+b''.join(frames)+gap)
            self.assertEqual(out[-1]['state'],'filter_settling')
            self.assertTrue(any(m.get('state')=='timestamp_gap_recalibrating' for m in out))
            corrupt=bytearray(frames[0]);corrupt[20]^=1
            out=run(hello+corrupt+frames[1])
            self.assertEqual([m['message'] for m in out if m['type']=='error'],['packet_crc_or_header','handshake_required'])
            out=run(hello+frames[0]+frames[0])
            self.assertEqual(out[-1]['message'],'sequence_gap_or_duplicate')

    def test_mapping_units_and_shape(self):
        c=load(ROOT/'config/eeg19-existing.json');labels=c['channels'][::-1]+['EXT'];units=['V']*len(labels)
        self.assertEqual(mapping(c,labels,units,500),list(range(18,-1,-1)))
        for bad_labels,bad_units,rate in [(labels,units,250),(labels,['uV']*len(labels),500),(['']*19,['']*19,500)]:
            with self.assertRaises(ValueError):mapping(c,bad_labels,bad_units,rate)
        c['lsl']['allow_missing_labels']=True
        self.assertEqual(mapping(c,['']*19,['']*19,500),list(range(19)))

    def test_wire_crc_and_header(self):
        p=data_packet(7,[[1,2],[3,4]],[1.0,1.002],2)
        self.assertEqual(struct.unpack('<2sBBHI',p[:10]),(b'UC',1,1,36,7))
        self.assertEqual(zlib.crc32(p[:-4]),struct.unpack('<I',p[-4:])[0])
        with self.assertRaises(ValueError):data_packet(0,[[float('nan')]], [1],1)
        # Feed valid, corrupted, then valid bytes through the exact firmware parser.
        with tempfile.TemporaryDirectory() as name:
            d=Path(name);corrupt=bytearray(p);corrupt[20]^=1
            stream=b'noise'+p+corrupt+p
            source='#include "wire.h"\n#include <cstdio>\nint main(){wire::Parser<500> p;int c;while((c=getchar())!=EOF){int r=p.feed(c);if(r)printf("%d %u\\n",r,p.sequence());}}'
            (d/'test.cpp').write_text(source)
            subprocess.run(['g++','-std=c++17','-fsanitize=undefined','-I',str(ROOT/'firmware/include'),str(d/'test.cpp'),'-o',str(d/'test')],check=True)
            output=subprocess.run([str(d/'test')],input=stream,capture_output=True,check=True).stdout
            self.assertEqual(output,b'1 7\n-1 7\n1 7\n')

    def test_partial_serial_reads_and_errors(self):
        class Serial:
            def __init__(self,data):self.data=bytearray(data);self.written=b''
            @property
            def in_waiting(self):return min(3,len(self.data))
            def read(self,n):result=self.data[:n];del self.data[:n];return result
            def write(self,b):self.written+=b
        events=[];ser=Serial(b'boot\n{"type":"features","values":[]}\n{"type":"ack","seq":3}\n')
        self.assertEqual(Device(ser,events.append).wait('ack',3)['seq'],3)
        self.assertEqual(len(events),1)
        with self.assertRaises(RuntimeError):Device(Serial(b'{"type":"error","message":"config_mismatch"}\n'),events.append).wait('hello')


if __name__=='__main__':unittest.main(verbosity=2)
