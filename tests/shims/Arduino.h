#pragma once
// Linux adapter for running the actual firmware entry points in protocol regressions.
#include <chrono>
#include <cstdarg>
#include <cstdio>
#include <cstdint>
#include <vector>
#include <iterator>
#include <iostream>
class NativeSerial {
public:
    std::vector<uint8_t> input;
    size_t index=0;
    void setRxBufferSize(int) {}
    void begin(int) {}
    int available() {return int(input.size()-index);}
    int read() {return available()?input[index++]:-1;}
    void print(const char* s) {std::fputs(s,stdout);}
    void print(char c) {std::putchar(c);}
    void print(int x) {std::printf("%d",x);}
    void println(const char* s) {std::puts(s);}
    void printf(const char* format,...) {va_list args;va_start(args,format);std::vprintf(format,args);va_end(args);}
};
extern NativeSerial Serial;
inline uint32_t micros() {return uint32_t(std::chrono::duration_cast<std::chrono::microseconds>(std::chrono::steady_clock::now().time_since_epoch()).count());}
inline uint32_t millis() {return micros()/1000;}
inline void delay(int) {}
inline void yield() {}
