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

/* ABI 1: caller owns all buffers; lengths/capacities count elements, not bytes.
 * Returned strings are immutable, library-owned UTF-8. No Python dependency.
 * Functions do not retain input/output pointers. Independent calls are thread-safe.
 * Incompatible ABI changes require a new major before reading/writing outputs. */
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
    FISICA_INCOMPATIBLE_VERSION = 12
};
enum FisicaPacketKind { FISICA_PACKET_NONE = 0, FISICA_PACKET_MEASUREMENT = 1, FISICA_PACKET_RESPONSE = 2 };

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
 * metrics: [weight_kg, battery_percent]. Battery 99 retains the legacy placeholder;
 * battery is NOT a validated hardware reading in result schema 1. */
FISICA_API int32_t fisica_core_decode_packet(const uint8_t *packet, uint32_t length,
    double *raw, uint32_t raw_capacity, double *display, uint32_t display_capacity,
    double *metrics, uint32_t metrics_capacity);
FISICA_API int32_t fisica_core_preprocess(const double *grid, uint32_t rows, uint32_t cols,
    double *output, uint32_t capacity);
FISICA_API int32_t fisica_core_display_grid(const double *grid, uint32_t gap_cols,
    double *output, uint32_t capacity);

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
 * Preserves legacy stabilization policy and nominal 1cm cells; not clinical calibration. */
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
 * State-changing timeout => FAULT: close/reopen transport before further commands.
 * Protocol has no request ID: stale same-header ACKs cannot be authenticated.
 * Event metadata: [kind,state,status,device_error,operation,header,type,length].
 * TX payload must be sent in order; report send/read failures via LOST.
 * RESULT payload is the matching response; device_error retains the 16-bit NACK.
 * poll with insufficient capacity leaves the event queued (length is returned).
 * Feed <=65536 bytes/call; drain to avoid the bounded 1024-event queue overflowing.
 * V1 text commands and reserved session mode/start/stop commands are not accepted
 * by session_command; stateless command() remains available for legacy users. */
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
FISICA_API int32_t fisica_core_session_create(FisicaSession **session);
FISICA_API void fisica_core_session_destroy(FisicaSession *session);
/* CONNECTED value: 1 BLE, 2 USB; START value: duration ms, -1 unlimited.
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

/* ABI 1.4: runtime entitlement context. The same binary serves every company; no
 * company, contract or delivery identifier is compiled in. The host injects a
 * server-signed token (online response or offline file) and wall-clock seconds.
 * NOTHING here gates SDK features: results are status and warnings only, and are
 * never returned as error statuses. Serialize calls per handle; handles are isolated.
 * Three independent facts:
 *   base license   - granted with genuine hardware, no date expiry (always GRANTED)
 *   verification   - how recently the server confirmed this info (30 days at most)
 *   contract       - state of the verified company's contract, by signed dates only
 * A token is only attributed to a company/contract after its signature verifies.
 * A token does not prove device authenticity, code origin or contract compliance. */
