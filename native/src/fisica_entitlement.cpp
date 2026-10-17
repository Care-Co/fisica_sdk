// Runtime license: a server-signed SDK key decides between full and basic mode.
// A key is a seat. It is activated once for this app's ID or this PC's hash, which the core
// reads itself, and works in full mode only there, within its own expiry and its contract's
// full-mode end. The only state is the last verified key and the latest wall-clock time seen
// (to notice a clock set back); nothing records whether, when or how often the SDK is used.
#include "fisica_core.h"
#include "fisica_license.h"
#include "fisica_release.h"
#include "third_party/monocypher/monocypher-ed25519.h"

#include <algorithm>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

#if defined(__APPLE__)
#include <TargetConditionals.h>
#include <CoreFoundation/CoreFoundation.h>
#if TARGET_OS_OSX
#include <unistd.h>
#include <uuid/uuid.h>
#endif
#elif defined(_WIN32)
#ifndef NOMINMAX
#define NOMINMAX
#endif
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#if defined(_MSC_VER)
#pragma comment(lib, "advapi32.lib")
#endif
#endif

namespace {
constexpr uint64_t max_refresh_seconds = 30ull * 24 * 3600;
constexpr uint64_t clock_tolerance = 3600;          // a clock set back by less is a correction
constexpr uint64_t renew_window = 7ull * 24 * 3600; // renew a key this long before it expires
constexpr uint64_t ending_window = 7ull * 24 * 3600;
constexpr uint32_t max_token = FISICA_LIMIT_TOKEN_BYTES, max_state = 262144;
constexpr uint64_t max_time = 1ull << 53;
constexpr uint32_t all_features = FISICA_FEATURE_RAW_DATA | FISICA_FEATURE_EXPORT | FISICA_FEATURE_ADVANCED;

struct Token {
    std::string text, kid, company, contract, grants, key, bind, features_text;
    uint32_t kind = 0, environment = 0, bind_type = 0, track = 0, features = all_features;
    uint64_t verified_at = 0, refresh_by = 0, start = 0, end = 0, revoked_at = 0, key_until = 0, full_until = 0;
};
struct Target { uint32_t type = FISICA_BIND_NONE; std::string value; };

// SHA-256 (FIPS 180-4) for the PC hash and the activation/release code checks.
class Sha256 {
    uint32_t h[8] = {0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19};
    uint8_t block[64] = {};
    uint64_t length = 0;
    size_t used = 0;
    static uint32_t rotr(uint32_t x, int n) { return (x >> n) | (x << (32 - n)); }
    void compress() {
        static const uint32_t k[64] = {
            0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
            0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
            0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
            0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
            0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
            0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
            0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
            0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2};
        uint32_t w[64];
        for (int i = 0; i < 16; ++i)
            w[i] = uint32_t(block[4*i]) << 24 | uint32_t(block[4*i+1]) << 16 | uint32_t(block[4*i+2]) << 8 | block[4*i+3];
        for (int i = 16; i < 64; ++i) {
            const uint32_t s0 = rotr(w[i-15], 7) ^ rotr(w[i-15], 18) ^ (w[i-15] >> 3);
            const uint32_t s1 = rotr(w[i-2], 17) ^ rotr(w[i-2], 19) ^ (w[i-2] >> 10);
            w[i] = w[i-16] + s0 + w[i-7] + s1;
        }
        uint32_t a = h[0], b = h[1], c = h[2], d = h[3], e = h[4], f = h[5], g = h[6], x = h[7];
        for (int i = 0; i < 64; ++i) {
            const uint32_t t1 = x + (rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25)) + ((e & f) ^ (~e & g)) + k[i] + w[i];
            const uint32_t t2 = (rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22)) + ((a & b) ^ (a & c) ^ (b & c));
            x = g; g = f; f = e; e = d + t1; d = c; c = b; b = a; a = t1 + t2;
        }
        h[0] += a; h[1] += b; h[2] += c; h[3] += d; h[4] += e; h[5] += f; h[6] += g; h[7] += x;
    }
    void add(const uint8_t *data, size_t size) {
        while (size) {
            const size_t take = std::min(size, size_t(64) - used);
            std::memcpy(block + used, data, take);
            used += take; data += take; size -= take;
            if (used == 64) { compress(); used = 0; }
        }
    }
