/* Hardware-free C ABI example with the same steps as every partner sample: core version, the license,
 * connect, read the battery, measure for 5 seconds, stop, show the report, disconnect.
 * Supply a synthetic 645-byte frame as hex text and, optionally, an SDK key file from the developer
 * portal (activated for this PC). The scripted replies below (those of scale-mock.json) stand in for a
 * Scale: send the core's bytes to your OS transport and feed back what it receives instead, and pass
 * your monotonic clock instead of the simulated one.
 * All packet decoding, session state, deadlines, licensing and analysis run in the core.
 * Without a key activated here the core runs in basic mode: at most one frame a second,
 * no raw data; weight and foot size are still computed from every packet. */
#include "fisica_core.h"
#include <stdio.h>
#include <string.h>
#include <time.h>

#define FRAME_BYTES 645
#define FRAME_COUNT 12

/* The synthetic Scale: the replies of scale-mock.json; START is followed by FRAME_COUNT frames. */
static const char *const script[][2] = {
    { "GET_FIRMWARE_VERSION", "24434701322e302e31fff0" }, { "GET_BATTERY_LEVEL", "243147010050fff0" },
    { "SET_DEVICE_USB", "242253010000fff0" }, { "START_CONTINUOUS_MEASUREMENT", "245153010000fff0" },
    { "STOP_CONTINUOUS_MEASUREMENT", "245153010000fff0" }, { "SET_DEVICE_STOP", "242253010000fff0" },
};
/* Reason names as the other samples show them (enum FisicaModeReason). */
static const char *const reasons[] = { "", "no_key", "legacy_token", "not_activated", "no_target", "other_target",
    "revoked", "clock_rollback", "key_expired", "not_started", "full_ended" };

typedef struct {
    FisicaSession *session;
    uint64_t now;            /* simulated monotonic milliseconds */
    uint8_t frame[FRAME_BYTES];
    char firmware[64];
    int battery;             /* percent, -1 until read */
    unsigned frames;         /* frames the session handed out */
} Scale;

/* One clear line for a failed call; NO_RESULT is not a failure. */
static int ok(int status) {
    if (status == FISICA_OK || status == FISICA_NO_RESULT) return 1;
    fprintf(stderr, "Error: %s\n", fisica_core_error_message(status));
    return 0;
}

static size_t from_hex(const char *text, uint8_t *bytes, size_t capacity) {
    size_t length = 0;
    unsigned byte;
    while (length < capacity && sscanf(text + 2 * length, "%2x", &byte) == 1) bytes[length++] = (uint8_t)byte;
    return length;
}

/* Answer one command the core sends, as the Scale would. */
static int answer(Scale *scale, const uint8_t *request, uint32_t length) {
    for (size_t i = 0; i < sizeof(script) / sizeof(script[0]); ++i) {
        uint8_t command[32], reply[64];
        uint32_t command_length = 0;
        if (!ok(fisica_core_command(script[i][0], 0, NULL, 0, command, sizeof(command), &command_length))) return 0;
        if (command_length != length || memcmp(command, request, length) != 0) continue;
        if (!ok(fisica_core_session_feed(scale->session, reply, (uint32_t)from_hex(script[i][1], reply, sizeof(reply)), scale->now)))
            return 0;
        if (strcmp(script[i][0], "START_CONTINUOUS_MEASUREMENT") == 0)
            for (unsigned frame = 0; frame < FRAME_COUNT; ++frame)
                if (!ok(fisica_core_session_feed(scale->session, scale->frame, FRAME_BYTES, scale->now))) return 0;
        return 1;
    }
    return 1;  /* no scripted reply: the core's deadline reports it */
}

/* Handle everything the core produced: bytes to send, frames and results. 0 after a failure. */
static int drain(Scale *scale) {
    static uint8_t payload[FISICA_LIMIT_EVENT_BYTES];
    for (;;) {
        uint32_t event[8];  /* kind, state, status, device_error, operation, header, type, length */
        int status = fisica_core_session_poll(scale->session, event, 8, payload, sizeof(payload));
        if (status == FISICA_NO_RESULT) return 1;
        if (!ok(status)) return 0;
        const uint32_t length = event[7];
        if (event[0] == FISICA_TRANSMIT) {
            if (!answer(scale, payload, length)) return 0;
        } else if (event[0] == FISICA_MEASUREMENT) {
            /* [weight, 29x29 display grid] as doubles; raw grid and packet follow in full mode. */
            ++scale->frames;
        } else if (event[0] == FISICA_RESULT && event[4] == FISICA_PROBE) {
            /* The firmware probe; an unanswered one does not prevent use. */
            uint8_t version[sizeof(scale->firmware) - 1];
            uint32_t version_length = 0;
            if (event[2] == FISICA_OK && fisica_core_response_payload(payload, length, version, sizeof(version), &version_length) == FISICA_OK) {
                memcpy(scale->firmware, version, version_length);
                scale->firmware[version_length] = '\0';
            }
        } else if (event[0] == FISICA_RESULT && event[4] == FISICA_REQUEST) {
            /* GET_BATTERY_LEVEL: [header, type, ret, data_high, data_low, success]; a refusal leaves it unread. */
            int32_t response[6];
            if (event[2] == FISICA_OK && fisica_core_decode_response(payload, length, response, 6) == FISICA_OK &&
                response[5] && (response[3] << 8 | response[4]) <= 100)
                scale->battery = response[3] << 8 | response[4];
        } else if (event[0] == FISICA_RESULT && event[2] != FISICA_OK) {
            fprintf(stderr, "Error: %s\n", fisica_core_error_message((int32_t)event[2]));
            return 0;
        }
    }
}

