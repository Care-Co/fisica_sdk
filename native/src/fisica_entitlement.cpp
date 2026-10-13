// Runtime entitlement context: server-signed verification info, never a feature gate.
// Three independent facts: (1) the base SDK grant has no date expiry, (2) how recently
// the server confirmed this info, (3) the state of the company's contract.
#include "fisica_core.h"
#include "fisica_release.h"
#include "third_party/monocypher/monocypher-ed25519.h"

#include <algorithm>
#include <array>
#include <cstring>
#include <string>
#include <vector>

namespace {
constexpr uint64_t max_refresh_seconds = 30ull * 24 * 3600;
constexpr uint32_t max_token = 4096, max_state = 262144, max_usage = 16;
constexpr uint64_t max_time = 1ull << 53;

struct Token {
    std::string text, kid, company, contract, grants;
    uint32_t kind = 0, environment = 0;
    uint64_t verified_at = 0, refresh_by = 0, start = 0, end = 0, revoked_at = 0;
};
struct Usage { Token token; uint64_t last_used = 0; bool observed = false, dirty = false; };
struct Report {
    bool active = false;
    std::array<uint8_t,16> id{};
    Token token; uint64_t last_used = 0; bool observed = false;
};

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
bool identifier(const std::string& value) {
    if (value.empty() || value.size() > 64) return false;
    return std::all_of(value.begin(), value.end(), [](char c) {
        return (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '_' || c == '-' || c == '.' || c == ':';
    });
}
// Strict flat JSON: string and non-negative integer members only, no escapes.
// A small closed grammar is safer here than a general parser on untrusted input.
bool parse_payload(const std::vector<uint8_t>& json, Token& token, uint64_t& version) {
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
    uint32_t seen = 0; bool has_version = false;
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
        struct Field { const char *name; std::string *text; uint64_t *number; };
        std::string kind;
        const Field fields[] = {
            {"v", nullptr, &version}, {"kid", &token.kid, nullptr}, {"company", &token.company, nullptr},
            {"contract", &token.contract, nullptr}, {"kind", &kind, nullptr}, {"grants", &token.grants, nullptr},
            {"verified_at", nullptr, &token.verified_at}, {"refresh_by", nullptr, &token.refresh_by},
            {"contract_start", nullptr, &token.start}, {"contract_end", nullptr, &token.end},
            {"revoked_at", nullptr, &token.revoked_at},
        };
        for (uint32_t f = 0; f < sizeof(fields) / sizeof(fields[0]); ++f) {
            if (key != fields[f].name) continue;
            if (seen & (1u << f)) return false; // Duplicate members are ambiguous.
            seen |= 1u << f;
            if (numeric != (fields[f].number != nullptr)) return false;
            if (numeric) *fields[f].number = number; else *fields[f].text = string_value;
            if (key == "v") has_version = true;
            if (key == "kind") token.kind = kind == "basic" ? 1 : kind == "special" ? 2 : 0;
        }
        space(); if (i >= n) return false;
        if (json[i] == ',') { ++i; continue; }
        if (json[i++] != '}') return false;
        break;
    }
    space();
    return i == n && has_version;
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
    uint64_t version = 0;
    if (!parse_payload(payload, token, version)) return FISICA_ENTITLEMENT_REJECTED_FORMAT;
    const FisicaEntitlementKey *key = nullptr;
    for (const auto& candidate : fisica_entitlement_keys)
        if (candidate.kid && token.kid == candidate.kid) key = &candidate;
    if (!key) return FISICA_ENTITLEMENT_REJECTED_KEY;
    if (crypto_ed25519_check(signature.data(), key->public_key, data, size_t(dot - begin)) != 0)
        return FISICA_ENTITLEMENT_REJECTED_SIGNATURE;
    if (version != 1 || !identifier(token.company) || !identifier(token.contract) || !token.kind ||
        !token.verified_at || token.refresh_by <= token.verified_at || token.grants.size() > 256)
        return FISICA_ENTITLEMENT_REJECTED_FORMAT;
    // The server cannot grant a longer confirmation window than the policy maximum.
    token.refresh_by = std::min(token.refresh_by, token.verified_at + max_refresh_seconds);
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
bool same_contract(const Token& a, const Token& b) { return a.company == b.company && a.contract == b.contract; }
std::string number(uint64_t value) { return std::to_string(value); }
bool parse_number(const std::string& text, uint64_t& value) {
    if (text.empty() || text.size() > 16) return false;
    value = 0;
    for (char c : text) { if (c < '0' || c > '9') return false; value = value * 10 + uint64_t(c - '0'); }
    return true;
}
int32_t copy_out(const std::string& value, uint8_t *output, uint32_t capacity, uint32_t *length) {
    if (!length) return FISICA_INVALID_ARGUMENT;
    *length = uint32_t(value.size());
    if (value.size() > capacity) return FISICA_BUFFER_TOO_SMALL;
    if (!value.empty() && !output) return FISICA_INVALID_ARGUMENT;
    std::copy(value.begin(), value.end(), output);
    return FISICA_OK;
}
} // namespace

struct FisicaEntitlement {
    bool verified = false;
    Token current;
    uint32_t last_outcome = FISICA_ENTITLEMENT_NONE;
    std::vector<Usage> usage;
    Report report;