public:
    void update(const std::string& text) {
        length += text.size();
        add(reinterpret_cast<const uint8_t*>(text.data()), text.size());
    }
    std::string hex() {
        const uint64_t bits = length * 8;
        const uint8_t one = 0x80, zero = 0;
        add(&one, 1);
        while (used != 56) add(&zero, 1);
        uint8_t tail[8];
        for (int i = 0; i < 8; ++i) tail[i] = uint8_t(bits >> (56 - 8 * i));
        add(tail, 8);
        static const char digits[] = "0123456789abcdef";
        std::string out;
        for (uint32_t value : h) for (int shift = 28; shift >= 0; shift -= 4) out.push_back(digits[(value >> shift) & 15]);
        return out;
    }
};
std::string sha256_hex(const std::string& text) { Sha256 hash; hash.update(text); return hash.hex(); }

int base64_value(char c) {
    if (c >= 'A' && c <= 'Z') return c - 'A';
    if (c >= 'a' && c <= 'z') return c - 'a' + 26;
    if (c >= '0' && c <= '9') return c - '0' + 52;
    return c == '-' ? 62 : c == '_' ? 63 : -1;
}
bool base64url(const char *text, size_t length, std::vector<uint8_t>& output) {
    if (length % 4 == 1) return false;
    uint32_t buffer = 0; int bits = 0;
    for (size_t i = 0; i < length; ++i) {
        const int value = base64_value(text[i]);
        if (value < 0) return false;
        buffer = (buffer << 6) | uint32_t(value); bits += 6;
        if (bits >= 8) { bits -= 8; output.push_back(uint8_t(buffer >> bits)); buffer &= (1u << bits) - 1; }
    }
    return buffer == 0; // Canonical encoding only: no stray trailing bits.
}
std::string base64url_encode(const std::string& data) {
    static const char table[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
    std::string out; uint32_t buffer = 0; int bits = 0;
    for (unsigned char c : data) {
        buffer = (buffer << 8) | c; bits += 8;
        while (bits >= 6) { bits -= 6; out.push_back(table[(buffer >> bits) & 63]); }
        buffer &= (1u << bits) - 1;
    }
    if (bits) out.push_back(table[(buffer << (6 - bits)) & 63]);
    return out;
}
bool alnum(char c) { return (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9'); }
bool identifier(const std::string& value) {
    if (value.empty() || value.size() > 64) return false;
    return std::all_of(value.begin(), value.end(), [](char c) { return alnum(c) || c == '_' || c == '-' || c == '.' || c == ':'; });
}
// Reverse-DNS app ID, as the server checks it: [A-Za-z0-9][A-Za-z0-9_-]*(\.[A-Za-z0-9_-]+)+
bool app_id(const std::string& value) {
    if (value.empty() || value.size() > 128 || !alnum(value[0])) return false;
    bool dotted = false;
    for (size_t i = 0; i < value.size(); ++i) {
        const char c = value[i];
        if (c == '.') {
            if (value[i-1] == '.' || i + 1 == value.size()) return false;
            dotted = true;
        } else if (!alnum(c) && c != '_' && c != '-') return false;
    }
    return dotted;
}

}  // namespace
namespace fisica {
bool apk_mapped(const std::string& maps, const std::string& package) {
    if (package.empty()) return false;
    const std::string marker = "/" + package + "-";
    for (size_t begin = 0; begin < maps.size();) {
        size_t finish = maps.find('\n', begin);
        if (finish == std::string::npos) finish = maps.size();
        const std::string line = maps.substr(begin, finish - begin);
        begin = finish + 1;
        const size_t at = line.find(marker);
        if (at == std::string::npos) continue;
        const size_t slash = line.find('/', at + marker.size());
        if (slash != std::string::npos && line.compare(slash, std::string::npos, "/base.apk") == 0 &&
            line.find("/app/") != std::string::npos && line.find("/app/") < at + 1) return true;
    }
    return false;
}
}  // namespace fisica
namespace {
bool pc_hash(const std::string& value) {
    return value.size() == 64 && std::all_of(value.begin(), value.end(), [](char c) { return (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'); });
}
bool ends_with(const std::string& value, const char *suffix) {
    const size_t n = std::strlen(suffix);
    return value.size() >= n && value.compare(value.size() - n, n, suffix) == 0;
}
// A debug build (.debug/.dev) is the same app, as on the server.
std::string app_root(std::string value) {
    for (const char *suffix : {".debug", ".dev"})
        if (ends_with(value, suffix)) value.resize(value.size() - std::strlen(suffix));
    return value;
}
bool same_target(uint32_t type, const std::string& bind, const Target& target) {
    if (!type || type != target.type) return false;
    return type == FISICA_BIND_APP ? app_root(bind) == app_root(target.value) : bind == target.value;
}
const char *bind_name(uint32_t type) { return type == FISICA_BIND_APP ? "app" : type == FISICA_BIND_PC ? "pc" : ""; }

Target pc_target(const std::string& machine) {
    Target target;
    if (!machine.empty()) { target.type = FISICA_BIND_PC; target.value = sha256_hex("fisica-pc-v1:" + machine); }
    return target;
}
std::string trimmed(std::string value) {
    while (!value.empty() && (value.back() == '\n' || value.back() == '\r' || value.back() == ' ' || value.back() == '\0')) value.pop_back();
    return value;
}
#if defined(__APPLE__)
// An app bundle's ID. On macOS only a real .app counts, and not the Python interpreter or
// Apple's own tools (xctest): scripts and command-line programs are bound to the PC.
bool apple_app(std::string& out) {
    CFBundleRef bundle = CFBundleGetMainBundle();
    if (!bundle) return false;
    CFStringRef identifier = CFBundleGetIdentifier(bundle);
    char buffer[256];
    if (!identifier || !CFStringGetCString(identifier, buffer, sizeof buffer, kCFStringEncodingUTF8)) return false;
    const std::string id(buffer);
#if TARGET_OS_OSX
    CFURLRef url = CFBundleCopyBundleURL(bundle);
    if (!url) return false;
    char path[4096];
    const bool ok = CFURLGetFileSystemRepresentation(url, true, reinterpret_cast<UInt8*>(path), sizeof path);
    CFRelease(url);
    std::string location = ok ? std::string(path) : std::string();
    while (!location.empty() && location.back() == '/') location.pop_back();
    if (!ends_with(location, ".app") || id == "org.python.python" || id.rfind("com.apple.", 0) == 0) return false;
#endif
    if (!app_id(id)) return false;
    out = id;
    return true;
}
#endif
// The target is read here, never passed in by the host: wrapper sources are public.
Target read_target() noexcept {
    try {
#if defined(__APPLE__)
        std::string id;
        if (apple_app(id)) return {FISICA_BIND_APP, id};
#if TARGET_OS_OSX
        uuid_t uuid; struct timespec wait = {0, 0};
        if (gethostuuid(uuid, &wait) == 0) {
            uuid_string_t text; uuid_unparse_upper(uuid, text);
            return pc_target(text);
        }
#endif
#elif defined(_WIN32)
        HKEY key = nullptr;
        if (RegOpenKeyExA(HKEY_LOCAL_MACHINE, "SOFTWARE\\Microsoft\\Cryptography", 0, KEY_QUERY_VALUE | KEY_WOW64_64KEY, &key) == ERROR_SUCCESS) {
            char value[128] = {}; DWORD type = 0, size = sizeof value - 1;
            const LONG status = RegQueryValueExA(key, "MachineGuid", nullptr, &type, reinterpret_cast<LPBYTE>(value), &size);
            RegCloseKey(key);
            if (status == ERROR_SUCCESS && type == REG_SZ) return pc_target(trimmed(std::string(value, size)));
        }
#elif defined(__ANDROID__)
        // The process name can be set to another app's name (android:process in the manifest),
        // but the installed APK's path is chosen by the system.
        // So the process name (":" starts a named secondary process) counts only when this process
        // has that package's own APK mapped: /data/app/[~~<hash>/]<package>-<suffix>/base.apk.
        std::string name;
        if (FILE *file = std::fopen("/proc/self/cmdline", "rb")) {
            char buffer[256] = {};
            const size_t size = std::fread(buffer, 1, sizeof buffer - 1, file);
            std::fclose(file);
            name = std::string(buffer, strnlen(buffer, size));
            name = name.substr(0, name.find(':'));
        }
        if (FILE *file = std::fopen("/proc/self/maps", "rb")) {
            std::string maps; char chunk[4096]; size_t size;
            while ((size = std::fread(chunk, 1, sizeof chunk, file)) > 0 && maps.size() < (16u << 20)) maps.append(chunk, size);
            std::fclose(file);
            if (app_id(name) && fisica::apk_mapped(maps, name)) return {FISICA_BIND_APP, name};
        }
#else
        for (const char *path : {"/etc/machine-id", "/var/lib/dbus/machine-id"}) {
            if (FILE *file = std::fopen(path, "rb")) {
                char buffer[128] = {};
                const size_t size = std::fread(buffer, 1, sizeof buffer - 1, file);
                std::fclose(file);
                const std::string machine = trimmed(std::string(buffer, size));
                if (!machine.empty()) return pc_target(machine);
            }
        }
#endif
    } catch (...) {}
    return {};
}

// Strict flat JSON: string and non-negative integer members only, no escapes.
// A small closed grammar is safer here than a general parser on untrusted input.
bool parse_payload(const std::vector<uint8_t>& json, Token& token, uint64_t& version,
                   std::string& kind, std::string& bind_type, std::string& track) {
    size_t i = 0; const size_t n = json.size();
    const auto space = [&] { while (i < n && (json[i] == ' ' || json[i] == '\n' || json[i] == '\r' || json[i] == '\t')) ++i; };
    const auto text = [&](std::string& out) {
        if (i >= n || json[i] != '"') return false;
        for (++i; i < n && json[i] != '"'; ++i) {
            if (json[i] < 0x20 || json[i] > 0x7e || json[i] == '\\' || out.size() >= 512) return false;
            out.push_back(char(json[i]));
        }
        if (i >= n) return false;
        ++i; return true;
    };
    struct Field { const char *name; std::string *text; uint64_t *number; };
    const Field fields[] = {
        {"v", nullptr, &version}, {"kid", &token.kid, nullptr}, {"company", &token.company, nullptr},
        {"contract", &token.contract, nullptr}, {"kind", &kind, nullptr}, {"grants", &token.grants, nullptr},
        {"verified_at", nullptr, &token.verified_at}, {"refresh_by", nullptr, &token.refresh_by},
        {"contract_start", nullptr, &token.start}, {"contract_end", nullptr, &token.end},
        {"revoked_at", nullptr, &token.revoked_at}, {"key", &token.key, nullptr},
        {"bind_type", &bind_type, nullptr}, {"bind", &token.bind, nullptr},
        {"key_until", nullptr, &token.key_until}, {"full_until", nullptr, &token.full_until},
        {"features", &token.features_text, nullptr}, {"track", &track, nullptr},
    };
    uint32_t seen = 0;
    space(); if (i >= n || json[i++] != '{') return false;
    for (bool first = true;; first = false) {
        space();
        if (first && i < n && json[i] == '}') { ++i; break; }
        std::string key, string_value; uint64_t number = 0; bool numeric = false;
        if (!text(key)) return false;
        space(); if (i >= n || json[i++] != ':') return false; space();
        if (i < n && json[i] == '"') { if (!text(string_value)) return false; }
        else {
            const size_t begin = i;
            while (i < n && json[i] >= '0' && json[i] <= '9') {
                number = number * 10 + (json[i++] - '0'); if (number > max_time) return false;
            }
            if (i == begin || (json[begin] == '0' && i - begin > 1)) return false;
            numeric = true;
        }
        // Members this core does not know are ignored, so the server can add new ones first.
        for (uint32_t f = 0; f < sizeof(fields) / sizeof(fields[0]); ++f) {
            if (key != fields[f].name) continue;
            if (seen & (1u << f)) return false; // Duplicate members are ambiguous.
            seen |= 1u << f;
            if (numeric != (fields[f].number != nullptr)) return false;
            if (numeric) *fields[f].number = number; else *fields[f].text = string_value;
        }
        space(); if (i >= n) return false;
        if (json[i] == ',') { ++i; continue; }
        if (json[i++] != '}') return false;
        break;
    }
    space();
    return i == n && (seen & 1u);
}
uint32_t feature_bits(const std::string& text) {
    if (text.empty()) return all_features;
    uint32_t bits = 0; size_t begin = 0;
    while (begin <= text.size()) {
        const size_t end = std::min(text.find(',', begin), text.size());
        const std::string name = text.substr(begin, end - begin);
        if (name == "raw_data") bits |= FISICA_FEATURE_RAW_DATA;
        else if (name == "export") bits |= FISICA_FEATURE_EXPORT;
        else if (name == "advanced") bits |= FISICA_FEATURE_ADVANCED;
        begin = end + 1; // Names this core does not know open nothing.
    }
    return bits;
}
// Returns a FisicaEntitlementOutcome. Signature is checked before any claim is trusted.
uint32_t parse_token(const uint8_t *data, uint32_t length, Token& token) {
    while (length && (data[length-1] == '\n' || data[length-1] == '\r' || data[length-1] == ' ' || data[length-1] == '\t')) --length;
    while (length && (*data == '\n' || *data == '\r' || *data == ' ' || *data == '\t')) { ++data; --length; }
    if (length < 8 || length > max_token || std::memcmp(data, "FSE1.", 5) != 0) return FISICA_ENTITLEMENT_REJECTED_FORMAT;
    const char *begin = reinterpret_cast<const char*>(data), *finish = begin + length;
    const char *dot = std::find(begin + 5, finish, '.');
    if (dot == finish || std::find(dot + 1, finish, '.') != finish) return FISICA_ENTITLEMENT_REJECTED_FORMAT;
    std::vector<uint8_t> payload, signature;
    if (!base64url(begin + 5, size_t(dot - begin - 5), payload) || !base64url(dot + 1, size_t(finish - dot - 1), signature) ||
        signature.size() != 64) return FISICA_ENTITLEMENT_REJECTED_FORMAT;
    uint64_t version = 0; std::string kind, bind_type, track;
    if (!parse_payload(payload, token, version, kind, bind_type, track)) return FISICA_ENTITLEMENT_REJECTED_FORMAT;
    const FisicaEntitlementKey *key = nullptr;
    for (const auto& candidate : fisica_entitlement_keys)
        if (candidate.kid && token.kid == candidate.kid) key = &candidate;
    if (!key) return FISICA_ENTITLEMENT_REJECTED_KEY;
    if (crypto_ed25519_check(signature.data(), key->public_key, data, size_t(dot - begin)) != 0)
        return FISICA_ENTITLEMENT_REJECTED_SIGNATURE;
    token.kind = kind == "basic" ? 1 : kind == "special" ? 2 : 0;
    token.bind_type = bind_type == "app" ? FISICA_BIND_APP : bind_type == "pc" ? FISICA_BIND_PC : 0;
    token.track = track == "trial" ? FISICA_TRACK_TRIAL : track == "extension" ? FISICA_TRACK_EXTENSION :
                  track == "contract" ? FISICA_TRACK_CONTRACT : 0;
    const bool bind_ok = bind_type.empty() ? token.bind.empty() :
        token.bind_type == FISICA_BIND_APP ? app_id(token.bind) : token.bind_type == FISICA_BIND_PC && pc_hash(token.bind);
    const bool features_ok = token.features_text.size() <= 256 && std::all_of(token.features_text.begin(), token.features_text.end(),
        [](char c) { return (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '_' || c == ','; });
    if (version != 1 || !identifier(token.company) || !identifier(token.contract) || !token.kind ||
        !token.verified_at || token.refresh_by <= token.verified_at || token.grants.size() > 256 ||
        (!token.key.empty() && !identifier(token.key)) || !bind_ok || (token.bind_type && token.key.empty()) ||
        (!track.empty() && !token.track) || !features_ok)
        return FISICA_ENTITLEMENT_REJECTED_FORMAT;
    // The server cannot grant a longer confirmation window than the policy maximum.
    token.refresh_by = std::min(token.refresh_by, token.verified_at + max_refresh_seconds);
    token.features = feature_bits(token.features_text);
    token.environment = key->environment;
    token.text.assign(begin, finish);
    return FISICA_ENTITLEMENT_ACCEPTED;
}
uint32_t contract_state(const Token& token, uint64_t now) {
    if (token.revoked_at) return FISICA_CONTRACT_REVOKED; // Only a server-signed revocation counts.
    if (token.end && now >= token.end) return FISICA_CONTRACT_ENDED;
    if (token.start && now < token.start) return FISICA_CONTRACT_NOT_STARTED;
    return FISICA_CONTRACT_ACTIVE;
}
int32_t copy_out(const std::string& value, uint8_t *output, uint32_t capacity, uint32_t *length) {
    if (!length) return FISICA_INVALID_ARGUMENT;
    *length = uint32_t(value.size());
    if (value.size() > capacity) return FISICA_BUFFER_TOO_SMALL;
    if (!value.empty() && !output) return FISICA_INVALID_ARGUMENT;
    std::copy(value.begin(), value.end(), output);
    return FISICA_OK;
}
// "<prefix>.<base64url({"k","t","b"})>.<check>", the format the developer portal reads.
std::string make_code(const char *prefix, const std::string& key, uint32_t type, const std::string& bind) {
    const std::string body = base64url_encode(std::string("{\"k\":\"") + key + "\",\"t\":\"" + bind_name(type) + "\",\"b\":\"" + bind + "\"}");
    const std::string material = std::string(std::strcmp(prefix, "FRC1") == 0 ? "fisica-release-code-v1|" : "") + prefix + "." + body;
    return std::string(prefix) + "." + body + "." + sha256_hex(material).substr(0, 8);
}

struct Evaluation {
    uint32_t mode = FISICA_MODE_BASIC, reason = FISICA_REASON_NO_KEY, features = 0, warnings = 0;
    uint64_t until = 0;
};
} // namespace

struct FisicaEntitlement {
    bool verified = false;
    Token current;
    Target target = read_target();
    uint64_t latest = 0; // latest wall-clock time seen, to notice a clock set back
    uint32_t last_outcome = FISICA_ENTITLEMENT_NONE;

    Evaluation evaluate(uint64_t now) const {
        Evaluation result;
        if (last_outcome >= FISICA_ENTITLEMENT_REJECTED_FORMAT) result.warnings |= FISICA_WARN_IMPORT_REJECTED;
        if (!verified) return result;
        const Token& t = current;
        if (t.environment == FISICA_KEY_TEST) result.warnings |= FISICA_WARN_TEST_KEY;
        if (t.key.empty()) { result.reason = FISICA_REASON_LEGACY_TOKEN; return result; }
        if (!t.bind_type) { result.reason = FISICA_REASON_NOT_ACTIVATED; return result; }
        if (!target.type) { result.reason = FISICA_REASON_NO_TARGET; return result; }
        if (!same_target(t.bind_type, t.bind, target)) { result.reason = FISICA_REASON_OTHER_TARGET; return result; }
        if (t.revoked_at) { result.reason = FISICA_REASON_REVOKED; return result; }
        if (now + clock_tolerance < std::max(latest, t.verified_at)) { result.reason = FISICA_REASON_CLOCK_ROLLBACK; return result; }
        const uint64_t window = t.key_until > t.verified_at ? std::min(renew_window, (t.key_until - t.verified_at) / 3) : 0;
        if (now >= t.refresh_by || (t.key_until && now + window >= t.key_until)) result.warnings |= FISICA_WARN_RENEW_DUE;
        if (t.key_until && now >= t.key_until) { result.reason = FISICA_REASON_KEY_EXPIRED; return result; }
        if (t.start && now < t.start) { result.reason = FISICA_REASON_NOT_STARTED; return result; }
        if (!t.full_until || now >= t.full_until) { result.reason = FISICA_REASON_FULL_ENDED; return result; }
        if (t.end && now >= t.end) result.warnings |= FISICA_WARN_GRACE;
        if (now + ending_window >= t.full_until) result.warnings |= FISICA_WARN_FULL_ENDING;
        result.mode = FISICA_MODE_FULL; result.reason = FISICA_REASON_NONE;
        result.features = t.features | FISICA_FEATURE_FULL_RATE;
        result.until = t.key_until ? std::min(t.key_until, t.full_until) : t.full_until;
        return result;
    }
    // Every call with a wall-clock time evaluates before it moves the latest time forward,
    // so a clock set back is compared with what was seen before it.
    Evaluation observe(uint64_t now) {
        const Evaluation result = evaluate(now);
        latest = std::max(latest, now);
        return result;
    }
    void accept(const Token& token, uint64_t now) {
        // A newer signed time is evidence of the real time: it replaces a latest time that a
        // clock once set too far ahead left behind. The same key imported again is not.
        const bool newer = !verified || token.verified_at > current.verified_at;
        latest = newer ? std::max(token.verified_at, now) : std::max({latest, token.verified_at, now});
        current = token; verified = true;
    }
};

namespace {
template<class F> int32_t guarded(FisicaEntitlement *context, F operation) noexcept {
    if (!context) return FISICA_INVALID_ARGUMENT;
    try { return operation(); } catch (...) { return FISICA_INTERNAL_ERROR; }
}
}

uint64_t fisica_wall_seconds(uint64_t host_s) noexcept {
#if FISICA_VALIDATION_BUILD
    return host_s;
#else
    const auto system = std::chrono::duration_cast<std::chrono::seconds>(
        std::chrono::system_clock::now().time_since_epoch()).count();
    return std::max<uint64_t>(host_s, system > 0 ? uint64_t(system) : 0);
#endif
}
uint64_t fisica_monotonic_ms(uint64_t host_ms) noexcept {
#if FISICA_VALIDATION_BUILD
    return host_ms;
#else
    (void)host_ms;
    return uint64_t(std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::steady_clock::now().time_since_epoch()).count());
#endif
}

FisicaLicenseView fisica_license_view(FisicaEntitlement *context, uint64_t now_s) noexcept {
    FisicaLicenseView view;
    if (!context || now_s > fisica_max_wall_seconds) return view;
    now_s = fisica_wall_seconds(now_s);
    try {
        const Evaluation result = context->observe(now_s);
        view.mode = result.mode; view.features = result.features; view.until = result.until;
    } catch (...) {}
    return view;
}
bool fisica_license_raw(FisicaEntitlement *context, uint64_t now_s) noexcept {
    const FisicaLicenseView view = fisica_license_view(context, now_s);
    return view.mode == FISICA_MODE_FULL && (view.features & FISICA_FEATURE_RAW_DATA);
}

extern "C" {
int32_t fisica_core_entitlement_create(FisicaEntitlement **context) {
    if (!context) return FISICA_INVALID_ARGUMENT;
    *context = nullptr;
    try { *context = new FisicaEntitlement; return FISICA_OK; } catch (...) { return FISICA_INTERNAL_ERROR; }
}
void fisica_core_entitlement_destroy(FisicaEntitlement *context) { delete context; }

int32_t fisica_core_entitlement_import(FisicaEntitlement *context, const uint8_t *token, uint32_t length,
                                      uint64_t now_s, uint32_t *outcome) {
    return guarded(context, [&]() -> int32_t {
        if (!token || !outcome || now_s > fisica_max_wall_seconds) return FISICA_INVALID_ARGUMENT;
        now_s = fisica_wall_seconds(now_s);
        Token parsed;
        uint32_t result = parse_token(token, length, parsed);
        // A key activated elsewhere never replaces the key this target holds.
        if (result == FISICA_ENTITLEMENT_ACCEPTED && parsed.bind_type && !same_target(parsed.bind_type, parsed.bind, context->target))
            result = FISICA_ENTITLEMENT_REJECTED_TARGET;
        // A rejected or older input never erases information that was already verified.
        if (result == FISICA_ENTITLEMENT_ACCEPTED && context->verified && parsed.key == context->current.key &&
            parsed.company == context->current.company && parsed.contract == context->current.contract &&
            parsed.verified_at < context->current.verified_at)
            result = FISICA_ENTITLEMENT_IGNORED_OLDER;
        if (result == FISICA_ENTITLEMENT_ACCEPTED) context->accept(parsed, now_s);
        else context->latest = std::max(context->latest, now_s);
        context->last_outcome = *outcome = result;
        return FISICA_OK;
    });
}

int32_t fisica_core_entitlement_status(FisicaEntitlement *context, uint64_t now_s, uint64_t *status, uint32_t capacity) {
    return guarded(context, [&]() -> int32_t {
        if (!status || now_s > fisica_max_wall_seconds) return FISICA_INVALID_ARGUMENT;
        if (capacity < FISICA_LIMIT_STATUS_FIELDS) return FISICA_BUFFER_TOO_SMALL;
        now_s = fisica_wall_seconds(now_s);
        std::fill_n(status, FISICA_LIMIT_STATUS_FIELDS, 0);
        const Evaluation result = context->observe(now_s);
        const Token& token = context->current;
        status[0] = result.mode; status[1] = result.reason; status[2] = result.features;
        status[7] = context->target.type;
        status[15] = context->last_outcome;
        status[17] = result.warnings; status[18] = context->latest;
        if (!context->verified) { status[3] = FISICA_CONTRACT_UNVERIFIED; return FISICA_OK; }
        status[3] = contract_state(token, now_s);
        status[4] = token.kind; status[5] = token.track; status[6] = token.bind_type;
        status[8] = token.verified_at; status[9] = token.refresh_by; status[10] = token.key_until;
        status[11] = token.full_until; status[12] = token.start; status[13] = token.end; status[14] = token.revoked_at;
        status[16] = token.environment;
        return FISICA_OK;
    });
}

int32_t fisica_core_entitlement_text(FisicaEntitlement *context, uint32_t field, uint8_t *output,
                                    uint32_t capacity, uint32_t *length) {
    return guarded(context, [&]() -> int32_t {
        const Token& token = context->current;
        const Target& target = context->target;
        switch (field) {
        case FISICA_ENTITLEMENT_COMPANY: return copy_out(token.company, output, capacity, length);
        case FISICA_ENTITLEMENT_CONTRACT: return copy_out(token.contract, output, capacity, length);
        case FISICA_ENTITLEMENT_GRANTS: return copy_out(token.grants, output, capacity, length);
        case FISICA_ENTITLEMENT_SIGNING_KEY: return copy_out(token.kid, output, capacity, length);
        case FISICA_ENTITLEMENT_TOKEN: return copy_out(token.text, output, capacity, length);
        case FISICA_ENTITLEMENT_KEY: return copy_out(token.key, output, capacity, length);
        case FISICA_ENTITLEMENT_BIND: return copy_out(token.bind, output, capacity, length);
        case FISICA_ENTITLEMENT_TARGET: return copy_out(target.value, output, capacity, length);
        case FISICA_ENTITLEMENT_FEATURES: return copy_out(token.features_text, output, capacity, length);
        case FISICA_ENTITLEMENT_ACTIVATION_REQUEST:
            if (!length) return FISICA_INVALID_ARGUMENT;
            if (!target.type) return FISICA_INVALID_STATE;
            return copy_out(std::string("{\"bind_type\":\"") + bind_name(target.type) + "\",\"bind\":\"" + target.value + "\"}",
                            output, capacity, length);
        case FISICA_ENTITLEMENT_STATE_NAME:
            // One file per seat on this device: a key per contract and target.
            return copy_out(context->verified ? "entitlement-" + sha256_hex(token.company + "\n" + token.contract + "\n" +
                            target.value).substr(0, 16) + ".state" : std::string(), output, capacity, length);
        case FISICA_ENTITLEMENT_ACTIVATION_CODE:
            if (!length) return FISICA_INVALID_ARGUMENT;
            if (!target.type || !context->verified || token.key.empty() || token.bind_type) return FISICA_INVALID_STATE;
            return copy_out(make_code("FAC1", token.key, target.type, target.value), output, capacity, length);
        default: return FISICA_INVALID_ARGUMENT;
        }
    });
}

int32_t fisica_core_entitlement_next_exchange(FisicaEntitlement *context, uint64_t now_s, uint32_t *exchange) {
    return guarded(context, [&]() -> int32_t {
        if (!exchange || now_s > fisica_max_wall_seconds) return FISICA_INVALID_ARGUMENT;
        const Evaluation result = context->observe(fisica_wall_seconds(now_s));
        *exchange = FISICA_EXCHANGE_NONE;
        if (!context->verified || context->current.key.empty()) return FISICA_OK;  // no key, or a legacy contract token
        switch (result.reason) {
        case FISICA_REASON_NOT_ACTIVATED:
            if (context->target.type) *exchange = FISICA_EXCHANGE_ACTIVATE;
            break;
        case FISICA_REASON_CLOCK_ROLLBACK: case FISICA_REASON_KEY_EXPIRED:
        case FISICA_REASON_NOT_STARTED: case FISICA_REASON_FULL_ENDED:
            *exchange = FISICA_EXCHANGE_REFRESH;
            break;
        case FISICA_REASON_NONE:
            if (result.warnings & FISICA_WARN_RENEW_DUE) *exchange = FISICA_EXCHANGE_REFRESH;
            break;
        default: break;  // no target, another target, or released: nothing the server can change here
        }
        return FISICA_OK;
    });
}

int32_t fisica_core_entitlement_release(FisicaEntitlement *context, uint8_t *output, uint32_t capacity, uint32_t *length) {
    return guarded(context, [&]() -> int32_t {
        if (!length) return FISICA_INVALID_ARGUMENT;
        const Token& token = context->current;
        if (!context->verified || token.key.empty() || !same_target(token.bind_type, token.bind, context->target))
            return FISICA_INVALID_STATE;
        const int32_t status = copy_out(make_code("FRC1", token.key, token.bind_type, token.bind), output, capacity, length);
        if (status == FISICA_OK) { context->current = Token(); context->verified = false; }
        return status;
    });
}

int32_t fisica_core_entitlement_export(FisicaEntitlement *context, uint8_t *output, uint32_t capacity, uint32_t *length) {
    return guarded(context, [&]() -> int32_t {
        std::string state = "FSS1\n";
        if (context->verified) state += "C " + context->current.text + "\n";
        if (context->latest) state += "T " + std::to_string(context->latest) + "\n";
        return copy_out(state, output, capacity, length);
    });
}

int32_t fisica_core_entitlement_restore(FisicaEntitlement *context, const uint8_t *state, uint32_t length) {
    return guarded(context, [&]() -> int32_t {
        if (!state || length < 5 || length > max_state || std::memcmp(state, "FSS1\n", 5) != 0) return FISICA_INVALID_ARGUMENT;
        bool verified = false; Token restored; uint64_t latest = 0;
        const char *cursor = reinterpret_cast<const char*>(state) + 5, *finish = reinterpret_cast<const char*>(state) + length;
        while (cursor < finish) {
            const char *line_end = std::find(cursor, finish, '\n');
            // Only the key and time lines are state. Anything else, including usage lines written
            // by earlier development builds, is ignored.
            if (line_end - cursor > 2 && cursor[0] == 'C' && cursor[1] == ' ') {
                // The stored key is verified again: editing the local file cannot forge a license.
                Token token;
                if (parse_token(reinterpret_cast<const uint8_t*>(cursor + 2), uint32_t(line_end - cursor - 2), token) == FISICA_ENTITLEMENT_ACCEPTED) {
                    restored = token; verified = true;
                }
            } else if (line_end - cursor > 2 && line_end - cursor <= 18 && cursor[0] == 'T' && cursor[1] == ' ') {
                uint64_t value = 0; bool digits = true;
                for (const char *c = cursor + 2; c < line_end; ++c) {
                    if (*c < '0' || *c > '9') { digits = false; break; }
                    value = value * 10 + uint64_t(*c - '0');
                }
                if (digits && value <= fisica_max_wall_seconds) latest = value;
            }
            cursor = line_end == finish ? finish : line_end + 1;
        }
        context->current = verified ? restored : Token();
        context->verified = verified;
        context->latest = std::max(context->latest, latest);
        return FISICA_OK;
    });
}
}
