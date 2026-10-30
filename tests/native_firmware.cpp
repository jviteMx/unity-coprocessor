#include "Arduino.h"
NativeSerial Serial;
void setup();
void loop();
int main() {
    Serial.input=std::vector<uint8_t>(std::istreambuf_iterator<char>(std::cin),std::istreambuf_iterator<char>());
    setup();
    while(Serial.available()) loop();
    return 0;
}