    Usage *find(const Token& token) {
        for (auto& item : usage) if (same_contract(item.token, token)) return &item;
        return nullptr;
    }
};

namespace {
template<class F> int32_t guarded(FisicaEntitlement *context, F operation) noexcept {
    if (!context) return FISICA_INVALID_ARGUMENT;
    try { return operation(); } catch (...) { return FISICA_INTERNAL_ERROR; }
}
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
        if (!token || !outcome || now_s > max_time) return FISICA_INVALID_ARGUMENT;
        Token parsed;
        uint32_t result = parse_token(token, length, parsed);
        // A rejected or older input never erases information that was already verified.
        if (result == FISICA_ENTITLEMENT_ACCEPTED && context->verified &&
            same_contract(parsed, context->current) && parsed.verified_at < context->current.verified_at)
            result = FISICA_ENTITLEMENT_IGNORED_OLDER;
        if (result == FISICA_ENTITLEMENT_ACCEPTED) {
            if (auto *item = context->find(parsed)) item->token = parsed;
            context->current = parsed; context->verified = true;
        }
        context->last_outcome = *outcome = result;
        return FISICA_OK;
    });
}

int32_t fisica_core_entitlement_status(FisicaEntitlement *context, uint64_t now_s, uint64_t *status, uint32_t capacity) {
    return guarded(context, [&]() -> int32_t {
        if (!status || now_s > max_time) return FISICA_INVALID_ARGUMENT;
        if (capacity < 12) return FISICA_BUFFER_TOO_SMALL;
        std::fill_n(status, 12, 0);
        const Token& token = context->current;
        uint64_t warnings = 0;
        status[0] = FISICA_BASE_LICENSE_GRANTED; // No date expiry; unrelated to everything below.
        status[9] = context->last_outcome;
        if (context->last_outcome >= FISICA_ENTITLEMENT_REJECTED_FORMAT) warnings |= FISICA_WARN_IMPORT_REJECTED;
        if (!context->verified) {
            status[1] = FISICA_VERIFICATION_NONE; status[2] = FISICA_CONTRACT_UNVERIFIED;
            status[11] = warnings | FISICA_WARN_UNVERIFIED;
            return FISICA_OK;
        }
        const bool due = now_s >= token.refresh_by;
        status[1] = due ? FISICA_VERIFICATION_REFRESH_DUE : FISICA_VERIFICATION_FRESH;
        status[2] = contract_state(token, now_s);
        status[3] = token.kind;
        status[4] = token.verified_at; status[5] = token.refresh_by;
        status[6] = token.start; status[7] = token.end; status[8] = token.revoked_at;
        status[10] = token.environment;
        if (due) warnings |= FISICA_WARN_REFRESH_DUE;
        if (status[2] == FISICA_CONTRACT_ENDED) warnings |= FISICA_WARN_CONTRACT_ENDED;
        if (status[2] == FISICA_CONTRACT_REVOKED) warnings |= FISICA_WARN_CONTRACT_REVOKED;
        if (status[2] == FISICA_CONTRACT_NOT_STARTED) warnings |= FISICA_WARN_CONTRACT_NOT_STARTED;
        if (token.environment == FISICA_KEY_TEST) warnings |= FISICA_WARN_TEST_KEY;
        status[11] = warnings;
        return FISICA_OK;
    });
}