typedef struct FisicaEntitlement FisicaEntitlement;
enum FisicaEntitlementOutcome {
    FISICA_ENTITLEMENT_NONE = 0, FISICA_ENTITLEMENT_ACCEPTED = 1,
    FISICA_ENTITLEMENT_IGNORED_OLDER = 2, /* older confirmation of the same contract */
    FISICA_ENTITLEMENT_REJECTED_FORMAT = 3, FISICA_ENTITLEMENT_REJECTED_KEY = 4,
    FISICA_ENTITLEMENT_REJECTED_SIGNATURE = 5
};
enum FisicaBaseLicense { FISICA_BASE_LICENSE_GRANTED = 1 };
enum FisicaVerification {
    FISICA_VERIFICATION_NONE = 0, FISICA_VERIFICATION_FRESH = 1, FISICA_VERIFICATION_REFRESH_DUE = 2
};
enum FisicaContract {
    FISICA_CONTRACT_UNVERIFIED = 0, FISICA_CONTRACT_ACTIVE = 1, FISICA_CONTRACT_NOT_STARTED = 2,
    FISICA_CONTRACT_ENDED = 3, FISICA_CONTRACT_REVOKED = 4
};
enum FisicaContractKind { FISICA_CONTRACT_KIND_NONE = 0, FISICA_CONTRACT_KIND_BASIC = 1, FISICA_CONTRACT_KIND_SPECIAL = 2 };
enum FisicaKeyEnvironment { FISICA_KEY_NONE = 0, FISICA_KEY_PRODUCTION = 1, FISICA_KEY_TEST = 2 };
enum FisicaEntitlementWarning {
    FISICA_WARN_UNVERIFIED = 1, FISICA_WARN_REFRESH_DUE = 2, FISICA_WARN_CONTRACT_ENDED = 4,
    FISICA_WARN_CONTRACT_REVOKED = 8, FISICA_WARN_CONTRACT_NOT_STARTED = 16,
    FISICA_WARN_IMPORT_REJECTED = 32, FISICA_WARN_TEST_KEY = 64
};
enum FisicaEntitlementText {
    FISICA_ENTITLEMENT_COMPANY = 1, FISICA_ENTITLEMENT_CONTRACT = 2, FISICA_ENTITLEMENT_GRANTS = 3,
    FISICA_ENTITLEMENT_KEY_ID = 4, FISICA_ENTITLEMENT_TOKEN = 5
};
FISICA_API int32_t fisica_core_entitlement_create(FisicaEntitlement **context);
FISICA_API void fisica_core_entitlement_destroy(FisicaEntitlement *context);
/* Token: "FSE1.<base64url payload>.<base64url Ed25519 signature>", at most 4096 bytes.
 * Returns OK for any well-formed call; *outcome says what happened. A rejected or
 * older token leaves previously verified information untouched. Re-importing a file
 * cannot extend the confirmation window: it is derived from signed times only. */
FISICA_API int32_t fisica_core_entitlement_import(FisicaEntitlement *context,
    const uint8_t *token, uint32_t length, uint64_t now_s, uint32_t *outcome);
/* status[12]: [base_license, verification, contract, contract_kind, verified_at,
 * refresh_by, contract_start, contract_end, revoked_at, last_import_outcome,
 * key_environment, warnings]. Times are Unix seconds; 0 means not set. */
FISICA_API int32_t fisica_core_entitlement_status(FisicaEntitlement *context,
    uint64_t now_s, uint64_t *status, uint32_t capacity);
FISICA_API int32_t fisica_core_entitlement_text(FisicaEntitlement *context, uint32_t field,
    uint8_t *output, uint32_t capacity, uint32_t *length);
/* Call on real feature use (connect, measure, analyze), not on import or refresh.
 * Keeps only "used", the last local use time and whether use was observed while the
 * contract was ended/revoked. No counts. Does nothing without a verified contract. */
FISICA_API int32_t fisica_core_entitlement_mark_used(FisicaEntitlement *context, uint64_t now_s);
/* Next pending usage report: the token authenticates the company/contract to the
 * server and body is the JSON to send. report_id: 16 host-random bytes naming this
 * report for retry de-duplication only. A report stays frozen until report_done, so a
 * retry resends identical bytes. NO_RESULT when nothing is pending. */
FISICA_API int32_t fisica_core_entitlement_report(FisicaEntitlement *context,
    const uint8_t *report_id, uint32_t id_length, uint8_t *token, uint32_t token_capacity,
    uint32_t *token_length, uint8_t *body, uint32_t body_capacity, uint32_t *body_length);
FISICA_API int32_t fisica_core_entitlement_report_done(FisicaEntitlement *context);
/* Opaque text state for the host to store. Restore verifies every stored token again. */
FISICA_API int32_t fisica_core_entitlement_export(FisicaEntitlement *context,
    uint8_t *output, uint32_t capacity, uint32_t *length);
FISICA_API int32_t fisica_core_entitlement_restore(FisicaEntitlement *context,
    const uint8_t *state, uint32_t length);

#ifdef __cplusplus
}
#endif
#endif
