/* Hardware-free C ABI example. Supply a synthetic 645-byte frame as hex text.
 * Replace the scripted replies below with bytes from your OS transport.
 * All packet decoding, session state, deadlines and analysis run in the core. */
#include "fisica_core.h"
#include <stdio.h>
#include <string.h>

static int check(int status) {
    if (status != FISICA_OK && status != FISICA_NO_RESULT) {
        fprintf(stderr, "%d: %s\n", status, fisica_core_error_message(status));
        return 0;
    }
    return 1;
}

int main(int argc, char **argv) {
    FisicaSession *session = NULL;
    uint8_t frame[645], collected[12 * 645], start[32];
    uint32_t start_length = 0, count = 0;
    if (argc != 2) { fprintf(stderr, "Usage: fisica-c sample-frame.hex\n"); return 2; }
    FILE *input = fopen(argv[1], "r");
    if (!input) { perror(argv[1]); return 2; }
    for (unsigned i = 0; i < 645; ++i) {
        unsigned byte;
        if (fscanf(input, "%2x", &byte) != 1) { fclose(input); return 2; }
        frame[i] = (uint8_t)byte;
    }
    fclose(input);
    printf("Core: %s, ABI: %u\n", fisica_core_version(), fisica_core_abi_version());
    if (!check(fisica_core_command("START_CONTINUOUS_MEASUREMENT", 0, NULL, 0, start, sizeof(start), &start_length)) ||
        !check(fisica_core_session_create(&session))) return 1;
    int result = 0;
    const int actions[] = { FISICA_CONNECTED, FISICA_START, FISICA_STOP, FISICA_DISCONNECT };
    for (unsigned step = 0; step < 4; ++step) {
        if (!check(fisica_core_session_action(session, actions[step], step == 0 ? 2 : step == 1 ? -1 : 0, step))) { result = 1; break; }
        for (;;) {
            uint32_t meta[8]; uint8_t payload[4096];
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
            } else if (meta[0] == FISICA_MEASUREMENT && count < 12 && length == 645) {
                memcpy(collected + count++ * 645, payload, 645);
            } else if (meta[0] == FISICA_RESULT && meta[2] != FISICA_OK) {
                check(meta[2]); result = 1; break;
            }
        }
        if (result) break;
    }
    if (!result && count) {
        double report[5]; int status = fisica_core_analyze_packets(collected, count, report, 5);
        if (!check(status)) result = 1;
        else if (status == FISICA_OK) printf("Synthetic weight: %.2f kg, frames: %u\n", report[0], count);
        else puts("No stable result");
    }
    fisica_core_session_destroy(session);
    return result;
}
