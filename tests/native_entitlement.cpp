// Public license ABI under sanitizers: buffer guards, hostile input, modes and session limits.
// Arguments are name=value pairs: the shared vectors of tests/fixtures/entitlement.json plus
// keys activated for this host (tools/entitlement_dev.py host_vectors).
#include "fisica_core.h"
#include <array>
#include <cassert>
#include <cstdio>
#include <cstring>
#include <map>
#include <string>
#include <vector>

static std::map<std::string, std::string> vectors;
static const uint64_t day = 86400;

static uint32_t import(FisicaEntitlement *context, const std::string& token, uint64_t now) {
    uint32_t outcome = 99;
    assert(fisica_core_entitlement_import(context, reinterpret_cast<const uint8_t*>(token.data()), uint32_t(token.size()), now, &outcome) == FISICA_OK);
    return outcome;
}
static std::array<uint64_t, FISICA_LIMIT_STATUS_FIELDS> status(FisicaEntitlement *context, uint64_t now) {
    std::array<uint64_t, FISICA_LIMIT_STATUS_FIELDS> values{};
    assert(fisica_core_entitlement_status(context, now, values.data(), uint32_t(values.size())) == FISICA_OK);
    return values;
}
static std::string text(FisicaEntitlement *context, uint32_t field, int32_t expected = FISICA_OK) {
    std::vector<uint8_t> buffer(8192); uint32_t length = 0;
    assert(fisica_core_entitlement_text(context, field, buffer.data(), uint32_t(buffer.size()), &length) == expected);
    return expected == FISICA_OK ? std::string(buffer.begin(), buffer.begin() + length) : std::string();
}

// One session in MEASURING over USB, fed measurement packets 300 ms apart.
struct Measuring {
    FisicaSession *session = nullptr;
    uint64_t now = 1000;
    Measuring() {
        assert(fisica_core_session_create(&session) == FISICA_OK);
        assert(fisica_core_session_action(session, FISICA_CONNECTED, FISICA_LINK_USB, now) == FISICA_OK);
        const uint8_t firmware[] = {0x24,0x43,0x47,1,'2','.','0','.','1',0xff,0xf0};
        feed(firmware, sizeof firmware);
        assert(fisica_core_session_action(session, FISICA_START, -1, now) == FISICA_OK);
        const uint8_t usb[] = {0x24,0x22,0x53,1,0,2,0xff,0xf0}, start[] = {0x24,0x51,0x53,1,0,1,0xff,0xf0};
        feed(usb, sizeof usb); feed(start, sizeof start);
        drain();
    }
    ~Measuring() { fisica_core_session_destroy(session); }
    void feed(const uint8_t *bytes, uint32_t length) { assert(fisica_core_session_feed(session, bytes, length, now) == FISICA_OK); }
    std::vector<uint32_t> drain() {
        std::vector<uint32_t> sizes; uint32_t event[8]; std::vector<uint8_t> payload(FISICA_LIMIT_EVENT_BYTES);
        while (fisica_core_session_poll(session, event, 8, payload.data(), uint32_t(payload.size())) == FISICA_OK) {
            if (event[0] != FISICA_MEASUREMENT) continue;
            assert(event[5] == 0 && event[6] == 0);
            double weight; std::memcpy(&weight, payload.data(), 8);
            assert(weight == 70.5);
            sizes.push_back(event[7]);
        }
        return sizes;
    }
    // Returns the MEASUREMENT payload sizes handed out for `count` packets.
    std::vector<uint32_t> packets(int count) {
        std::array<uint8_t,645> packet{};
        for (int i = 1; i < 300; ++i) packet[i] = uint8_t(10 + i % 7);
        packet[639]=packet[643]=0xf0; packet[640]=packet[644]=0x0f; packet[641]=2; packet[642]=193;
        std::vector<uint32_t> sizes;
        for (int i = 0; i < count; ++i) {
            now += 300; feed(packet.data(), 645);
            for (auto size : drain()) sizes.push_back(size);
        }
        return sizes;
    }
};

