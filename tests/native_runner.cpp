#include "pipeline.h"
#include "wire.h"
#include <cstdio>
#include <vector>
#include <memory>

int main() {
    std::vector<float> ring(cfg::N*cfg::WINDOW);
    std::unique_ptr<copro::Engine> engine(new copro::Engine(ring.data()));
    float x[cfg::N];double ts;
    while(std::fread(&ts,sizeof(ts),1,stdin)==1 && std::fread(x,sizeof(float),cfg::N,stdin)==cfg::N) {
        if(engine->push(x,ts)) {
            engine->estimate();
            std::printf("%.9f %d %d",ts,engine->window_valid,engine->rank);
            for(int k=0;k<cfg::N;k++) std::printf(" %d",engine->removed[k]);
            for(int c=0;c<cfg::N;c++) for(int b=0;b<cfg::BANDS;b++) std::printf(" %.9g",double(engine->band_power(c,b)));
            for(const auto& f:cfg::FEATURES) for(int j=0;j<f.count;j++) std::printf(" %.9g",double(engine->evaluate(f,f.channels[j])));
            std::printf("\n");
        }
    }
    if(engine->fault()) {std::fprintf(stderr,"Pipeline failed: %s\n",engine->status());return 2;}
    return 0;
}
