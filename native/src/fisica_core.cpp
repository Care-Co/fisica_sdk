#include "fisica_core.h"
#include "fisica_release.h"

#include <algorithm>
#include <array>
#include <cmath>
#include <cstring>
#include <limits>
#include <string>
#include <vector>

namespace {
constexpr uint32_t rows_raw = 29, cols_raw = 22, cells_raw = 638;
constexpr uint32_t cols_display = 29, cells_display = 841, packet_size = 645;
constexpr uint64_t max_cells = 16 * 1024 * 1024;

bool measurement_packet(const uint8_t *packet) {
    // Recorded Scale packets use 639; retain the previous SDK's 638 layout.
    const bool separator = (packet[639]==0xf0 && packet[640]==0x0f) ||
                           (packet[638]==0xf0 && packet[639]==0x0f);
    return separator && packet[643]==0xf0 && packet[644]==0x0f;
}
bool variable_response(uint8_t header, uint8_t type) {
    return type==0x47 && header>=0x40 && header<=0x49;
}
bool response_packet(const uint8_t *packet, uint32_t length) {
    return packet && length>=8 && length<=4096 && packet[0]==0x24 &&
        packet[1]>=0x10 && packet[1]<=0x60 &&
        (packet[2]==0x47 || packet[2]==0x53) && packet[3]<=1 &&
        (length==8 || variable_response(packet[1],packet[2])) &&
        packet[length-2]==0xff && packet[length-1]==0xf0;
}

template <typename F> int32_t protect(F operation) noexcept {
    try { return operation(); } catch (...) { return FISICA_INTERNAL_ERROR; }
}
bool valid_grid(const double *grid, uint32_t rows, uint32_t cols) {
    if (!grid || !rows || !cols || uint64_t(rows) * cols > max_cells) return false;
    for (uint64_t i = 0; i < uint64_t(rows) * cols; ++i)
        if (!std::isfinite(grid[i]) || grid[i] < 0) return false;
    return true;
}
void preprocess(const double *grid, uint32_t rows, uint32_t cols, double *output) {
    double sum = 0;
    uint32_t active = 0;
    for (uint32_t col = 0; col < cols; ++col) {
        uint32_t count = 0;
        for (uint32_t row = 0; row < rows; ++row) count += grid[row * cols + col] != 0;
        for (uint32_t row = 0; row < rows; ++row) {
            const auto index = row * cols + col;
            output[index] = grid[index] * count;
        }
    }
    // Match NumPy's row-major mean rather than accumulating by columns.
    for (uint32_t i = 0; i < rows * cols; ++i)
        if (output[i] != 0) { sum += output[i]; ++active; }
    if (!active) return;
    const double step = (2.0 * (sum / active)) / 10.0;
    double maximum = 0;
    for (uint32_t i = 0; i < rows * cols; ++i) {
        if (output[i] == 0) continue;
        uint32_t bin = 0;
        while (bin < 9 && output[i] >= step * (bin + 1)) ++bin;
        const double upper = bin == 9 ? 2.0 * (sum / active) : step * (bin + 1);
        output[i] = (step * bin + upper) / 2;
        maximum = std::max(maximum, output[i]);
    }
    if (maximum > 255) {
        const double scale = 255 / maximum;
        for (uint32_t i = 0; i < rows * cols; ++i) output[i] = std::trunc(output[i] * scale);
    }
}
void display_grid(const double *grid, uint32_t gap, double *output) {
    const uint32_t cols = cols_raw + gap;
    std::fill(output, output + rows_raw * cols, 0.0);
    for (uint32_t row = 0; row < rows_raw; ++row) {
        std::copy_n(grid + row * cols_raw, 11, output + row * cols);
        std::copy_n(grid + row * cols_raw + 11, 11, output + row * cols + 11 + gap);
    }
}
void dimensions(const double *xy, uint32_t count, double cw, double ch, double *result) {
    std::fill_n(result, 12, 0.0);
    if (!count) return;
    double cx = 0, cy = 0;
    for (uint32_t i = 0; i < count; ++i) { cx += xy[2*i]; cy += xy[2*i+1]; }
    cx /= count; cy /= count;
    double px = 1, py = 0, sx = 0, sy = 1;
    double width = cw, length = ch;
    if (count > 1) {
        double a = 0, b = 0, d = 0;
        for (uint32_t i = 0; i < count; ++i) {
            const double x = xy[2*i] - cx, y = xy[2*i+1] - cy;
            a += x*x; b += x*y; d += y*y;
        }
        a /= count-1; b /= count-1; d /= count-1;
        if (a == d && b == 0) {
            // Legacy argmin/argmax both choose the first eigenvector on a tie.
            px = sx = 1; py = sy = 0;
        } else {
            const double angle = std::atan2(2*b, a-d)/2;
            px = std::cos(angle); py = std::sin(angle); sx = -py; sy = px;
        }
        double pmin = std::numeric_limits<double>::infinity(), smin = pmin;
        double pmax = -pmin, smax = -pmin;
        for (uint32_t i = 0; i < count; ++i) {
            const double x = xy[2*i]-cx, y = xy[2*i+1]-cy;
            const double p = x*px+y*py, s = x*sx+y*sy;
            pmin = std::min(pmin,p); pmax = std::max(pmax,p);
            smin = std::min(smin,s); smax = std::max(smax,s);
        }
        cx += px*(pmax+pmin)/2 + sx*(smax+smin)/2;
        cy += py*(pmax+pmin)/2 + sy*(smax+smin)/2;
        const double diagonal = std::hypot(cw,ch);
        width = (smax-smin+diagonal)*1.025;
        length = (pmax-pmin+diagonal)*1.025;
    }
    result[0] = width; result[1] = length; result[2] = cx; result[3] = cy;
    if (count == 1) {
        const double corners[] = {cx-width/2,cy-length/2,cx+width/2,cy-length/2,
                                  cx+width/2,cy+length/2,cx-width/2,cy+length/2};
        std::copy_n(corners,8,result+4);
    } else {
        const double signs[][2] = {{1,1},{1,-1},{-1,-1},{-1,1}};
        for (uint32_t i=0;i<4;++i) {
            result[4+2*i] = cx+signs[i][0]*px*length/2+signs[i][1]*sx*width/2;
            result[5+2*i] = cy+signs[i][0]*py*length/2+signs[i][1]*sy*width/2;
        }
    }
}
double percentile(const std::vector<double>& values, double fraction) {
    const double position = (values.size()-1)*fraction;
    const auto lower = static_cast<size_t>(position);
    const auto upper = std::min(lower+1,values.size()-1);
    return values[lower]+(values[upper]-values[lower])*(position-lower);
}
std::vector<uint8_t> fixed(uint8_t header, uint8_t type, uint16_t value=0) {
    return {0x24,header,type,0,static_cast<uint8_t>(value>>8),static_cast<uint8_t>(value),0xff,0xf0};
}
std::vector<uint8_t> variable(uint8_t header, uint8_t type, const uint8_t *data, uint32_t length) {
    std::vector<uint8_t> command = {0x24,header,type,0};
    if (length) command.insert(command.end(),data,data+length);
    command.insert(command.end(),{0xff,0xf0});
    return command;
}
} // namespace