int32_t fisica_core_entitlement_text(FisicaEntitlement *context, uint32_t field, uint8_t *output,
                                    uint32_t capacity, uint32_t *length) {
    return guarded(context, [&]() -> int32_t {
        const Token& token = context->current;
        switch (field) {
        case FISICA_ENTITLEMENT_COMPANY: return copy_out(token.company, output, capacity, length);
        case FISICA_ENTITLEMENT_CONTRACT: return copy_out(token.contract, output, capacity, length);
        case FISICA_ENTITLEMENT_GRANTS: return copy_out(token.grants, output, capacity, length);
        case FISICA_ENTITLEMENT_KEY_ID: return copy_out(token.kid, output, capacity, length);
        case FISICA_ENTITLEMENT_TOKEN: return copy_out(token.text, output, capacity, length);
        default: return FISICA_INVALID_ARGUMENT;
        }
    });
}

int32_t fisica_core_entitlement_mark_used(FisicaEntitlement *context, uint64_t now_s) {
    return guarded(context, [&]() -> int32_t {
        if (now_s > max_time) return FISICA_INVALID_ARGUMENT;
        // Unverified use is never attributed to any company, contract or shared identity.
        if (!context->verified) return FISICA_OK;
        Usage *item = context->find(context->current);
        if (!item) {
            if (context->usage.size() >= max_usage)
                context->usage.erase(std::min_element(context->usage.begin(), context->usage.end(),
                    [](const Usage& a, const Usage& b) { return a.last_used < b.last_used; }));
            context->usage.push_back({context->current});
            item = &context->usage.back();
        }
        item->last_used = std::max(item->last_used, now_s);
        const uint32_t state = contract_state(item->token, now_s);
        // An observation that use and an ended/revoked contract coincided; not proof of a breach.
        if (state == FISICA_CONTRACT_ENDED || state == FISICA_CONTRACT_REVOKED) item->observed = true;
        item->dirty = true;
        return FISICA_OK;
    });
}

int32_t fisica_core_entitlement_report(FisicaEntitlement *context, const uint8_t *report_id, uint32_t id_length,
                                      uint8_t *token, uint32_t token_capacity, uint32_t *token_length,
                                      uint8_t *body, uint32_t body_capacity, uint32_t *body_length) {
    return guarded(context, [&]() -> int32_t {
        if (!report_id || id_length != 16 || !token_length || !body_length) return FISICA_INVALID_ARGUMENT;
        Report& report = context->report;
        if (!report.active) {
            const auto item = std::find_if(context->usage.begin(), context->usage.end(), [](const Usage& u) { return u.dirty; });
            if (item == context->usage.end()) return FISICA_NO_RESULT;
            // Freeze one snapshot so a retry resends identical content under the same temporary id.
            report.active = true; std::copy_n(report_id, 16, report.id.begin());
            report.token = item->token; report.last_used = item->last_used; report.observed = item->observed;
        }
        static const char hex[] = "0123456789abcdef";
        std::string id;
        for (uint8_t byte : report.id) { id.push_back(hex[byte >> 4]); id.push_back(hex[byte & 15]); }
        const std::string json = "{\"report_id\":\"" + id + "\",\"client_last_used_at\":" + number(report.last_used) +
            ",\"observed_after_contract_end\":" + (report.observed ? "true" : "false") + "}";
        const int32_t first = copy_out(report.token.text, token, token_capacity, token_length);
        const int32_t second = copy_out(json, body, body_capacity, body_length);
        return first ? first : second;
    });
}

