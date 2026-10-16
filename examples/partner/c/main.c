/* Hardware-free C ABI example. Supply a synthetic 645-byte frame as hex text and,
 * optionally, an SDK key file from the developer portal (activated for this PC).
 * Replace the scripted replies below with bytes from your OS transport.
 * All packet decoding, session state, deadlines, licensing and analysis run in the core.
 * Without a key activated here the core runs in basic mode: at most one frame a second,
 * no raw data; weight and foot size are still computed from every packet. */
#include "fisica_core.h"
#include <stdio.h>
#include <string.h>
#include <time.h>

static int check(int status) {
    if (status != FISICA_OK && status != FISICA_NO_RESULT) {
        fprintf(stderr, "%d: %s\n", status, fisica_core_error_message(status));
        return 0;
    }
    return 1;
}

/* Imports an SDK key file and prints the mode. Online apps also activate and renew it
 * through the developer portal API (see the SDK documentation). */
static FisicaEntitlement *license_from(const char *path, uint64_t now) {
    static const char *reasons[] = { "full", "no key", "contract token, not an SDK key", "not activated",
        "no app/PC identity", "activated for another app or PC", "released or revoked", "clock set back",
        "key expired", "contract not started", "trial or contract ended" };
    FisicaEntitlement *license = NULL;
    uint8_t token[FISICA_LIMIT_TOKEN_BYTES]; size_t size = 0; uint32_t outcome = 0;
    uint64_t status[FISICA_LIMIT_STATUS_FIELDS];
    if (!check(fisica_core_entitlement_create(&license))) return NULL;
    if (path) {
        FILE *file = fopen(path, "rb");
        if (!file) { perror(path); return license; }
        size = fread(token, 1, sizeof(token), file);
        fclose(file);
        check(fisica_core_entitlement_import(license, token, (uint32_t)size, now, &outcome));
    }
    if (check(fisica_core_entitlement_status(license, now, status, FISICA_LIMIT_STATUS_FIELDS)))
        printf("License: %s mode (%s)\n", status[0] == FISICA_MODE_FULL ? "full" : "basic",
               status[1] < sizeof(reasons) / sizeof(reasons[0]) ? reasons[status[1]] : "?");
    return license;
}

int main(int argc, char **argv) {
    FisicaSession *session = NULL;
    uint8_t frame[645], start[32];
    uint32_t start_length = 0, count = 0, raw = 0;
    if (argc != 2 && argc != 3) { fprintf(stderr, "Usage: fisica-c sample-frame.hex [sdk-key.fse]\n"); return 2; }
    FILE *input = fopen(argv[1], "r");
    if (!input) { perror(argv[1]); return 2; }
    for (unsigned i = 0; i < 645; ++i) {
        unsigned byte;
        if (fscanf(input, "%2x", &byte) != 1) { fclose(input); return 2; }
        frame[i] = (uint8_t)byte;
    }
    fclose(input);
    printf("Core: %s, ABI: %u\n", fisica_core_version(), fisica_core_abi_version());
    const uint64_t now = (uint64_t)time(NULL);
    FisicaEntitlement *license = license_from(argc == 3 ? argv[2] : NULL, now);
    if (!license || !check(fisica_core_command("START_CONTINUOUS_MEASUREMENT", 0, NULL, 0, start, sizeof(start), &start_length)) ||
        !check(fisica_core_session_create(&session)) || !check(fisica_core_session_license(session, license, now, 0))) return 1;
    int result = 0;
    const int actions[] = { FISICA_CONNECTED, FISICA_START, FISICA_STOP, FISICA_DISCONNECT };
    static uint8_t payload[FISICA_LIMIT_EVENT_BYTES];
    for (unsigned step = 0; step < 4; ++step) {
        if (!check(fisica_core_session_action(session, actions[step], step == 0 ? 2 : step == 1 ? -1 : 0, step))) { result = 1; break; }
        for (;;) {
            uint32_t meta[8];
            int status = fisica_core_session_poll(session, meta, 8, payload, sizeof(payload));
            if (status == FISICA_NO_RESULT) break;
            if (!check(status)) { result = 1; break; }
            uint32_t length = meta[7];
            if (meta[0] == FISICA_TRANSMIT) {
                /* Synthetic AT success reply, not an ACK inferred from an OS write. */
                uint8_t reply[] = { 0x24, payload[1], payload[2], 1, 0, 0, 0xff, 0xf0 };
                const uint8_t firmware[] = { 0x24, 0x43, 0x47, 1, '2', '.', '0', '.', '1', 0xff, 0xf0 };
                int probe = payload[1] == 0x43;
                if (!check(fisica_core_session_feed(session, probe ? firmware : reply, probe ? sizeof(firmware) : sizeof(reply), step))) { result = 1; break; }
                if (length == start_length && memcmp(payload, start, length) == 0) {
                    for (unsigned i = 0; i < 12; ++i) if (!check(fisica_core_session_feed(session, frame, sizeof(frame), step))) { result = 1; break; }
                }
            } else if (meta[0] == FISICA_MEASUREMENT) {
                /* [weight, 29x29 display grid] as doubles; raw grid and packet follow in full mode. */
                ++count;
                raw += length > 842 * 8;
            } else if (meta[0] == FISICA_RESULT && meta[2] != FISICA_OK) {
                check(meta[2]); result = 1; break;
            }
        }
        if (result) break;
    }
    if (!result) {
        double report[5]; int status = fisica_core_session_analyze(session, report, 5);
        if (!check(status)) result = 1;
        else if (status == FISICA_OK) printf("Synthetic weight: %.2f kg; frames handed out: %u (%u with raw data) of 12\n", report[0], count, raw);
        else puts("No stable result");
    }
    fisica_core_session_destroy(session);
    fisica_core_entitlement_destroy(license);
    return result;
}