extern "C" {
const char *fisica_core_version(void) { return FISICA_CORE_VERSION; }
uint32_t fisica_core_abi_version(void) { return (FISICA_ABI_MAJOR<<16)|FISICA_ABI_MINOR; }
const char *fisica_core_policy_json(void) { return FISICA_CORE_POLICY; }
const char *fisica_core_error_message(int32_t status) {
    switch (status) {
    case FISICA_OK: return "OK";
    case FISICA_INVALID_ARGUMENT: return "Invalid input, shape or non-finite value";
    case FISICA_BUFFER_TOO_SMALL: return "Output buffer is too small";
    case FISICA_NO_RESULT: return "No complete packet or usable measurement";
    case FISICA_UNKNOWN_COMMAND: return "Unknown command";
    case FISICA_INCOMPATIBLE_VERSION: return "Unsupported SDK, core or wrapper version";
    case FISICA_INVALID_STATE: return "Operation is invalid in the current session state";
    case FISICA_BUSY: return "Another command is awaiting ACK";
    case FISICA_COMMAND_TIMEOUT: return "Command ACK timed out; device outcome is uncertain";
    case FISICA_DEVICE_REJECTED: return "Device rejected the command";
    case FISICA_CANCELLED: return "Operation was cancelled";
    case FISICA_TRANSPORT_LOST: return "Transport was lost during a command";
    default: return "Native core internal error";
    }
}
int32_t fisica_core_preprocess(const double *grid,uint32_t rows,uint32_t cols,double *output,uint32_t capacity) {
    if (!output || !valid_grid(grid,rows,cols)) return FISICA_INVALID_ARGUMENT;
    if (uint64_t(rows)*cols>capacity) return FISICA_BUFFER_TOO_SMALL;
    return protect([&]() -> int32_t { preprocess(grid,rows,cols,output); return FISICA_OK; });
}
int32_t fisica_core_display_grid(const double *grid,uint32_t gap,double *output,uint32_t capacity) {
    if (!output || gap>1024 || !valid_grid(grid,rows_raw,cols_raw)) return FISICA_INVALID_ARGUMENT;
    if (rows_raw*(cols_raw+gap)>capacity) return FISICA_BUFFER_TOO_SMALL;
    display_grid(grid,gap,output); return FISICA_OK;
}
int32_t fisica_core_decode_packet(const uint8_t *packet,uint32_t length,double *raw,uint32_t raw_capacity,
    double *display,uint32_t display_capacity,double *metrics,uint32_t metrics_capacity) {
    // Preserve the legacy decode-only contract; stream framing validates footers.
    if (!packet || length!=packet_size || !raw || !display || !metrics)
        return FISICA_INVALID_ARGUMENT;
    if (raw_capacity<cells_raw || display_capacity<cells_display || metrics_capacity<2) return FISICA_BUFFER_TOO_SMALL;
    return protect([&]() -> int32_t {
        for (uint32_t i=0;i<cells_raw;++i) raw[i]=packet[i+1];
        std::array<double,cells_raw> processed{};
        preprocess(raw,rows_raw,cols_raw,processed.data());
        display_grid(processed.data(),7,display);
        metrics[0]=((uint16_t(packet[641])<<8)|packet[642])/10.0;
        metrics[1]=99.0; // Legacy placeholder; no new firmware assumption.
        return FISICA_OK;
    });
}
int32_t fisica_core_next_packet(const uint8_t *buffer,uint32_t length,uint32_t *consumed,uint32_t *kind,
    uint8_t *packet,uint32_t capacity,uint32_t *packet_length) {
    if ((!buffer && length) || !consumed || !kind || !packet || !packet_length) return FISICA_INVALID_ARGUMENT;
    *consumed=0; *kind=FISICA_PACKET_NONE; *packet_length=0;
    uint32_t start=length, size=0, selected_kind=0;
    // Prefer a complete measurement over command-shaped bytes inside its pressure grid.
    if (length>=packet_size) {
        for (uint32_t i=0;i<=length-packet_size;++i) {
            if (measurement_packet(buffer+i)) {
                start=i; size=packet_size; selected_kind=FISICA_PACKET_MEASUREMENT; break;
            }
        }
    }
    if (length>=8) {
        for (uint32_t i=0;i<=length-8 && i<start;++i) {
            // Until a complete frame is available, bytes inside a fragmented
            // pressure grid must not be interpreted as a command response.
            if (!size && i>0) break;
            if (buffer[i]!=0x24) continue;
            uint32_t response_size=8;
            if (variable_response(buffer[i+1],buffer[i+2])) {
                response_size=0;
                const uint32_t available=std::min<uint32_t>(length-i,4096);
                for (uint32_t end=6;end+1<available;++end) {
                    if (buffer[i+end]==0xff && buffer[i+end+1]==0xf0) { response_size=end+2; break; }
                }
            }
            if (response_size && response_packet(buffer+i,response_size)) {
                start=i; size=response_size; selected_kind=FISICA_PACKET_RESPONSE; break;
            }
        }
    }
    if (!size) {
        // Bound noise while preserving every possible partial 645-byte frame.
        if (length>4096) *consumed=length-(packet_size-1);
        return FISICA_NO_RESULT;
    }
    *packet_length=size;
    if (capacity<size) return FISICA_BUFFER_TOO_SMALL;
    std::memcpy(packet,buffer+start,size);
    *consumed=start+size; *kind=selected_kind; return FISICA_OK;
}
int32_t fisica_core_decode_response(const uint8_t *packet,uint32_t length,int32_t *response,uint32_t capacity) {
    if (!response || !response_packet(packet,length))
        return FISICA_INVALID_ARGUMENT;
    if (capacity<6) return FISICA_BUFFER_TOO_SMALL;
    for (uint32_t i=0;i<5;++i) response[i]=packet[i+1];
    response[5]=packet[3]==1; return FISICA_OK;
}
int32_t fisica_core_response_payload(const uint8_t *packet,uint32_t length,uint8_t *output,
    uint32_t capacity,uint32_t *output_length) {
    if (!output || !output_length || !response_packet(packet,length)) return FISICA_INVALID_ARGUMENT;
    *output_length=length-6;
    if (capacity<*output_length) return FISICA_BUFFER_TOO_SMALL;
    std::memcpy(output,packet+4,*output_length); return FISICA_OK;
}
int32_t fisica_core_dimensions(const double *xy,uint32_t count,double cw,double ch,double *result,uint32_t capacity) {
    if (!result || (!xy && count) || count>max_cells || !std::isfinite(cw) || !std::isfinite(ch) || cw<=0 || ch<=0)
        return FISICA_INVALID_ARGUMENT;
    if (capacity<12) return FISICA_BUFFER_TOO_SMALL;
    for (uint64_t i=0;i<uint64_t(count)*2;++i) if (!std::isfinite(xy[i])) return FISICA_INVALID_ARGUMENT;
    return protect([&]() -> int32_t { dimensions(xy,count,cw,ch,result); return FISICA_OK; });
}
int32_t fisica_core_cop(const double *grid,uint32_t rows,uint32_t cols,double cw,double ch,double *result,uint32_t capacity) {
    if (!result || !valid_grid(grid,rows,cols) || !std::isfinite(cw) || !std::isfinite(ch) || cw<=0 || ch<=0)
        return FISICA_INVALID_ARGUMENT;
    if (capacity<2) return FISICA_BUFFER_TOO_SMALL;
    double weight=0,x=0,y=0;
    for (uint32_t row=0;row<rows;++row) for (uint32_t col=0;col<cols;++col) {
        const double value=grid[row*cols+col]; weight+=value;
        x+=value*(col*cw+cw/2); y+=value*(row*ch+ch/2);
    }
    result[0]=weight ? x/weight : 0; result[1]=weight ? y/weight : 0; return FISICA_OK;
}
int32_t fisica_core_analyze(const double *matrices,const double *weights,uint32_t count,uint32_t rows,uint32_t cols,
    double *result,uint32_t capacity) {
    if (!result || !matrices || !weights || !count || !rows || cols<2 ||
        uint64_t(rows)*cols>max_cells || count>max_cells/(uint64_t(rows)*cols))
        return FISICA_INVALID_ARGUMENT;
    if (capacity<5) return FISICA_BUFFER_TOO_SMALL;
    const uint32_t cells=rows*cols;
    for (uint32_t i=0;i<count;++i)
        if (!std::isfinite(weights[i]) || !valid_grid(matrices+uint64_t(i)*cells,rows,cols)) return FISICA_INVALID_ARGUMENT;
    return protect([&]() -> int32_t {
        std::vector<uint32_t> active, stable;
        for (uint32_t i=0;i<count;++i) {
            bool nonzero=false;
            for (uint32_t c=0;c<cells;++c) if (matrices[uint64_t(i)*cells+c]!=0) { nonzero=true; break; }
            if (nonzero) active.push_back(i);
        }
        for (size_t i=1;i<active.size();++i) {
            if (std::abs(weights[active[i]]-weights[active[i-1]])<=0.5) stable.push_back(active[i]);
            else stable.clear();
            if (stable.size()>=10) break;
        }
        if (stable.empty()) return FISICA_NO_RESULT;
        double avg=0;
        for (auto i:stable) avg+=weights[i];
        result[0]=std::nearbyint((avg/stable.size())*100)/100;
        std::vector<double> grid(cells), samples(stable.size());
        for (uint32_t c=0;c<cells;++c) {
            for (size_t i=0;i<stable.size();++i) samples[i]=static_cast<float>(matrices[uint64_t(stable[i])*cells+c]);
            std::sort(samples.begin(),samples.end());
            const double q1=percentile(samples,0.25),q3=percentile(samples,0.75),iqr=q3-q1;
            float sum=0; uint32_t kept=0;
            for (auto i:stable) {
                const float sample=static_cast<float>(matrices[uint64_t(i)*cells+c]);
                if (sample>=q1-1.5*iqr && sample<=q3+1.5*iqr) { sum+=sample; ++kept; }
            }
            grid[c]=kept ? static_cast<float>(sum/kept) : 0;
        }
        for (uint32_t side=0;side<2;++side) {
            const uint32_t begin=side ? cols/2 : 0, end=side ? cols : cols/2;
            std::vector<double> xy;
            for (uint32_t row=0;row<rows;++row) for (uint32_t col=begin;col<end;++col)
                if (grid[row*cols+col]>0) { xy.push_back(col-begin+0.5); xy.push_back(row+0.5); }
            double box[12]; dimensions(xy.data(),static_cast<uint32_t>(xy.size()/2),1,1,box);
            result[1+side*2]=box[0]; result[2+side*2]=box[1];
        }
        return FISICA_OK;
    });
}
int32_t fisica_core_command(const char *name,double value,const uint8_t *payload,uint32_t payload_length,
    uint8_t *output,uint32_t capacity,uint32_t *output_length) {
    if (!name || !output || !output_length || (!payload && payload_length) || payload_length>4096 || !std::isfinite(value))
        return FISICA_INVALID_ARGUMENT;
    *output_length=0;
    return protect([&]() -> int32_t {
        std::vector<uint8_t> bytes;
        const std::string command(name);
        struct Named { const char *name; uint8_t header,type; uint16_t value; };
        const Named commands[] = {
            {"SET_DEVICE_STOP",0x22,0x53,0},{"SET_DEVICE_BLE",0x22,0x53,1},{"SET_DEVICE_USB",0x22,0x53,2},
            {"START_CONTINUOUS_MEASUREMENT",0x51,0x53,1},{"STOP_CONTINUOUS_MEASUREMENT",0x51,0x53,0},
            {"SET_AUTO_MODE_ON",0x21,0x53,1},{"SET_AUTO_MODE_OFF",0x21,0x53,0},
            {"GET_CURRENT_WEIGHT",0x14,0x47,0},{"GET_BATTERY_LEVEL",0x31,0x47,0},
            {"GET_FIRMWARE_VERSION",0x43,0x47,0},{"GET_AUTO_MODE_STATUS",0x21,0x47,0},
            {"SET_WEIGHT_ZERO",0x11,0x53,0},{"SET_WEIGHT_SCALE_TEST_ZERO",0x10,0x53,0xcc00},
            {"SET_WEIGHT_SCALE",0x10,0x53,0},{"SET_WAKEUP",0x32,0x53,0},
            {"SET_BLEMODE_V2",0x22,0x53,1},{"SET_USBMODE_V2",0x22,0x53,2},
            {"START_MEASUREMENT_AUTO_V2",0x51,0x53,1},{"STOP_MEASUREMENT_V2",0x51,0x53,0},
            {"stop_single_measurement",0x50,0x53,0}
        };
        for (const auto& item:commands) if (command==item.name) { bytes=fixed(item.header,item.type,item.value); break; }
        if (command=="create_weight_scale") {
            if (value<std::numeric_limits<int16_t>::min() || value>std::numeric_limits<int16_t>::max() || std::trunc(value)!=value)
                return FISICA_INVALID_ARGUMENT;
            bytes=fixed(0x10,0x53,static_cast<uint16_t>(static_cast<int32_t>(value)));
        } else if (command=="set_min_weight" || command=="set_measurement_count" || command=="set_measurement_interval") {
            const double scaled=command=="set_min_weight" ? std::trunc(value*10) : value;
            if (scaled<0 || scaled>65535 || std::trunc(scaled)!=scaled) return FISICA_INVALID_ARGUMENT;
            const uint8_t header=command=="set_min_weight" ? 0x12 : command=="set_measurement_count" ? 0x20 : 0x23;
            bytes=fixed(header,0x53,static_cast<uint16_t>(scaled));
        } else if (command=="set_wifi_credentials") {
            bytes=variable(0x49,0x53,payload,payload_length);
        } else if (command=="start_single_measurement") {
            if (payload_length!=2) return FISICA_INVALID_ARGUMENT;
            const uint8_t data[]={1,payload[0],payload[1]}; bytes=variable(0x50,0x53,data,3);
        } else if (command=="_legacy_fixed") {
            if (payload_length!=4) return FISICA_INVALID_ARGUMENT;
            bytes=fixed(payload[0],payload[1],(uint16_t(payload[2])<<8)|payload[3]);
        } else if (command=="_legacy_variable") {
            if (payload_length<2) return FISICA_INVALID_ARGUMENT;
            bytes=variable(payload[0],payload[1],payload+2,payload_length-2);
        } else if (command=="SET_MODE1_V1" || command=="START_MEASUREMENT_V1" || command=="STOP_MEASUREMENT_V1") {
            const std::string text=command=="SET_MODE1_V1" ? "AT+MODE=1" : command=="START_MEASUREMENT_V1" ? "AT+AUTO" : "AT+STOP";
            bytes.assign(text.begin(),text.end());
        }
        if (bytes.empty()) return FISICA_UNKNOWN_COMMAND;
        *output_length=static_cast<uint32_t>(bytes.size());
        if (capacity<bytes.size()) return FISICA_BUFFER_TOO_SMALL;
        std::copy(bytes.begin(),bytes.end(),output); return FISICA_OK;
    });
}
} // extern C