int32_t fisica_core_entitlement_report_done(FisicaEntitlement *context) {
    return guarded(context, [&]() -> int32_t {
        Report& report = context->report;
        if (!report.active) return FISICA_INVALID_STATE;
        const auto item = std::find_if(context->usage.begin(), context->usage.end(),
            [&](const Usage& u) { return same_contract(u.token, report.token); });
        // Use recorded after the snapshot stays pending for the next report.
        if (item != context->usage.end() && item->last_used == report.last_used && item->observed == report.observed)
            context->usage.erase(item);
        report = Report{};
        return FISICA_OK;
    });
}

int32_t fisica_core_entitlement_export(FisicaEntitlement *context, uint8_t *output, uint32_t capacity, uint32_t *length) {
    return guarded(context, [&]() -> int32_t {
        static const char hex[] = "0123456789abcdef";
        std::string state = "FSS1\n";
        if (context->verified) state += "C " + context->current.text + "\n";
        for (const auto& item : context->usage)
            state += "U " + item.token.text + " " + number(item.last_used) + " " + (item.observed ? "1" : "0") + " " + (item.dirty ? "1" : "0") + "\n";
        if (context->report.active) {
            state += "R ";
            for (uint8_t byte : context->report.id) { state.push_back(hex[byte >> 4]); state.push_back(hex[byte & 15]); }
            state += " " + context->report.token.text + " " + number(context->report.last_used) + " " + (context->report.observed ? "1" : "0") + "\n";
        }
        return copy_out(state, output, capacity, length);
    });
}

int32_t fisica_core_entitlement_restore(FisicaEntitlement *context, const uint8_t *state, uint32_t length) {
    return guarded(context, [&]() -> int32_t {
        if (!state || length < 5 || length > max_state || std::memcmp(state, "FSS1\n", 5) != 0) return FISICA_INVALID_ARGUMENT;
        FisicaEntitlement restored;
        const char *cursor = reinterpret_cast<const char*>(state) + 5, *finish = reinterpret_cast<const char*>(state) + length;
        while (cursor < finish) {
            const char *line_end = std::find(cursor, finish, '\n');
            std::vector<std::string> parts(1);
            for (const char *c = cursor; c < line_end; ++c) { if (*c == ' ') parts.emplace_back(); else parts.back().push_back(*c); }
            cursor = line_end == finish ? finish : line_end + 1;
            // Stored tokens are verified again: editing the local file cannot forge an attribution.
            const auto token = [&](const std::string& text, Token& out) {
                return parse_token(reinterpret_cast<const uint8_t*>(text.data()), uint32_t(text.size()), out) == FISICA_ENTITLEMENT_ACCEPTED;
            };
            if (parts[0] == "C" && parts.size() == 2) {
                if (token(parts[1], restored.current)) restored.verified = true; else restored.current = Token{};
            } else if (parts[0] == "U" && parts.size() == 5 && restored.usage.size() < max_usage) {
                Usage item;
                if (token(parts[1], item.token) && parse_number(parts[2], item.last_used) && !restored.find(item.token)) {
                    item.observed = parts[3] == "1"; item.dirty = parts[4] == "1";
                    restored.usage.push_back(item);
                }
            } else if (parts[0] == "R" && parts.size() == 5 && parts[1].size() == 32) {
                Report report; bool valid = true;
                for (unsigned i = 0; i < 16 && valid; ++i) {
                    const auto digit = [&](char c) { return c >= '0' && c <= '9' ? c - '0' : c >= 'a' && c <= 'f' ? c - 'a' + 10 : -1; };
                    const int high = digit(parts[1][2*i]), low = digit(parts[1][2*i+1]);
                    valid = high >= 0 && low >= 0;
                    if (valid) report.id[i] = uint8_t(high * 16 + low);
                }
                if (valid && token(parts[2], report.token) && parse_number(parts[3], report.last_used)) {
                    report.observed = parts[4] == "1"; report.active = true; restored.report = report;
                }
            }
        }
        restored.last_outcome = context->last_outcome;
        *context = restored;
        return FISICA_OK;
    });
}
}
