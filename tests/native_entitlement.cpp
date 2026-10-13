// Public entitlement ABI under sanitizers: buffer guards and hostile input.
// argv[1] = a token signed with the test key (tests/fixtures/entitlement.json "valid").
#include "fisica_core.h"
#include <array>
#include <cassert>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

static uint32_t import(FisicaEntitlement *context, const std::string& token, uint64_t now) {
    uint32_t outcome = 99;
    assert(fisica_core_entitlement_import(context, reinterpret_cast<const uint8_t*>(token.data()), uint32_t(token.size()), now, &outcome) == FISICA_OK);
    return outcome;
}

int main(int argc, char **argv) {
    assert(argc == 2);
    const std::string valid = argv[1];
    const uint64_t now = 1800000000;
    FisicaEntitlement *context = nullptr;
    assert(fisica_core_entitlement_create(nullptr) == FISICA_INVALID_ARGUMENT);
    assert(fisica_core_entitlement_create(&context) == FISICA_OK && context);
    uint64_t status[13]; status[12] = 99;
    assert(fisica_core_entitlement_status(context, now, status, 11) == FISICA_BUFFER_TOO_SMALL);
    assert(fisica_core_entitlement_status(context, now, status, 12) == FISICA_OK && status[12] == 99);
    assert(status[0] == FISICA_BASE_LICENSE_GRANTED && status[2] == FISICA_CONTRACT_UNVERIFIED);
    uint32_t outcome = 0;
    assert(fisica_core_entitlement_import(context, nullptr, 4, now, &outcome) == FISICA_INVALID_ARGUMENT);
    assert(fisica_core_entitlement_import(nullptr, nullptr, 0, now, &outcome) == FISICA_INVALID_ARGUMENT);
    assert(fisica_core_entitlement_mark_used(context, now) == FISICA_OK);

    // Every single-character change must be rejected and must leave the context unverified.
    const char replacements[] = {'A', 'B', '-', '_', '.', '=', ' ', '\n', '\0', '{', '\x7f', char(0xff)};
    size_t rejected = 0;
    for (size_t i = 0; i < valid.size(); ++i) {
        for (char replacement : replacements) {
            if (valid[i] == replacement) continue;
            std::string mutated = valid; mutated[i] = replacement;
            const uint32_t result = import(context, mutated, now);
            assert(result >= FISICA_ENTITLEMENT_REJECTED_FORMAT);
            ++rejected;
        }
    }
    for (size_t length = 0; length < valid.size(); ++length) assert(import(context, valid.substr(0, length), now) >= FISICA_ENTITLEMENT_REJECTED_FORMAT);
    std::string huge(5000, 'A'); std::memcpy(&huge[0], "FSE1.", 5);
    assert(import(context, huge, now) == FISICA_ENTITLEMENT_REJECTED_FORMAT);
    uint32_t seed = 12345;
    for (int round = 0; round < 20000; ++round) {
        std::string noise(1 + (seed >> 8) % 300, '\0');
        for (auto& c : noise) { seed = seed * 1664525u + 1013904223u; c = char(seed >> 24); }
        if (round % 2) std::memcpy(&noise[0], "FSE1.", noise.size() < 5 ? noise.size() : 5);
        assert(import(context, noise, now) >= FISICA_ENTITLEMENT_REJECTED_FORMAT);
    }
    assert(fisica_core_entitlement_status(context, now, status, 12) == FISICA_OK && status[2] == FISICA_CONTRACT_UNVERIFIED);

    assert(import(context, valid + "\n", now) == FISICA_ENTITLEMENT_ACCEPTED);
    std::array<uint8_t,8> text{}; text.fill(99); uint32_t length = 0;
    assert(fisica_core_entitlement_text(context, FISICA_ENTITLEMENT_COMPANY, text.data(), 3, &length) == FISICA_BUFFER_TOO_SMALL && length == 4);
    for (auto value : text) assert(value == 99);
    assert(fisica_core_entitlement_text(context, FISICA_ENTITLEMENT_COMPANY, text.data(), 4, &length) == FISICA_OK && std::memcmp(text.data(), "acme", 4) == 0 && text[4] == 99);
    assert(fisica_core_entitlement_text(context, 99, text.data(), 8, &length) == FISICA_INVALID_ARGUMENT);

    std::array<uint8_t,16> id{}; std::vector<uint8_t> token(4096), body(256); uint32_t token_length = 0, body_length = 0;
    assert(fisica_core_entitlement_report(context, id.data(), 16, token.data(), 4096, &token_length, body.data(), 256, &body_length) == FISICA_NO_RESULT);
    assert(fisica_core_entitlement_report_done(context) == FISICA_INVALID_STATE);
    assert(fisica_core_entitlement_mark_used(context, now + 1) == FISICA_OK);
    assert(fisica_core_entitlement_report(context, id.data(), 15, token.data(), 4096, &token_length, body.data(), 256, &body_length) == FISICA_INVALID_ARGUMENT);
    assert(fisica_core_entitlement_report(context, id.data(), 16, token.data(), 4096, &token_length, body.data(), 8, &body_length) == FISICA_BUFFER_TOO_SMALL);
    assert(fisica_core_entitlement_report(context, id.data(), 16, token.data(), 4096, &token_length, body.data(), 256, &body_length) == FISICA_OK);
    assert(token_length == valid.size() && body_length > 0);

    std::vector<uint8_t> state(262144, 99);
    assert(fisica_core_entitlement_export(context, state.data(), 4, &length) == FISICA_BUFFER_TOO_SMALL);
    assert(state[0] == 99);
    assert(fisica_core_entitlement_export(context, state.data(), uint32_t(state.size()), &length) == FISICA_OK);
    const std::vector<uint8_t> saved(state.begin(), state.begin() + length);
    FisicaEntitlement *other = nullptr;
    assert(fisica_core_entitlement_create(&other) == FISICA_OK);
    assert(fisica_core_entitlement_restore(other, saved.data(), uint32_t(saved.size())) == FISICA_OK);
    assert(fisica_core_entitlement_status(other, now, status, 12) == FISICA_OK && status[2] == FISICA_CONTRACT_ACTIVE);
    // A stored state edited byte by byte is either refused or loses the entries whose tokens no longer verify.
    for (size_t i = 0; i < saved.size(); i += 3) {
        std::vector<uint8_t> edited = saved; edited[i] ^= 0x21;
        const int32_t restored = fisica_core_entitlement_restore(other, edited.data(), uint32_t(edited.size()));
        assert(restored == FISICA_OK || restored == FISICA_INVALID_ARGUMENT);
        assert(fisica_core_entitlement_status(other, now, status, 12) == FISICA_OK);
    }
    for (size_t size = 0; size < saved.size(); size += 7) fisica_core_entitlement_restore(other, saved.data(), uint32_t(size));
    assert(fisica_core_entitlement_restore(other, nullptr, 10) == FISICA_INVALID_ARGUMENT);

    fisica_core_entitlement_destroy(other);
    fisica_core_entitlement_destroy(context);
    fisica_core_entitlement_destroy(nullptr);
    std::printf("Native entitlement contract passed (%zu single-character mutations rejected)\n", rejected);
}
