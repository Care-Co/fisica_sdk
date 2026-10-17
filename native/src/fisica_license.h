// Internal to the core: what the session and the grid functions need from a license
// context, and the packet decoder they share. Not part of the public header.
#pragma once
#include "fisica_core.h"
#include <functional>
#include <string>

struct FisicaLicenseView {
    uint32_t mode = FISICA_MODE_BASIC, features = 0;
    uint64_t until = 0; // wall-clock second at which full mode ends; 0 in basic mode
};

// Customer builds read the system clock themselves: a host may move the time later, never
// earlier, so it cannot keep an ended key or grant in full mode. Validation builds (test keys
// trusted) use the host's time as is, for tests. Wall-clock seconds; monotonic milliseconds.
uint64_t fisica_wall_seconds(uint64_t host_s) noexcept;
uint64_t fisica_monotonic_ms(uint64_t host_ms) noexcept;
// Unix seconds after 2200-01-01 are a mistake (milliseconds passed as seconds), not a time.
constexpr uint64_t fisica_max_wall_seconds = 7258118400ull;

// The license decision at now_s; a NULL context is basic mode. Never throws.
FisicaLicenseView fisica_license_view(FisicaEntitlement *context, uint64_t now_s) noexcept;
// True when now_s is usable and the license opens RAW_DATA.
bool fisica_license_raw(FisicaEntitlement *context, uint64_t now_s) noexcept;

namespace fisica {
constexpr uint32_t packet_size = 645, raw_cells = 638, display_cells = 841;
// Android: true when /proc/self/maps text maps this package's own installed base.apk.
bool apk_mapped(const std::string& maps, const std::string& package);
// Decode a 645-byte measurement packet without any license check.
void decode_measurement(const uint8_t *packet, double *raw, double *display, double *metrics);
// fisica_core_next_packet; expected (header, type of the pending command, or nullptr) lets
// that command's response be found after leading noise while no frames stream.
int32_t next_packet(const uint8_t *buffer, uint32_t length, uint32_t *consumed, uint32_t *kind,
                    uint8_t *packet, uint32_t capacity, uint32_t *packet_length, const uint8_t *expected);
// fisica_core_analyze_packets over packets fetched one by one.
int32_t analyze_packets(uint32_t count, const std::function<const uint8_t*(uint32_t)>& packet,
                        double *result, uint32_t capacity);
}
