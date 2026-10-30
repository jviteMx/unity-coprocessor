#pragma once
#include "generated_config.h"
#include <cmath>
#include <cstdint>
#include <cstring>
#include <algorithm>
#include <new>

namespace copro {
using namespace cfg;
constexpr float kPi = 3.14159265358979323846f;

// All large working arrays belong to a heap-allocated Engine, never the Arduino task stack.
class Engine {
    float* ring;
    float z1[N][SOS_COUNT]{}, z2[N][SOS_COUNT]{};
    float mean[N]{}, cov[N][N]{}, eigvec[N][N]{}, eigen[N]{};
    float white[N][N]{}, rotate[N][N]{}, mixing[N][N]{}, unmix[N][N]{};
    float grad[N][N]{}, scratch[N][N]{}, block[N][16]{};
    float re[FFT]{}, im[FFT]{}, hann[FFT]{};
    float power[N][BANDS]{};
    bool rejected[WINDOW]{};
    int written=0, filled=0, hop=0, rejected_count=0, block_count=0;
    uint32_t settled=0, calibrated=0, trained=0;
    float hann_energy=0;
    bool failed=false, frozen=false;

    float filter(int ch, float x) {
        for (int s=0;s<SOS_COUNT;s++) {
            const float* b=SOS[s];
            float y=b[0]*x+z1[ch][s];
            z1[ch][s]=b[1]*x-b[3]*y+z2[ch][s];
            z2[ch][s]=b[2]*x-b[4]*y; x=y;
        }
        return x;
    }

    bool whiten() {
        // Covariance is accumulated with Welford; Jacobi eigenvectors are columns.
        for(int i=0;i<N;i++) for(int j=0;j<N;j++) {
            cov[i][j]/=float(CAL-1); eigvec[i][j]=(i==j);
        }
        for(int sweep=0;sweep<60;sweep++) {
            float off=0, diag=0;
            for(int p=0;p<N;p++) { diag+=std::fabs(cov[p][p]); for(int q=p+1;q<N;q++) off+=std::fabs(cov[p][q]); }
            if(off < 1e-6f*std::max(diag,1e-12f)) break;
            for(int p=0;p<N;p++) for(int q=p+1;q<N;q++) {
                if(std::fabs(cov[p][q])<1e-12f) continue;
                float a=0.5f*std::atan2(2*cov[p][q],cov[q][q]-cov[p][p]);
                float c=std::cos(a), s=std::sin(a);
                for(int k=0;k<N;k++) { float x=cov[k][p],y=cov[k][q]; cov[k][p]=c*x-s*y; cov[k][q]=s*x+c*y; }
                for(int k=0;k<N;k++) { float x=cov[p][k],y=cov[q][k]; cov[p][k]=c*x-s*y; cov[q][k]=s*x+c*y; }
                for(int k=0;k<N;k++) { float x=eigvec[k][p],y=eigvec[k][q]; eigvec[k][p]=c*x-s*y; eigvec[k][q]=s*x+c*y; }
            }
        }
        // Descending PCA order; discard zero reference dimensions instead of amplifying them.
        for(int i=0;i<N;i++) eigen[i]=cov[i][i];
        for(int i=0;i<N;i++) for(int j=i+1;j<N;j++) if(eigen[j]>eigen[i]) {
            std::swap(eigen[i],eigen[j]);
            for(int k=0;k<N;k++) std::swap(eigvec[k][i],eigvec[k][j]);
        }
        rank=0;
        if(!std::isfinite(eigen[0]) || eigen[0]<1e-8f) return false;
        int max_rank=N-(REF_MODE!=0);
        while(rank<max_rank && eigen[rank]>eigen[0]*RANK_TOL) rank++;
        if(rank<2) return false;
        for(int k=0;k<rank;k++) for(int c=0;c<N;c++) white[k][c]=eigvec[c][k]/std::sqrt(eigen[k]);
        for(int k=0;k<rank;k++) rotate[k][k]=1;
        return true;
    }