namespace {
bool release_version(const char *text, std::array<uint32_t,3>& version) {
    if (!text) return false;
    for (unsigned i=0;i<3;++i) {
        if (*text<'0' || *text>'9') return false;
        if (*text=='0' && text[1]>='0' && text[1]<='9') return false;
        uint64_t number=0;
        while (*text>='0' && *text<='9') {
            number=number*10+(*text++-'0'); if (number>UINT32_MAX) return false;
        }
        version[i]=uint32_t(number);
        if (i<2 && *text++!='.') return false;
    }
    return *text==0;
}
bool in_range(const std::array<uint32_t,3>& value,const char *minimum,const char *maximum) {
    std::array<uint32_t,3> lo{},hi{};
    return release_version(minimum,lo) && release_version(maximum,hi) && value>=lo && value<hi;
}
}
extern "C" int32_t fisica_core_check_wrapper(const char *name,const char *sdk,const char *wrapper,uint32_t *warnings) {
    if (!name || !warnings) return FISICA_INVALID_ARGUMENT;
    *warnings=0;
    std::array<uint32_t,3> sdk_value{},wrapper_value{},core_value{},deprecated{};
    if (!release_version(sdk,sdk_value) || !release_version(wrapper,wrapper_value) ||
        !release_version(FISICA_CORE_VERSION,core_value)) return FISICA_INCOMPATIBLE_VERSION;
    for (const auto &policy:fisica_wrapper_policies) if (std::strcmp(policy.name,name)==0) {
        if (FISICA_ABI_MAJOR!=policy.major || FISICA_ABI_MINOR<policy.minor || !policy.schema ||
            !in_range(core_value,policy.core_min,policy.core_max) ||
            !in_range(sdk_value,FISICA_SDK_MIN,FISICA_SDK_MAX) ||
            !in_range(wrapper_value,policy.minimum,policy.maximum)) return FISICA_INCOMPATIBLE_VERSION;
        if (release_version(FISICA_SDK_DEPRECATED,deprecated) && sdk_value<deprecated) *warnings|=1;
        if (release_version(policy.deprecated,deprecated) && wrapper_value<deprecated) *warnings|=2;
        return FISICA_OK;
    }
    return FISICA_INCOMPATIBLE_VERSION;
}
