#include <Arduino.h>
#include <esp_heap_caps.h>
#include "pipeline.h"
#include "wire.h"

using namespace cfg;
static copro::Engine* engine=nullptr;
static wire::Parser<4+32*(8+4*N)> parser;
static bool authorized=false;
static uint32_t expected=0, accepted=0, discontinuities=0, last_byte=0;
static double last_timestamp=0;

static void error(const char* message) {
    authorized=false;
    Serial.printf("{\"type\":\"error\",\"message\":\"%s\"}\n",message);
}
static void number(float x) {
    if(std::isfinite(x)) Serial.printf("%.8g",double(x));else Serial.print("null");
}
static void result(uint32_t compute_us) {
    Serial.printf("{\"type\":\"features\",\"config_id\":\"%s\",\"lsl_time\":%.9f,\"window_start\":%.9f,\"samples\":%lu,\"discontinuities\":%lu,\"valid\":%s,\"ica_rank\":%d,\"removed_components\":[",
        ID,engine->timestamp,engine->timestamp-(WINDOW-1)/double(FS),(unsigned long)accepted,(unsigned long)discontinuities,engine->window_valid?"true":"false",engine->rank);
    bool comma=false;
    for(int k=0;k<engine->rank;k++) if(engine->removed[k]) {if(comma)Serial.print(',');Serial.print(k);comma=true;}
    Serial.printf("],\"compute_us\":%lu,\"values\":[",(unsigned long)compute_us);
    comma=false;
    for(const auto& f:FEATURES) {
        float sum=0;bool valid=engine->window_valid;
        for(int j=0;j<f.count;j++) {
            float v=engine->evaluate(f,f.channels[j]);valid&=std::isfinite(v);sum+=v;
            if(!f.mean) {
                if(comma)Serial.print(',');comma=true;
                Serial.printf("{\"name\":\"%s\",\"channel\":\"%s\",\"valid\":%s,\"value\":",f.name,CHANNELS[f.channels[j]],engine->window_valid&&std::isfinite(v)?"true":"false");
                number(engine->window_valid?v:NAN);Serial.print('}');
            }
        }
        if(f.mean) {
            if(comma)Serial.print(',');comma=true;
            Serial.printf("{\"name\":\"%s\",\"channel\":\"mean\",\"valid\":%s,\"value\":",f.name,valid?"true":"false");number(valid?sum/f.count:NAN);Serial.print('}');
        }
    }
    Serial.println("]}");
}

static void packet() {
    const auto* p=parser.payload();
    if(parser.type()==wire::HELLO) {
        if(parser.length()!=64 || std::memcmp(p,ID,64)) {error("config_mismatch");return;}
        engine->reset();expected=accepted=discontinuities=0;last_timestamp=0;authorized=true;
        Serial.printf("{\"type\":\"hello\",\"config_id\":\"%s\",\"channels\":%d,\"sample_rate_hz\":%.6g}\n",ID,N,double(FS));return;
    }
    if(!authorized) {error("handshake_required");return;}
    if(parser.type()!=wire::DATA || parser.length()<4) {error("bad_type_or_length");return;}
    int nch=wire::u16(p), count=wire::u16(p+2);
    if(nch!=N || count<1 || count>32 || parser.length()!=4+count*(8+4*N)) {error("bad_shape");return;}
    if(parser.sequence()!=expected) {error("sequence_gap_or_duplicate");return;}
    // Validate the entire payload before committing any samples.
    double previous=last_timestamp;
    for(int i=0;i<count;i++) {
        const auto* sample=p+4+i*(8+4*N);double ts;std::memcpy(&ts,sample,8);
        if(!std::isfinite(ts) || ts<=previous) {error("invalid_timestamp");return;} previous=ts;
        for(int c=0;c<N;c++) {float v;std::memcpy(&v,sample+8+4*c,4);if(!std::isfinite(v) || std::fabs(v)>1e7f) {error("invalid_sample");return;}}
    }
    uint32_t start=micros();
    for(int i=0;i<count;i++) {
        const auto* sample=p+4+i*(8+4*N);double ts;float x[N];
        std::memcpy(&ts,sample,8);std::memcpy(x,sample+8,4*N);
        if(last_timestamp && ts-last_timestamp>2.5/double(FS)) {
            engine->reset();discontinuities++;
            Serial.println("{\"type\":\"status\",\"state\":\"timestamp_gap_recalibrating\"}");
        }
        last_timestamp=ts;accepted++;
        uint32_t t=micros();
        if(engine->push(x,ts)) {engine->estimate();result(micros()-t);}
        if(engine->fault()) {error("ica_or_filter_failed_restart_and_recalibrate");return;}
        yield();
    }
    expected++;
    Serial.printf("{\"type\":\"ack\",\"seq\":%lu,\"accepted\":%lu,\"state\":\"%s\",\"batch_compute_us\":%lu}\n",(unsigned long)parser.sequence(),(unsigned long)accepted,engine->status(),(unsigned long)(micros()-start));
}

void setup() {
    Serial.setRxBufferSize(16384);Serial.begin(921600);
    size_t bytes=sizeof(float)*N*WINDOW;
    auto* storage=static_cast<float*>(heap_caps_malloc(bytes,MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT));
    if(!storage) storage=static_cast<float*>(heap_caps_malloc(bytes,MALLOC_CAP_8BIT));
    engine=new(std::nothrow) copro::Engine(storage);
    if(!storage || !engine) {Serial.println("{\"type\":\"error\",\"message\":\"allocation_failed\"}");delete engine;engine=nullptr;return;}
    Serial.printf("{\"type\":\"boot\",\"config_id\":\"%s\",\"ring_bytes\":%u,\"engine_bytes\":%u}\n",ID,unsigned(bytes),unsigned(sizeof(copro::Engine)));
}
void loop() {
    if(!engine) {delay(100);return;}
    if(parser.partial() && uint32_t(millis()-last_byte)>2000) {parser.reset();error("packet_timeout");}
    while(Serial.available()) {
        int b=Serial.read();if(b<0)break;last_byte=millis();
        int state=parser.feed(uint8_t(b));
        if(state<0) error("packet_crc_or_header");
        if(state>0) packet();
    }
    delay(1);
}