    bool train_block() {
        // The fixed whitening + tanh natural-gradient update follows the Phase 37 approach.
        for(int i=0;i<rank;i++) for(int j=0;j<rank;j++) {
            float g=0;
            for(int b=0;b<16;b++) g+=std::tanh(block[i][b])*block[j][b];
            grad[i][j]=(i==j ? 1.0f : 0.0f)-g/16;
        }
        for(int i=0;i<rank;i++) for(int j=0;j<rank;j++) {
            float d=0; for(int k=0;k<rank;k++) d+=grad[i][k]*rotate[k][j];
            scratch[i][j]=rotate[i][j]+MU*d;
        }
        // Two-pass modified Gram-Schmidt maintains an orthogonal rotation.
        for(int i=0;i<rank;i++) {
            for(int pass=0;pass<2;pass++) for(int j=0;j<i;j++) {
                float dot=0; for(int k=0;k<rank;k++) dot+=scratch[i][k]*rotate[j][k];
                for(int k=0;k<rank;k++) scratch[i][k]-=dot*rotate[j][k];
            }
            float norm=0; for(int k=0;k<rank;k++) norm+=scratch[i][k]*scratch[i][k];
            if(!std::isfinite(norm) || norm<1e-12f) return false;
            for(int k=0;k<rank;k++) rotate[i][k]=scratch[i][k]/std::sqrt(norm);
        }
        return true;
    }

    bool freeze() {
        for(int k=0;k<rank;k++) for(int c=0;c<N;c++) {
            float u=0,a=0;
            for(int j=0;j<rank;j++) {
                u+=rotate[k][j]*white[j][c];
                a+=eigvec[c][j]*std::sqrt(eigen[j])*rotate[k][j];
            }
            if(!std::isfinite(a) || !std::isfinite(u)) return false;
            unmix[k][c]=u; mixing[c][k]=a;
        }
        if(REMOVE_MODE==1) {
            for(int k=0;k<N;k++) {
                if(REMOVE[k] && k>=rank) return false;
                removed[k]=REMOVE[k];
            }
        } else if(REMOVE_MODE==2) {
            float best=FRONTAL_THRESHOLD; int selected=-1;
            for(int k=0;k<rank;k++) {
                float frontal=0, total=0;
                for(int c=0;c<N;c++) { float a=std::fabs(mixing[c][k]); total+=a; if(FRONTAL[c]) frontal+=a; }
                float fraction=frontal/std::max(total,1e-12f);
                if(fraction>best) {best=fraction;selected=k;}
            }
            if(selected>=0) removed[selected]=true;
        }
        frozen=true; return true;
    }

    void fft() {
        for(int i=1,j=0;i<FFT;i++) {
            int bit=FFT>>1; for(;j&bit;bit>>=1) j^=bit; j^=bit;
            if(i<j) {std::swap(re[i],re[j]);std::swap(im[i],im[j]);}
        }
        for(int len=2;len<=FFT;len<<=1) {
            float a=-2*kPi/len, wr0=std::cos(a),wi0=std::sin(a);
            for(int i=0;i<FFT;i+=len) {
                float wr=1,wi=0;
                for(int j=0;j<len/2;j++) {
                    int u=i+j,v=u+len/2;
                    float vr=re[v]*wr-im[v]*wi,vi=re[v]*wi+im[v]*wr;
                    re[v]=re[u]-vr; im[v]=im[u]-vi; re[u]+=vr; im[u]+=vi;
                    float next=wr*wr0-wi*wi0; wi=wr*wi0+wi*wr0; wr=next;
                }
            }
        }
    }

public:
    int rank=0;
    bool removed[N]{};
    uint32_t samples_seen=0, resets=0;
    double timestamp=0;
    bool window_valid=false;
    explicit Engine(float* storage):ring(storage) {
        for(int i=0;i<FFT;i++) {hann[i]=0.5f-0.5f*std::cos(2*kPi*i/FFT);hann_energy+=hann[i]*hann[i];}
    }
    const char* status() const {
        if(failed) return "ica_failed";
        if(settled<SETTLE) return "filter_settling";
        if(ICA_ENABLED && calibrated<CAL) return "ica_calibration";
        if(ICA_ENABLED && !frozen) return "ica_training";
        if(filled<WINDOW) return "welch_warmup";
        return "ready";
    }
    bool fault() const {return failed;}
    void reset() {
        // Reconstruct in place without large temporary objects or stack allocation.
        float* storage=ring; uint32_t count=resets+1;
        this->~Engine(); new(this) Engine(storage); resets=count;
    }
    // Return true only for a complete window containing a single frozen ICA transform.
    bool push(const float* input, double ts) {
        samples_seen++; timestamp=ts;
        if(failed) return false;
        float x[N], ref=0;
        if(REF_MODE==1) ref=input[REF_CH];
        if(REF_MODE==2) {for(int c=0;c<N;c++) ref+=input[c];ref/=N;}
        bool bad=false;
        for(int c=0;c<N;c++) {
            x[c]=filter(c,input[c]-ref);
            if(!std::isfinite(x[c])) {failed=true;return false;}
            bad|=std::fabs(x[c])>MAX_UV;
        }
        if(settled<SETTLE) {settled++;return false;}
        if(ICA_ENABLED && calibrated<CAL) {
            calibrated++;
            float delta[N];
            for(int i=0;i<N;i++) {delta[i]=x[i]-mean[i];mean[i]+=delta[i]/calibrated;}
            for(int i=0;i<N;i++) for(int j=0;j<N;j++) cov[i][j]+=delta[i]*(x[j]-mean[j]);
            if(calibrated==CAL && !whiten()) failed=true;
            return false;
        }
        if(ICA_ENABLED && !frozen) {
            float z[N]{};
            for(int k=0;k<rank;k++) for(int c=0;c<N;c++) z[k]+=white[k][c]*(x[c]-mean[c]);
            for(int k=0;k<rank;k++) {
                float y=0;for(int j=0;j<rank;j++) y+=rotate[k][j]*z[j];
                if(!std::isfinite(y)) {failed=true;return false;}
                block[k][block_count]=y;
            }
            block_count++; trained++;
            if(block_count==16) {block_count=0;if(!train_block()) {failed=true;return false;}}
            if(trained>=TRAIN && !block_count && !freeze()) failed=true;
            return false;
        }
        if(ICA_ENABLED) {
            float source[N]{};
            for(int k=0;k<rank;k++) if(removed[k]) for(int c=0;c<N;c++) source[k]+=unmix[k][c]*(x[c]-mean[c]);
            for(int c=0;c<N;c++) for(int k=0;k<rank;k++) if(removed[k]) x[c]-=mixing[c][k]*source[k];
        }
        if(filled==WINDOW) rejected_count-=rejected[written];
        rejected[written]=bad; rejected_count+=bad;
        for(int c=0;c<N;c++) { if(!std::isfinite(x[c])) {failed=true;return false;} ring[c*WINDOW+written]=x[c]; }
        written=(written+1)%WINDOW; if(filled<WINDOW) filled++; hop++;
        if(filled<WINDOW || (hop<HOP && samples_seen)) return false;
        hop=0; window_valid=(rejected_count==0); return true;
    }

