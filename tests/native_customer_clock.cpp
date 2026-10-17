// Built against a customer (production keys only) release header: the core reads the system
// clock itself, so a host cannot set the license time back or space frames out on paper.
#include "fisica_core.h"
#include <array>
#include <cassert>
#include <chrono>
#include <cstdint>
#include <vector>

int main() {
    const auto system = uint64_t(std::chrono::duration_cast<std::chrono::seconds>(
        std::chrono::system_clock::now().time_since_epoch()).count());
    FisicaEntitlement *context = nullptr;
    assert(fisica_core_entitlement_create(&context) == FISICA_OK);
    std::array<uint64_t, FISICA_LIMIT_STATUS_FIELDS> status{};
    // The host claims 1970; the core uses its own clock (the latest time seen is today).
    assert(fisica_core_entitlement_status(context, 1, status.data(), status.size()) == FISICA_OK);
    assert(status[18] + 5 >= system);
    // Milliseconds passed as seconds are refused, not stored as the latest time.
    assert(fisica_core_entitlement_status(context, system * 1000, status.data(), status.size()) == FISICA_INVALID_ARGUMENT);
    uint32_t exchange = 99;
    assert(fisica_core_entitlement_next_exchange(context, 1, &exchange) == FISICA_OK && exchange == FISICA_EXCHANGE_NONE);
    fisica_core_entitlement_destroy(context);

    // Basic mode: frames fed with made-up times one second apart still arrive at most once a
    // real second.
    std::array<uint8_t, 645> packet{};
    packet[638] = packet[643] = 0xf0; packet[639] = packet[644] = 0x0f; packet[100] = 7;
    FisicaSession *session = nullptr;
    assert(fisica_core_session_create(&session) == FISICA_OK);
    std::vector<uint8_t> probe = {0x24,0x43,0x47,0x01,0x32,0x2e,0x30,0x2e,0x31,0xff,0xf0};
    std::vector<uint8_t> mode = {0x24,0x22,0x53,0x01,0x00,0x00,0xff,0xf0}, start = {0x24,0x51,0x53,0x01,0x00,0x00,0xff,0xf0};
    assert(fisica_core_session_action(session, FISICA_CONNECTED, 2, 0) == FISICA_OK);
    assert(fisica_core_session_feed(session, probe.data(), probe.size(), 0) == FISICA_OK);
    assert(fisica_core_session_action(session, FISICA_START, -1, 0) == FISICA_OK);
    assert(fisica_core_session_feed(session, mode.data(), mode.size(), 0) == FISICA_OK);
    assert(fisica_core_session_feed(session, start.data(), start.size(), 0) == FISICA_OK);
    for (uint64_t i = 1; i <= 5; ++i) assert(fisica_core_session_feed(session, packet.data(), packet.size(), i * 1000) == FISICA_OK);
    uint32_t event[8]; std::vector<uint8_t> payload(FISICA_LIMIT_EVENT_BYTES); int frames = 0;
    while (fisica_core_session_poll(session, event, 8, payload.data(), payload.size()) == FISICA_OK)
        if (event[0] == FISICA_MEASUREMENT) ++frames;
    assert(frames == 1);
    fisica_core_session_destroy(session);
    return 0;
}
