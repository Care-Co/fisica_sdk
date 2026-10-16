#ifndef FISICA_CORE_H
#define FISICA_CORE_H

#include <stdint.h>

#if defined(_WIN32)
# if defined(FISICA_CORE_STATIC)
#  define FISICA_API
# elif defined(FISICA_CORE_BUILD)
#  define FISICA_API __declspec(dllexport)
# else
#  define FISICA_API __declspec(dllimport)
# endif
#else
# define FISICA_API __attribute__((visibility("default")))
#endif

#ifdef __cplusplus
extern "C" {
#endif

/* ABI 2: caller owns all buffers; lengths/capacities count elements, not bytes.
 * Returned strings are immutable, library-owned UTF-8. No Python dependency.
 * Functions do not retain input/output pointers. Independent calls are thread-safe.
 * Incompatible ABI changes require a new major before reading/writing outputs.
 * ABI 2 adds licensing: a server-signed SDK key decides between full and basic mode,
 * and the core itself limits what it hands out in basic mode (FisicaEntitlement below). */
typedef struct FisicaEntitlement FisicaEntitlement;
enum FisicaStatus {
    FISICA_OK = 0,
    FISICA_INVALID_ARGUMENT = 1,
    FISICA_BUFFER_TOO_SMALL = 2,
    FISICA_NO_RESULT = 3,
    FISICA_UNKNOWN_COMMAND = 4,
    FISICA_INTERNAL_ERROR = 5,
    FISICA_INVALID_STATE = 6,
    FISICA_BUSY = 7,
    FISICA_COMMAND_TIMEOUT = 8,
    FISICA_DEVICE_REJECTED = 9,
    FISICA_CANCELLED = 10,
    FISICA_TRANSPORT_LOST = 11,
    FISICA_INCOMPATIBLE_VERSION = 12,
    FISICA_FEATURE_NOT_LICENSED = 13
};
enum FisicaPacketKind { FISICA_PACKET_NONE = 0, FISICA_PACKET_MEASUREMENT = 1, FISICA_PACKET_RESPONSE = 2 };
/* Input limits enforced by the calls below. ANALYSIS_FRAMES: most packets/matrices one
 * analysis accepts. FEED_BYTES: most bytes per session_feed call. TOKEN_BYTES: longest
 * entitlement token. Hosts that collect frames keep at most ANALYSIS_FRAMES of them.
 * STATUS_FIELDS: length of the entitlement status array. BASIC_FRAME_INTERVAL_MS: in basic
 * mode a session hands out at most one measurement frame per this many milliseconds.
 * EVENT_BYTES: a session_poll payload buffer this large holds every event. */
enum FisicaLimit {
    FISICA_LIMIT_ANALYSIS_FRAMES = 19949, FISICA_LIMIT_FEED_BYTES = 65536, FISICA_LIMIT_TOKEN_BYTES = 4096,
    FISICA_LIMIT_STATUS_FIELDS = 19, FISICA_LIMIT_BASIC_FRAME_INTERVAL_MS = 1000, FISICA_LIMIT_EVENT_BYTES = 16384
};

FISICA_API const char *fisica_core_version(void);
FISICA_API uint32_t fisica_core_abi_version(void); /* major << 16 | minor */
FISICA_API const char *fisica_core_policy_json(void);
FISICA_API const char *fisica_core_error_message(int32_t status);

/* ABI 1.3: shared offline wrapper policy. No numeric/session calls before success.
 * warnings bit 1: SDK deprecated, bit 2: wrapper deprecated. Stable SemVer only.
 * Uses the registered wrapper's core/ABI/schema/support requirements. */
FISICA_API int32_t fisica_core_check_wrapper(const char *name, const char *sdk_version,
    const char *wrapper_version, uint32_t *warnings);

/* Packet: 645 bytes. Raw grid: 638 doubles (29x22), display: 841 (29x29).
 * metrics: [weight_kg, battery_percent]. Measurement packets carry no battery field, so
 * battery is the legacy placeholder 99, NOT a hardware reading, in result schema 1.
 * Read the real level with the GET_BATTERY_LEVEL query (session_command); its RESULT
 * payload's data bytes are the percent.
 * ABI 2: these three return raw or processed grids, so they need a license whose
 * features include RAW_DATA at now_s (wall-clock Unix seconds); otherwise they return
 * FISICA_FEATURE_NOT_LICENSED. A session hands out display grids in every mode. */
FISICA_API int32_t fisica_core_decode_packet(FisicaEntitlement *license, uint64_t now_s,
    const uint8_t *packet, uint32_t length,
    double *raw, uint32_t raw_capacity, double *display, uint32_t display_capacity,
    double *metrics, uint32_t metrics_capacity);
FISICA_API int32_t fisica_core_preprocess(FisicaEntitlement *license, uint64_t now_s,
    const double *grid, uint32_t rows, uint32_t cols, double *output, uint32_t capacity);
FISICA_API int32_t fisica_core_display_grid(FisicaEntitlement *license, uint64_t now_s,
    const double *grid, uint32_t gap_cols, double *output, uint32_t capacity);

/* One native framing step. consumed includes skipped leading bytes and packet.
 * NONE consumes only proven noise; keep unconsumed bytes for the next chunk.
 * A measurement uses the existing dual-footer 645-byte layout. */
FISICA_API int32_t fisica_core_next_packet(const uint8_t *buffer, uint32_t length,
    uint32_t *consumed, uint32_t *kind, uint8_t *packet, uint32_t capacity, uint32_t *packet_length);
/* response: [header, type, ret, data_high, data_low, success] */
FISICA_API int32_t fisica_core_decode_response(const uint8_t *packet, uint32_t length,
    int32_t *response, uint32_t capacity);
/* ABI 1.1: payload bytes for fixed or variable-length AT query responses.
 * For variable responses data_high/data_low are the first two payload bytes,
 * not a numeric value. Maximum framed response length is 4096 bytes. */
FISICA_API int32_t fisica_core_response_payload(const uint8_t *packet, uint32_t length,
    uint8_t *output, uint32_t capacity, uint32_t *output_length);

/* Input matrices: count*rows*cols row-major doubles; weights: count doubles.
 * Result: [weight_kg, left_width_cm, left_length_cm, right_width_cm, right_length_cm].
 * Preserves legacy stabilization policy and nominal 1cm cells; not clinical calibration.
 * Weight and foot size are basic-mode results: analysis needs no license. A session
 * analyzes every packet it received with fisica_core_session_analyze. */
FISICA_API int32_t fisica_core_analyze(const double *matrices, const double *weights,
    uint32_t count, uint32_t rows, uint32_t cols, double *result, uint32_t capacity);
/* ABI 1.4: the same analysis from measurement packets exactly as received
 * (count * 645 bytes), for hosts that only keep MEASUREMENT payloads. Each packet
 * is decoded to its 29x29 display grid and weight; the 29x22 raw grid is not used.
 * Same result and limits as fisica_core_analyze. */
FISICA_API int32_t fisica_core_analyze_packets(const uint8_t *packets, uint32_t count,
    double *result, uint32_t capacity);
/* xy contains count coordinate pairs. Result: width,length,cx,cy then 4 corner xy pairs. */
FISICA_API int32_t fisica_core_dimensions(const double *xy, uint32_t count,
    double cell_width, double cell_height, double *result, uint32_t capacity);
FISICA_API int32_t fisica_core_cop(const double *grid, uint32_t rows, uint32_t cols,
    double cell_width, double cell_height, double *result, uint32_t capacity);

/* Only documented named commands. output_length is required, including on a capacity error.
 * Command bytes are observable at the OS/device boundary; this API is not device authorization. */
FISICA_API int32_t fisica_core_command(const char *name, double value,
    const uint8_t *payload, uint32_t payload_length, uint8_t *output,
    uint32_t capacity, uint32_t *output_length);

/* ABI 1.2: Sans-IO session. Opaque handle owns state, framing and pending ACK.
 * Serialize all calls to ONE handle, including destroy; different handles are
 * independent. Inputs are copied. No clocks, threads, transports or callbacks.
 * now_ms: nondecreasing host monotonic milliseconds, <= INT64_MAX. Tick regularly
 * and drain events after every call. ACK deadline: 2000 ms. Never auto-retries.
 * State-changing timeout or rejection => FAULT: close/reopen transport before further commands.
 * A GET_* query (e.g. GET_BATTERY_LEVEL) that is rejected or unanswered only reports its status
 * in RESULT; the session state is unchanged, as for the firmware probe.
 * Protocol has no request ID: stale same-header ACKs cannot be authenticated.
 * Event metadata: [kind,state,status,device_error,operation,header,type,length].
 * TX payload must be sent in order; report send/read failures via LOST.
 * RESULT payload is the matching response; device_error retains the 16-bit NACK.
 * poll with insufficient capacity leaves the event queued (length is returned).
 * Feed <=65536 bytes/call; drain to avoid the bounded 1024-event queue overflowing.
 * V1 text commands and reserved session mode/start/stop commands are not accepted
 * by session_command; stateless command() remains available for legacy users.
 * ABI 2 MEASUREMENT payload: 842 little-endian IEEE-754 doubles [weight_kg, 841 display
 * cells (29x29)]; only while the session's license includes RAW_DATA, then 638 doubles
 * (raw 29x22 grid) and the 645-byte packet. Its header/type metadata are 0. Without a full-mode license the session
 * hands out at most one MEASUREMENT per BASIC_FRAME_INTERVAL_MS; every packet still
 * counts toward fisica_core_session_analyze. */
typedef struct FisicaSession FisicaSession;
enum FisicaSessionState {
    FISICA_DISCONNECTED = 0, FISICA_IDLE = 1, FISICA_STARTING = 2,
    FISICA_MEASURING = 3, FISICA_STOPPING = 4, FISICA_RELEASING = 5,
    FISICA_FAULT = 6, FISICA_RECOVERING = 7
};
enum FisicaSessionAction {
    FISICA_CONNECTED = 1, FISICA_START = 2, FISICA_STOP = 3,
    FISICA_DISCONNECT = 4, FISICA_LOST = 5
};
enum FisicaSessionEvent {
    FISICA_TRANSMIT = 1, FISICA_STATE = 2, FISICA_RESULT = 3,
    FISICA_MEASUREMENT = 4, FISICA_RESPONSE = 5, FISICA_RECONNECT = 6
};
enum FisicaSessionOperation {
    FISICA_PROBE = 1, FISICA_BEGIN = 2, FISICA_END = 3,
    FISICA_CLOSE = 4, FISICA_REQUEST = 5
};
/* CONNECTED value: the Scale connection setting to use for this link. */
enum FisicaLink { FISICA_LINK_BLE = 1, FISICA_LINK_USB = 2 };
FISICA_API int32_t fisica_core_session_create(FisicaSession **session);
FISICA_API void fisica_core_session_destroy(FisicaSession *session);
/* CONNECTED value: FisicaLink (1 BLE, 2 USB); START value: duration ms, -1 unlimited.
 * LOST value: nonzero send failure (FAULT), zero read/link loss (RECOVERING).
 * On link recovery CONNECTED probes firmware then resumes only if core still
 * wants measurement; original duration deadline is preserved across recovery. */
FISICA_API int32_t fisica_core_session_action(FisicaSession *session,
    uint32_t action, int64_t value, uint64_t now_ms);
FISICA_API int32_t fisica_core_session_command(FisicaSession *session,
    const char *name, double value, const uint8_t *payload, uint32_t length, uint64_t now_ms);
FISICA_API int32_t fisica_core_session_feed(FisicaSession *session,
    const uint8_t *bytes, uint32_t length, uint64_t now_ms);
FISICA_API int32_t fisica_core_session_tick(FisicaSession *session, uint64_t now_ms);
FISICA_API int32_t fisica_core_session_poll(FisicaSession *session,
    uint32_t *event, uint32_t event_capacity, uint8_t *payload, uint32_t capacity);
/* ABI 2: copy the license decision of an entitlement context into the session (basic
 * until called; NULL license returns it to basic). A full-mode decision lasts until the
 * key or contract would end, counted on the session's monotonic clock. Call it again
 * after importing, refreshing or activating a key. Serialize with calls on both handles. */
FISICA_API int32_t fisica_core_session_license(FisicaSession *session,
    FisicaEntitlement *license, uint64_t now_s, uint64_t now_ms);
/* ABI 2: weight and foot size from every measurement packet since the last START, in
 * any mode. Same result layout and NO_RESULT rule as fisica_core_analyze. */
FISICA_API int32_t fisica_core_session_analyze(FisicaSession *session,
    double *result, uint32_t capacity);

/* ABI 2: license context. The same binary serves every company; no company, contract
 * or delivery identifier is compiled in. The host injects a server-signed SDK key
 * (online response or offline file) and wall-clock seconds; the core reads this app's
 * ID or this PC's hash itself (the target) and never takes it from the host.
 *   full mode  - a key activated for this target, not released, within its key expiry
 *                and its contract's full-mode end (trial, extension, or contract end +
 *                grace), on a clock that has not gone back more than an hour
 *   basic mode - everything else. Connection, measurement, settings, weight and foot
 *                size always work; raw data and full-rate frames need full mode.
 * A key is only attributed to a company/contract after its signature verifies. It does
 * not prove device authenticity or code origin. Serialize calls per handle.
 * No usage tracking: the context keeps the last verified key and the latest wall-clock
 * time it has seen (to notice a clock set back), and nothing about SDK use. */
enum FisicaEntitlementOutcome {
    FISICA_ENTITLEMENT_NONE = 0, FISICA_ENTITLEMENT_ACCEPTED = 1,
    FISICA_ENTITLEMENT_IGNORED_OLDER = 2, /* older confirmation of the same key */
    FISICA_ENTITLEMENT_REJECTED_FORMAT = 3, FISICA_ENTITLEMENT_REJECTED_KEY = 4,
    FISICA_ENTITLEMENT_REJECTED_SIGNATURE = 5,
    FISICA_ENTITLEMENT_REJECTED_TARGET = 6 /* activated for another app or PC */
};
enum FisicaMode { FISICA_MODE_BASIC = 1, FISICA_MODE_FULL = 2 };
/* Why the context is in basic mode; NONE in full mode. */
enum FisicaModeReason {
    FISICA_REASON_NONE = 0, FISICA_REASON_NO_KEY = 1, FISICA_REASON_LEGACY_TOKEN = 2,
    FISICA_REASON_NOT_ACTIVATED = 3, FISICA_REASON_NO_TARGET = 4, FISICA_REASON_OTHER_TARGET = 5,
    FISICA_REASON_REVOKED = 6, FISICA_REASON_CLOCK_ROLLBACK = 7, FISICA_REASON_KEY_EXPIRED = 8,
    FISICA_REASON_NOT_STARTED = 9, FISICA_REASON_FULL_ENDED = 10
};
/* Features open now. A key's "features" member narrows RAW_DATA/EXPORT/ADVANCED; empty
 * opens all three. FULL_RATE (every frame) comes with full mode. */
enum FisicaFeature {
    FISICA_FEATURE_RAW_DATA = 1, FISICA_FEATURE_EXPORT = 2, FISICA_FEATURE_ADVANCED = 4,
    FISICA_FEATURE_FULL_RATE = 8
};
enum FisicaContract {
    FISICA_CONTRACT_UNVERIFIED = 0, FISICA_CONTRACT_ACTIVE = 1, FISICA_CONTRACT_NOT_STARTED = 2,
    FISICA_CONTRACT_ENDED = 3, FISICA_CONTRACT_REVOKED = 4
};
enum FisicaContractKind { FISICA_CONTRACT_KIND_NONE = 0, FISICA_CONTRACT_KIND_BASIC = 1, FISICA_CONTRACT_KIND_SPECIAL = 2 };
enum FisicaTrack { FISICA_TRACK_NONE = 0, FISICA_TRACK_TRIAL = 1, FISICA_TRACK_EXTENSION = 2, FISICA_TRACK_CONTRACT = 3 };
enum FisicaBindType { FISICA_BIND_NONE = 0, FISICA_BIND_APP = 1, FISICA_BIND_PC = 2 };
enum FisicaKeyEnvironment { FISICA_KEY_NONE = 0, FISICA_KEY_PRODUCTION = 1, FISICA_KEY_TEST = 2 };
/* RENEW_DUE: refresh online (or import a renewal file) soon or now. GRACE: the contract
 * ended and full mode continues until full_until. FULL_ENDING: full mode ends within 7 days. */
enum FisicaEntitlementWarning {
    FISICA_WARN_RENEW_DUE = 1, FISICA_WARN_GRACE = 2, FISICA_WARN_FULL_ENDING = 4,
    FISICA_WARN_IMPORT_REJECTED = 8, FISICA_WARN_TEST_KEY = 16
};
/* ACTIVATION_REQUEST: JSON body for POST /sdk/v1/entitlement/activate with this target.
 * ACTIVATION_CODE: FAC1 code for activating the current unactivated key on another device.
 * Both return FISICA_INVALID_STATE when the core cannot read a target or (code) when no
 * unactivated key is loaded. */
enum FisicaEntitlementText {
    FISICA_ENTITLEMENT_COMPANY = 1, FISICA_ENTITLEMENT_CONTRACT = 2, FISICA_ENTITLEMENT_GRANTS = 3,
    FISICA_ENTITLEMENT_SIGNING_KEY = 4, FISICA_ENTITLEMENT_TOKEN = 5, FISICA_ENTITLEMENT_KEY = 6,
    FISICA_ENTITLEMENT_BIND = 7, FISICA_ENTITLEMENT_TARGET = 8, FISICA_ENTITLEMENT_FEATURES = 9,
    FISICA_ENTITLEMENT_ACTIVATION_REQUEST = 10, FISICA_ENTITLEMENT_ACTIVATION_CODE = 11
};
FISICA_API int32_t fisica_core_entitlement_create(FisicaEntitlement **context);
FISICA_API void fisica_core_entitlement_destroy(FisicaEntitlement *context);
/* Token: "FSE1.<base64url payload>.<base64url Ed25519 signature>", at most 4096 bytes.
 * Returns OK for any well-formed call; *outcome says what happened. A rejected or older
 * key, or one activated for another target, leaves the current key untouched. */
FISICA_API int32_t fisica_core_entitlement_import(FisicaEntitlement *context,
    const uint8_t *token, uint32_t length, uint64_t now_s, uint32_t *outcome);
/* status[STATUS_FIELDS]: [mode, reason, features, contract, contract_kind, track,
 * bind_type, target_type, verified_at, refresh_by, key_until, full_until, contract_start,
 * contract_end, revoked_at, last_import_outcome, key_environment, warnings, latest_time].
 * Times are Unix seconds; 0 means not set. bind_type: the key's; target_type: this
 * app's or PC's (NONE when the core cannot read it). */
FISICA_API int32_t fisica_core_entitlement_status(FisicaEntitlement *context,
    uint64_t now_s, uint64_t *status, uint32_t capacity);
FISICA_API int32_t fisica_core_entitlement_text(FisicaEntitlement *context, uint32_t field,
    uint8_t *output, uint32_t capacity, uint32_t *length);
/* Release the current activated key on this target: the core forgets the key and returns
 * the FRC1 release code to enter in the developer portal. Nothing changes when the
 * output is too small. FISICA_INVALID_STATE without a key activated for this target. */
FISICA_API int32_t fisica_core_entitlement_release(FisicaEntitlement *context,
    uint8_t *output, uint32_t capacity, uint32_t *length);
/* Opaque text state for the host to store: the verified key and the latest time seen,
 * so the license can be checked offline. It is not a record of SDK use. Restore
 * verifies the stored key again. */
FISICA_API int32_t fisica_core_entitlement_export(FisicaEntitlement *context,
    uint8_t *output, uint32_t capacity, uint32_t *length);
FISICA_API int32_t fisica_core_entitlement_restore(FisicaEntitlement *context,
    const uint8_t *state, uint32_t length);

#ifdef __cplusplus
}
#endif
#endif