    // Periodic Hann, segment-wise mean detrend, one-sided density, mean over segments.
    void estimate() {
        std::memset(power,0,sizeof(power));
        bool needed[N]{};
        for(const auto& f:FEATURES) for(int j=0;j<f.count;j++) needed[f.channels[j]]=true;
        for(int c=0;c<N;c++) if(needed[c]) {
            int segments=0;
            for(int offset=0;offset+FFT<=WINDOW;offset+=FFT-OVERLAP) {
                float m=0;
                for(int i=0;i<FFT;i++) m+=ring[c*WINDOW+(written+offset+i)%WINDOW];
                m/=FFT;
                for(int i=0;i<FFT;i++) {re[i]=(ring[c*WINDOW+(written+offset+i)%WINDOW]-m)*hann[i];im[i]=0;}
                fft(); segments++;
                for(int k=0;k<=FFT/2;k++) {
                    float frequency=k*FS/FFT;
                    float density=(re[k]*re[k]+im[k]*im[k])/(FS*hann_energy);
                    if(k>0 && k<FFT/2) density*=2;
                    for(int b=0;b<BANDS;b++) if(frequency>=BAND_EDGES[b][0] && frequency<BAND_EDGES[b][1]) power[c][b]+=density*(FS/FFT);
                }
            }
            for(int b=0;b<BANDS;b++) power[c][b]/=segments;
        }
    }
    float band_power(int channel, int band) const {return power[channel][band];}
    float evaluate(const Ratio& f, int channel) const {
        float stack[63];int size=0;
        for(int i=0;i<f.token_count;i++) {
            auto t=f.tokens[i];
            if(t.op==0) stack[size++]=power[channel][int(t.value)];
            else if(t.op==1) stack[size++]=t.value;
            else {
                float b=stack[--size],a=stack[--size],result=0;
                if(t.op==2) result=a+b;
                if(t.op==3) result=a-b;
                if(t.op==4) result=a*b;
                if(t.op==5) {if(std::fabs(b)<MIN_DEN) return NAN;result=a/b;}
                if(!std::isfinite(result)) return NAN;
                stack[size++]=result;
            }
        }
        return stack[0];
    }
};
} // namespace copro