static int act(Scale *scale, uint32_t action, int64_t value) {
    scale->now += 10;
    return ok(fisica_core_session_action(scale->session, action, value, scale->now)) && drain(scale);
}

/* Connect, read the battery, measure for 5 seconds, stop and show the report. 0 on success. */
static int measure(Scale *scale, int *connected) {
    if (!act(scale, FISICA_CONNECTED, FISICA_LINK_USB)) return 1;
    *connected = 1;
    printf("Connected to mock (firmware %s)\n", scale->firmware[0] ? scale->firmware : "unknown");
    /* Measurement packets carry no battery field: ask the Scale; the report shows this reading. */
    scale->now += 10;
    if (!ok(fisica_core_session_command(scale->session, "GET_BATTERY_LEVEL", 0, NULL, 0, scale->now)) || !drain(scale)) return 1;
    puts("Measuring for 5 seconds...");
    if (!act(scale, FISICA_START, -1)) return 1;
    scale->now += 5000;  /* a real host waits here and calls fisica_core_session_tick every few milliseconds */
    if (!act(scale, FISICA_STOP, 0)) return 1;
    double report[5];  /* weight kg; left width, left length, right width, right length in cm */
    const int status = fisica_core_session_analyze(scale->session, report, 5);
    if (!ok(status)) return 1;
    if (status == FISICA_NO_RESULT) {
        puts("No stable result; check the load on the Scale and the received frames");
        return 0;
    }
    /* Weight and foot size come from every packet, in basic and full mode alike. */
    printf("Weight: %.1f kg\n", report[0]);
    printf("Left foot: length %.1f cm, width %.1f cm\n", report[2], report[1]);
    printf("Right foot: length %.1f cm, width %.1f cm\n", report[4], report[3]);
    printf("Frames: %u\n", scale->frames);
    if (scale->battery >= 0) printf("Battery: %d%%\n", scale->battery);
    else puts("Battery: not read");
    return 0;
}

/* Imports the SDK key file, if any, and shows the license line. Online apps also activate and
 * renew the key through the developer portal API; the wrappers do that and add a sentence that
 * explains each reason (see the SDK documentation). */
static FisicaEntitlement *license_from(const char *path, uint64_t now_s) {
    FisicaEntitlement *license = NULL;
    uint64_t status[FISICA_LIMIT_STATUS_FIELDS];
    if (!ok(fisica_core_entitlement_create(&license))) return NULL;
    if (path) {
        uint8_t token[FISICA_LIMIT_TOKEN_BYTES];
        uint32_t outcome = 0;
        FILE *file = fopen(path, "rb");
        if (!file) {
            fprintf(stderr, "Error: cannot read the SDK key file %s\n", path);
            fisica_core_entitlement_destroy(license);
            return NULL;
        }
        const size_t size = fread(token, 1, sizeof(token), file);
        fclose(file);
        if (!ok(fisica_core_entitlement_import(license, token, (uint32_t)size, now_s, &outcome))) {
            fisica_core_entitlement_destroy(license);
            return NULL;
        }
    }
    if (!ok(fisica_core_entitlement_status(license, now_s, status, FISICA_LIMIT_STATUS_FIELDS))) {
        fisica_core_entitlement_destroy(license);
        return NULL;
    }
    if (status[0] == FISICA_MODE_FULL) puts("License: full mode");
    else printf("License: basic mode (%s)\n", status[1] < sizeof(reasons) / sizeof(reasons[0]) ? reasons[status[1]] : "unknown");
    return license;
}

static int read_frame(const char *path, uint8_t *frame) {
    FILE *input = fopen(path, "r");
    if (!input) {
        fprintf(stderr, "Error: cannot read %s\n", path);
        return 0;
    }
    unsigned i = 0, byte;
    while (i < FRAME_BYTES && fscanf(input, "%2x", &byte) == 1) frame[i++] = (uint8_t)byte;
    fclose(input);
    if (i != FRAME_BYTES) fprintf(stderr, "Error: %s does not hold a 645-byte frame as hex text\n", path);
    return i == FRAME_BYTES;
}

int main(int argc, char **argv) {
    if (argc != 2 && argc != 3) {
        fprintf(stderr, "Usage: fisica-c sample-frame.hex [sdk-key.fse]\n");
        return 2;
    }
    Scale scale;
    memset(&scale, 0, sizeof(scale));
    scale.battery = -1;
    if (!read_frame(argv[1], scale.frame)) return 1;
    const uint32_t abi = fisica_core_abi_version();
    printf("Fisica core %s (C ABI %u.%u)\n", fisica_core_version(), (unsigned)(abi >> 16), (unsigned)(abi & 0xffff));
    const uint64_t now_s = (uint64_t)time(NULL);
    FisicaEntitlement *license = license_from(argc == 3 ? argv[2] : NULL, now_s);
    if (!license) return 1;
    int result = 1, connected = 0;
    if (ok(fisica_core_session_create(&scale.session)) && ok(fisica_core_session_license(scale.session, license, now_s, 0)))
        result = measure(&scale, &connected);
    if (connected) {  /* always, also after an error: the Scale leaves measurement mode */
        if (!act(&scale, FISICA_DISCONNECT, 0)) result = 1;
        puts("Disconnected");
    }
    fisica_core_session_destroy(scale.session);
    fisica_core_entitlement_destroy(license);
    return result;
}
