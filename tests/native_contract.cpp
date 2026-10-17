#include "fisica_core.h"
#include "../native/src/fisica_license.h"
#include <array>
#include <cassert>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <string>

int main() {
    assert((fisica_core_abi_version() >> 16) == 2);
    std::array<uint8_t,645> packet{};
    packet[638]=packet[643]=0xf0; packet[639]=packet[644]=0x0f;
    packet[641]=2; packet[642]=193;
    std::array<double,639> raw{}; raw.fill(42);
    std::array<double,841> display{};
    std::array<double,2> metrics{};
    assert(fisica_core_decode_packet(nullptr,0,packet.data(),645,raw.data(),637,display.data(),841,metrics.data(),2)==FISICA_BUFFER_TOO_SMALL);
    for (auto value:raw) assert(value==42);
    // Raw and processed grids need full mode: without a license nothing is written.
    assert(fisica_core_decode_packet(nullptr,0,packet.data(),645,raw.data(),638,display.data(),841,metrics.data(),2)==FISICA_FEATURE_NOT_LICENSED);
    for (auto value:raw) assert(value==42);
    FisicaEntitlement *basic=nullptr;
    assert(fisica_core_entitlement_create(&basic)==FISICA_OK);
    assert(fisica_core_decode_packet(basic,1800000000,packet.data(),645,raw.data(),638,display.data(),841,metrics.data(),2)==FISICA_FEATURE_NOT_LICENSED);
    assert(fisica_core_display_grid(basic,1800000000,raw.data(),7,display.data(),841)==FISICA_FEATURE_NOT_LICENSED);
    assert(fisica_core_preprocess(basic,1800000000,raw.data(),29,22,display.data(),841)==FISICA_FEATURE_NOT_LICENSED);
    fisica_core_entitlement_destroy(basic);
    assert(std::strstr(fisica_core_error_message(FISICA_FEATURE_NOT_LICENSED),"full mode"));
    assert(fisica_core_decode_packet(nullptr,0,nullptr,645,raw.data(),638,display.data(),841,metrics.data(),2)==FISICA_INVALID_ARGUMENT);
    uint32_t consumed=0,kind=0,length=0;
    std::array<uint8_t,645> result{};
    assert(fisica_core_next_packet(packet.data(),645,&consumed,&kind,result.data(),644,&length)==FISICA_BUFFER_TOO_SMALL);
    assert(consumed==0 && length==645);
    assert(fisica_core_next_packet(packet.data(),645,&consumed,&kind,result.data(),645,&length)==FISICA_OK);
    assert(consumed==645 && kind==1 && std::memcmp(packet.data(),result.data(),645)==0);
    assert(fisica_core_preprocess(nullptr,0,raw.data(),UINT32_MAX,UINT32_MAX,display.data(),841)==FISICA_INVALID_ARGUMENT);
    // Weight and foot size need no license.
    double size[5];
    assert(fisica_core_analyze_packets(packet.data(),1,size,5)==FISICA_NO_RESULT || size[0]>=0);
    const double invalid[]={NAN,0};
    assert(fisica_core_cop(invalid,1,2,1,1,metrics.data(),2)==FISICA_INVALID_ARGUMENT);
    double analysis[5];
    assert(fisica_core_analyze(raw.data(),metrics.data(),65536,16777216,16777216,analysis,5)==FISICA_INVALID_ARGUMENT);
    std::array<uint8_t,8> command{}; command.fill(99);
    assert(fisica_core_command("START_CONTINUOUS_MEASUREMENT",0,nullptr,0,command.data(),7,&length)==FISICA_BUFFER_TOO_SMALL);
    for (auto value:command) assert(value==99);
    assert(fisica_core_command("create_weight_scale",32768,nullptr,0,command.data(),8,&length)==FISICA_INVALID_ARGUMENT);
    const uint8_t firmware[]={0x24,0x43,0x47,1,'2','.','0','.','1',0xff,0xf0};
    std::array<uint8_t,6> payload{}; payload.fill(99);
    assert(fisica_core_response_payload(firmware,sizeof(firmware),payload.data(),4,&length)==FISICA_BUFFER_TOO_SMALL);
    assert(length==5);
    for (auto value:payload) assert(value==99);
    assert(fisica_core_response_payload(firmware,sizeof(firmware),payload.data(),5,&length)==FISICA_OK);
    assert(payload[5]==99 && std::memcmp(payload.data(),"2.0.1",5)==0);
    int32_t response[6];
    assert(fisica_core_decode_response(firmware,sizeof(firmware),response,6)==FISICA_OK);
    assert(response[0]==0x43 && response[5]==1);
    assert(fisica_core_next_packet(firmware,sizeof(firmware),&consumed,&kind,result.data(),645,&length)==FISICA_OK);
    assert(consumed==sizeof(firmware) && kind==2);
    FisicaSession *session=nullptr;
    assert(fisica_core_session_create(nullptr)==FISICA_INVALID_ARGUMENT);
    assert(fisica_core_session_create(&session)==FISICA_OK && session);
    uint32_t event[9]; std::fill(event,event+9,99);
    assert(fisica_core_session_action(session,FISICA_CONNECTED,2,0)==FISICA_OK);
    assert(fisica_core_session_poll(session,event,7,command.data(),8)==FISICA_BUFFER_TOO_SMALL);
    assert(event[8]==99);
    assert(fisica_core_session_poll(session,event,8,command.data(),8)==FISICA_OK && event[0]==FISICA_STATE);
    command.fill(99);
    assert(fisica_core_session_poll(session,event,8,command.data(),7)==FISICA_BUFFER_TOO_SMALL && event[7]==8);
    for (auto value:command) assert(value==99);
    assert(fisica_core_session_poll(session,event,8,command.data(),8)==FISICA_OK && event[0]==FISICA_TRANSMIT);
    assert(fisica_core_session_tick(session,2000)==FISICA_OK);
    assert(fisica_core_session_poll(session,event,8,command.data(),8)==FISICA_OK && event[2]==FISICA_COMMAND_TIMEOUT);
    assert(fisica_core_session_tick(session,1999)==FISICA_INVALID_ARGUMENT);
    assert(fisica_core_session_feed(session,nullptr,1,2000)==FISICA_INVALID_ARGUMENT);
    assert(fisica_core_session_action(session,FISICA_START,-1,2001)==FISICA_OK);
    assert(fisica_core_session_tick(session,4001)==FISICA_OK);
    bool fault=false,timeout=false;
    while (fisica_core_session_poll(session,event,8,result.data(),645)==FISICA_OK) {
        fault |= event[0]==FISICA_STATE && event[1]==FISICA_FAULT;
        timeout |= event[0]==FISICA_RESULT && event[2]==FISICA_COMMAND_TIMEOUT;
    }
    assert(fault && timeout && event[8]==99);
    fisica_core_session_destroy(session);
    fisica_core_session_destroy(nullptr);

    // Android: an app ID counts only when that package's own installed APK is mapped.
    const std::string maps =
        "7f00-7f10 r--p 00000000 fd:05 1 /data/app/~~Xk2==/com.acme.kiosk-Ab3==/base.apk\n"
        "7f20-7f30 r--p 00000000 fd:05 2 /data/app/~~Zz1==/com.google.android.webview-Q1==/base.apk\n"
        "7f40-7f50 r--p 00000000 fd:05 3 /system/framework/framework.jar\n";
    assert(fisica::apk_mapped(maps, "com.acme.kiosk"));
    assert(fisica::apk_mapped(maps, "com.google.android.webview"));
    assert(!fisica::apk_mapped(maps, "com.victim.app"));  // a process named after another app
    assert(!fisica::apk_mapped(maps, "com.acme"));        // a prefix is not the package
    assert(fisica::apk_mapped("1-2 r-xp 0 0:0 0 /data/app/com.acme.kiosk-1/base.apk", "com.acme.kiosk"));
    assert(!fisica::apk_mapped("1-2 r-xp 0 0:0 0 /sdcard/com.acme.kiosk-1/base.apk", "com.acme.kiosk"));
    assert(fisica::apk_mapped("1-2 r-xp 0 0:0 0 /mnt/expand/1a2b/app/~~Q==/com.acme.kiosk-R==/base.apk", "com.acme.kiosk"));
    // Only where the system installs APKs: not a copy in another app's files or in Downloads.
    assert(!fisica::apk_mapped("1-2 r--p 0 0:0 0 /data/user/0/com.other/files/app/com.acme.kiosk-1/base.apk", "com.acme.kiosk"));
    assert(!fisica::apk_mapped("1-2 r--p 0 0:0 0 /storage/emulated/0/Download/app/com.acme.kiosk-1/base.apk", "com.acme.kiosk"));
    assert(!fisica::apk_mapped("1-2 r--p 0 0:0 0 /data/app/x/y/com.acme.kiosk-1/base.apk", "com.acme.kiosk"));
    assert(!fisica::apk_mapped("1-2 r--p 0 0:0 0 /data/app/com.acme.kiosk-1/split_config.apk", "com.acme.kiosk"));
}