int main(int argc, char **argv) {
    for (int i = 1; i < argc; ++i) {
        const std::string pair = argv[i]; const size_t eq = pair.find('=');
        assert(eq != std::string::npos);
        vectors[pair.substr(0, eq)] = pair.substr(eq + 1);
    }
    const uint64_t now = std::stoull(vectors.at("now"));
    const std::string unbound = vectors.at("unbound"), full = vectors.at("full");
    FisicaEntitlement *context = nullptr;
    assert(fisica_core_entitlement_create(nullptr) == FISICA_INVALID_ARGUMENT);
    assert(fisica_core_entitlement_create(&context) == FISICA_OK && context);
    std::array<uint64_t, FISICA_LIMIT_STATUS_FIELDS + 1> guard{}; guard.back() = 99;
    assert(fisica_core_entitlement_status(context, now, guard.data(), FISICA_LIMIT_STATUS_FIELDS - 1) == FISICA_BUFFER_TOO_SMALL);
    assert(fisica_core_entitlement_status(context, now, guard.data(), FISICA_LIMIT_STATUS_FIELDS) == FISICA_OK && guard.back() == 99);
    assert(guard[0] == FISICA_MODE_BASIC && guard[1] == FISICA_REASON_NO_KEY && guard[3] == FISICA_CONTRACT_UNVERIFIED);
    // The core reads this host's target itself.
    const uint64_t target_type = vectors.at("target_type") == "app" ? FISICA_BIND_APP : FISICA_BIND_PC;
    assert(guard[7] == target_type && text(context, FISICA_ENTITLEMENT_TARGET) == vectors.at("target"));
    assert(text(context, FISICA_ENTITLEMENT_ACTIVATION_REQUEST) ==
           "{\"bind_type\":\"" + vectors.at("target_type") + "\",\"bind\":\"" + vectors.at("target") + "\"}");
    text(context, FISICA_ENTITLEMENT_ACTIVATION_CODE, FISICA_INVALID_STATE);
    uint32_t outcome = 0;
    assert(fisica_core_entitlement_import(context, nullptr, 4, now, &outcome) == FISICA_INVALID_ARGUMENT);
    assert(fisica_core_entitlement_import(nullptr, nullptr, 0, now, &outcome) == FISICA_INVALID_ARGUMENT);

    // Every single-character change must be rejected and must leave the context without a key.
    const char replacements[] = {'A', 'B', '-', '_', '.', '=', ' ', '\n', '\0', '{', '\x7f', char(0xff)};
    size_t rejected = 0;
    for (size_t i = 0; i < unbound.size(); ++i) {
        for (char replacement : replacements) {
            if (unbound[i] == replacement) continue;
            std::string mutated = unbound; mutated[i] = replacement;
            assert(import(context, mutated, now) >= FISICA_ENTITLEMENT_REJECTED_FORMAT);
            ++rejected;
        }
    }
    for (size_t length = 0; length < unbound.size(); ++length) assert(import(context, unbound.substr(0, length), now) >= FISICA_ENTITLEMENT_REJECTED_FORMAT);
    std::string huge(5000, 'A'); std::memcpy(&huge[0], "FSE1.", 5);
    assert(import(context, huge, now) == FISICA_ENTITLEMENT_REJECTED_FORMAT);
    uint32_t seed = 12345;
    for (int round = 0; round < 20000; ++round) {
        std::string noise(1 + (seed >> 8) % 300, '\0');
        for (auto& c : noise) { seed = seed * 1664525u + 1013904223u; c = char(seed >> 24); }
        if (round % 2) std::memcpy(&noise[0], "FSE1.", noise.size() < 5 ? noise.size() : 5);
        assert(import(context, noise, now) >= FISICA_ENTITLEMENT_REJECTED_FORMAT);
    }
    auto values = status(context, now);
    assert(values[1] == FISICA_REASON_NO_KEY && (values[17] & FISICA_WARN_IMPORT_REJECTED) && (values[17] & FISICA_WARN_TEST_KEY) == 0);
    assert(import(context, vectors.at("tampered"), now) >= FISICA_ENTITLEMENT_REJECTED_FORMAT);
    assert(import(context, vectors.at("unknown_key"), now) == FISICA_ENTITLEMENT_REJECTED_KEY);

    // A legacy contract token names the company but is not a seat key: basic mode.
    assert(import(context, vectors.at("legacy"), now) == FISICA_ENTITLEMENT_ACCEPTED);
    values = status(context, now);
    assert(values[0] == FISICA_MODE_BASIC && values[1] == FISICA_REASON_LEGACY_TOKEN && values[3] == FISICA_CONTRACT_ACTIVE);

    // An unactivated key: basic mode, activation request and code for this target.
    assert(import(context, unbound + "\n", now) == FISICA_ENTITLEMENT_ACCEPTED);
    values = status(context, now);
    assert(values[0] == FISICA_MODE_BASIC && values[1] == FISICA_REASON_NOT_ACTIVATED && values[5] == FISICA_TRACK_TRIAL);
    assert(values[6] == FISICA_BIND_NONE && values[11] == now + 14 * day && (values[17] & FISICA_WARN_TEST_KEY));
    assert(text(context, FISICA_ENTITLEMENT_KEY) == "key_test_1");
    const std::string state_name = text(context, FISICA_ENTITLEMENT_STATE_NAME);
    assert(state_name.rfind("entitlement-", 0) == 0 && state_name.size() == 12 + 16 + 6 && state_name.substr(28) == ".state");
    const std::string code = text(context, FISICA_ENTITLEMENT_ACTIVATION_CODE);
    assert(code.rfind("FAC1.", 0) == 0 && code.size() > 15 && code[code.size() - 9] == '.');
    std::array<uint8_t,8> small{}; small.fill(99); uint32_t length = 0;
    assert(fisica_core_entitlement_text(context, FISICA_ENTITLEMENT_COMPANY, small.data(), 3, &length) == FISICA_BUFFER_TOO_SMALL && length == 4);
    for (auto value : small) assert(value == 99);
    assert(fisica_core_entitlement_text(context, FISICA_ENTITLEMENT_COMPANY, small.data(), 4, &length) == FISICA_OK && std::memcmp(small.data(), "acme", 4) == 0 && small[4] == 99);
    assert(fisica_core_entitlement_text(context, 99, small.data(), 8, &length) == FISICA_INVALID_ARGUMENT);
    assert(fisica_core_entitlement_release(context, small.data(), 8, &length) == FISICA_INVALID_STATE);
    assert(import(context, vectors.at("unbound_older"), now) == FISICA_ENTITLEMENT_IGNORED_OLDER);
    // A key activated for another PC never replaces the key this host holds.
    assert(import(context, vectors.at("other_target"), now) == FISICA_ENTITLEMENT_REJECTED_TARGET);
    assert(text(context, FISICA_ENTITLEMENT_KEY) == "key_test_1" && status(context, now)[1] == FISICA_REASON_NOT_ACTIVATED);

    // Activated here: full mode with every feature until the trial ends.
    assert(import(context, full, now) == FISICA_ENTITLEMENT_ACCEPTED);
    values = status(context, now + 60);
    assert(values[0] == FISICA_MODE_FULL && values[1] == FISICA_REASON_NONE && values[6] == target_type);
    assert(values[2] == (FISICA_FEATURE_RAW_DATA | FISICA_FEATURE_EXPORT | FISICA_FEATURE_ADVANCED | FISICA_FEATURE_FULL_RATE));
    assert(values[10] == now + 90 * day && (values[17] & FISICA_WARN_RENEW_DUE) == 0 && values[18] == now + 60);
    assert(import(context, vectors.at("full_older"), now) == FISICA_ENTITLEMENT_IGNORED_OLDER);
    std::array<uint8_t,645> packet{}; packet[639]=packet[643]=0xf0; packet[640]=packet[644]=0x0f; packet[641]=2; packet[642]=193;
    std::array<double,638> raw{}; std::array<double,841> display{}; std::array<double,2> metrics{};
    assert(fisica_core_decode_packet(context, now, packet.data(), 645, raw.data(), 638, display.data(), 841, metrics.data(), 2) == FISICA_OK && metrics[0] == 70.5);
    // A clock set back by more than an hour is basic mode; a smaller correction is not.
    assert(status(context, now - 1800)[0] == FISICA_MODE_FULL);
    assert(status(context, now - 7200)[1] == FISICA_REASON_CLOCK_ROLLBACK);
    assert(fisica_core_decode_packet(context, now - 7200, packet.data(), 645, raw.data(), 638, display.data(), 841, metrics.data(), 2) == FISICA_FEATURE_NOT_LICENSED);
    assert(status(context, now + 8 * day)[17] & FISICA_WARN_FULL_ENDING);
    assert(status(context, now + 15 * day)[1] == FISICA_REASON_FULL_ENDED);
    assert(status(context, now + 15 * day)[1] == FISICA_REASON_FULL_ENDED);
    assert(status(context, now + 91 * day)[1] == FISICA_REASON_KEY_EXPIRED);
    // The latest time seen now lies in the future of the key: going back to `now` is a rollback.
    assert(status(context, now)[1] == FISICA_REASON_CLOCK_ROLLBACK);

    // State: the key and the latest time seen, and nothing about use.
    std::vector<uint8_t> state(262144, 99);
    assert(fisica_core_entitlement_export(context, state.data(), 4, &length) == FISICA_BUFFER_TOO_SMALL);
    assert(state[0] == 99);
    assert(fisica_core_entitlement_export(context, state.data(), uint32_t(state.size()), &length) == FISICA_OK);
    const std::vector<uint8_t> saved(state.begin(), state.begin() + length);
    assert(std::string(saved.begin(), saved.end()) == "FSS1\nC " + full + "\nT " + std::to_string(now + 91 * day) + "\n");
    FisicaEntitlement *other = nullptr;
    assert(fisica_core_entitlement_create(&other) == FISICA_OK);
    assert(fisica_core_entitlement_restore(other, saved.data(), uint32_t(saved.size())) == FISICA_OK);
    assert(status(other, now)[1] == FISICA_REASON_CLOCK_ROLLBACK && status(other, now + 92 * day)[1] == FISICA_REASON_KEY_EXPIRED);
    // Importing the same key file again does not forget the latest time seen.
    assert(import(other, full, now) == FISICA_ENTITLEMENT_ACCEPTED);
    assert(status(other, now + 60)[1] == FISICA_REASON_CLOCK_ROLLBACK);
    // Usage lines from earlier development builds are ignored and not written back.
    const std::string legacy = "FSS1\nC " + full + "\nU " + full + " 1800000007 0 1\n";
    assert(fisica_core_entitlement_restore(other, reinterpret_cast<const uint8_t*>(legacy.data()), uint32_t(legacy.size())) == FISICA_OK);
    assert(fisica_core_entitlement_export(other, state.data(), uint32_t(state.size()), &length) == FISICA_OK);
    assert(std::string(state.begin(), state.begin() + length).find("\nU ") == std::string::npos);
    // A stored state edited byte by byte is either refused or loses the key that no longer verifies.
    for (size_t i = 0; i < saved.size(); i += 3) {
        std::vector<uint8_t> edited = saved; edited[i] ^= 0x21;
        const int32_t restored = fisica_core_entitlement_restore(other, edited.data(), uint32_t(edited.size()));
        assert(restored == FISICA_OK || restored == FISICA_INVALID_ARGUMENT);
        status(other, now);
    }
    for (size_t size = 0; size < saved.size(); size += 7) fisica_core_entitlement_restore(other, saved.data(), uint32_t(size));
    assert(fisica_core_entitlement_restore(other, nullptr, 10) == FISICA_INVALID_ARGUMENT);
    fisica_core_entitlement_destroy(other);

    // Sessions: basic mode hands out one display frame a second and never the packet.
    {
        Measuring basic;
        const auto sizes = basic.packets(10); // 3 s of packets, 300 ms apart
        assert(sizes.size() == 3);
        for (auto size : sizes) assert(size == 8 * 842);
        double result[5];
        assert(fisica_core_session_analyze(basic.session, result, 5) == FISICA_OK && result[0] == 70.5);
        assert(fisica_core_session_analyze(basic.session, result, 4) == FISICA_BUFFER_TOO_SMALL);
    }
    FisicaEntitlement *licensed = nullptr;
    assert(fisica_core_entitlement_create(&licensed) == FISICA_OK && import(licensed, full, now) == FISICA_ENTITLEMENT_ACCEPTED);
    {
        Measuring session;
        assert(fisica_core_session_license(session.session, licensed, now, session.now) == FISICA_OK);
        for (auto size : session.packets(10)) assert(size == 8 * (842 + 638) + 645);
        assert(session.packets(10).size() == 10);
        // NULL returns the session to basic mode.
        assert(fisica_core_session_license(session.session, nullptr, now, session.now) == FISICA_OK);
        assert(session.packets(10).size() <= 4);
    }
    {
        // Full mode ends on the session's own clock when the key or contract ends.
        Measuring session;
        assert(fisica_core_session_license(session.session, licensed, now + 14 * day - 2, session.now) == FISICA_OK);
        assert(session.packets(5).size() == 5);
        assert(session.packets(10).size() <= 4);
    }
    {
        // A key without RAW_DATA: every frame, no packet, no raw grid.
        FisicaEntitlement *limited = nullptr;
        assert(fisica_core_entitlement_create(&limited) == FISICA_OK && import(limited, vectors.at("limited"), now) == FISICA_ENTITLEMENT_ACCEPTED);
        assert(status(limited, now)[2] == (FISICA_FEATURE_EXPORT | FISICA_FEATURE_FULL_RATE));
        assert(fisica_core_decode_packet(limited, now, packet.data(), 645, raw.data(), 638, display.data(), 841, metrics.data(), 2) == FISICA_FEATURE_NOT_LICENSED);
        Measuring session;
        assert(fisica_core_session_license(session.session, limited, now, session.now) == FISICA_OK);
        for (auto size : session.packets(10)) assert(size == 8 * 842);
        fisica_core_entitlement_destroy(limited);
    }

    // A released key (signed by the server at refresh) is basic mode.
    assert(import(licensed, vectors.at("revoked"), now) == FISICA_ENTITLEMENT_ACCEPTED);
    assert(status(licensed, now + day)[1] == FISICA_REASON_REVOKED && status(licensed, now + day)[3] == FISICA_CONTRACT_REVOKED);
    fisica_core_entitlement_destroy(licensed);

    // Releasing on the device: the code, then no key. A too small buffer changes nothing.
    FisicaEntitlement *releasing = nullptr;
    assert(fisica_core_entitlement_create(&releasing) == FISICA_OK && import(releasing, full, now) == FISICA_ENTITLEMENT_ACCEPTED);
    std::vector<uint8_t> release(256, 0);
    assert(fisica_core_entitlement_release(releasing, release.data(), 8, &length) == FISICA_BUFFER_TOO_SMALL);
    assert(status(releasing, now)[0] == FISICA_MODE_FULL);
    assert(fisica_core_entitlement_release(releasing, release.data(), uint32_t(release.size()), &length) == FISICA_OK);
    const std::string release_code(release.begin(), release.begin() + length);
    assert(release_code.rfind("FRC1.", 0) == 0);
    assert(status(releasing, now)[1] == FISICA_REASON_NO_KEY);
    assert(fisica_core_entitlement_release(releasing, release.data(), uint32_t(release.size()), &length) == FISICA_INVALID_STATE);
    fisica_core_entitlement_destroy(releasing);

    fisica_core_entitlement_destroy(context);
    fisica_core_entitlement_destroy(nullptr);
    std::printf("Native license contract passed (%zu single-character mutations rejected)\nrelease_code=%s\nactivation_code=%s\n",
                rejected, release_code.c_str(), code.c_str());
}
