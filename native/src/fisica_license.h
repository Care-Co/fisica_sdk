// Internal to the core: what the session and the grid functions need from a license
// context, and the packet decoder they share. Not part of the public header.
#pragma once
#include "fisica_core.h"

struct FisicaLicenseView {
    uint32_t mode = FISICA_MODE_BASIC, features = 0;
    uint64_t until = 0; // wall-clock second at which full mode ends; 0 in basic mode
};

// The license decision at now_s; a NULL context is basic mode. Never throws.
FisicaLicenseView fisica_license_view(FisicaEntitlement *context, uint64_t now_s) noexcept;
// True when now_s is usable and the license opens RAW_DATA.
bool fisica_license_raw(FisicaEntitlement *context, uint64_t now_s) noexcept;

namespace fisica {
constexpr uint32_t packet_size = 645, raw_cells = 638, display_cells = 841;
// Decode a 645-byte measurement packet without any license check.
void decode_measurement(const uint8_t *packet, double *raw, double *display, double *metrics);
}
