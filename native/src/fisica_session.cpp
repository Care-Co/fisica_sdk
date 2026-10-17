#include "fisica_core.h"
#include "fisica_license.h"
#include <algorithm>
#include <array>
#include <cstring>
#include <deque>
#include <limits>
#include <stdexcept>
#include <vector>

namespace {
struct Event { std::array<uint32_t,8> meta; std::vector<uint8_t> bytes; };
enum Phase { NONE, PROBE, MODE, BEGIN, END, RELEASE, COMMAND };
constexpr uint64_t ack_timeout = 2000;
constexpr uint64_t max_license_seconds = 10ull * 366 * 24 * 3600;
void put_double(std::vector<uint8_t>& out, double value) {
    uint64_t bits; std::memcpy(&bits,&value,sizeof bits);
    for (int i=0;i<8;++i) out.push_back(uint8_t(bits>>(8*i)));
}
}

struct FisicaSession {
    uint32_t state = FISICA_DISCONNECTED, mode = 2;
    Phase phase = NONE;
    bool uncertain = false, timer_active = false;
    bool desired = false, closing = false, owned = false, stop_requested = false;
    uint64_t now = 0, ack_deadline = 0, measurement_deadline = 0;
    int64_t duration = -1;
    std::vector<uint8_t> pending, input;
    std::deque<Event> events;
    // License copied from an entitlement context: full mode until full_until_ms (monotonic).
    bool full = false;
    uint32_t features = 0;
    uint64_t full_until_ms = 0, last_frame_ms = 0;
    bool framed = false;
    // Every measurement packet since START, for analysis in any mode.
    std::deque<std::array<uint8_t,fisica::packet_size>> received;

    void emit(uint32_t kind, uint32_t status=0, uint32_t error=0,
              uint32_t operation=0, const std::vector<uint8_t>& bytes={}) {
        if (events.size() >= 1024) {
            // A host that does not poll loses old frames, then old unrequested responses (a device that
            // floods answers), never results or state changes.
            auto old=std::find_if(events.begin(),events.end(),[](const Event& e) { return e.meta[0]==FISICA_MEASUREMENT; });
            if (old==events.end())
                old=std::find_if(events.begin(),events.end(),[](const Event& e) { return e.meta[0]==FISICA_RESPONSE; });
            if (old==events.end()) throw std::overflow_error("Drain session events");
            events.erase(old);
        }
        const bool packet = kind != FISICA_MEASUREMENT && bytes.size() >= 3;
        uint32_t header = packet ? bytes[1] : 0;
        uint32_t type = packet ? bytes[2] : 0;
        events.push_back({{kind,state,status,error,operation,header,type,uint32_t(bytes.size())},bytes});
    }
    void transition(uint32_t next) { if (state!=next) { state=next; emit(FISICA_STATE); } }
    // License times and the basic-mode frame interval use the core's own clock in customer
    // builds (fisica_license.h), so feeding packets with made-up times does not open them.
    uint32_t open_features() const { return full && fisica_monotonic_ms(now)<full_until_ms ? features : 0; }
    // Basic mode hands out display grids at a limited rate and never the packet itself.
    void measurement(const std::vector<uint8_t>& packet) {
        if (received.size()>=FISICA_LIMIT_ANALYSIS_FRAMES) received.pop_front();
        received.emplace_back();
        std::copy_n(packet.begin(),fisica::packet_size,received.back().begin());
        const uint32_t open=open_features();
        const uint64_t clock=fisica_monotonic_ms(now);
        if (!(open & FISICA_FEATURE_FULL_RATE) && framed && clock-last_frame_ms<FISICA_LIMIT_BASIC_FRAME_INTERVAL_MS) return;
        framed=true; last_frame_ms=clock;
        std::array<double,fisica::raw_cells> raw{}; std::array<double,fisica::display_cells> display{}; std::array<double,2> metrics{};
        fisica::decode_measurement(packet.data(),raw.data(),display.data(),metrics.data());
        std::vector<uint8_t> payload; payload.reserve(8*(1+fisica::display_cells+fisica::raw_cells)+fisica::packet_size);
        put_double(payload,metrics[0]);
        for (double cell:display) put_double(payload,cell);
        if (open & FISICA_FEATURE_RAW_DATA) {
            for (double cell:raw) put_double(payload,cell);
            payload.insert(payload.end(),packet.begin(),packet.end());
        }
        emit(FISICA_MEASUREMENT,0,0,0,payload);
    }
    uint32_t operation() const {
        switch (phase) {
        case PROBE: return FISICA_PROBE;
        case MODE: case BEGIN: return FISICA_BEGIN;
        case END: return FISICA_END;
        case RELEASE: return FISICA_CLOSE;
        default: return FISICA_REQUEST;
        }
    }
    void send(Phase next, const char *name, double value=0) {
        std::array<uint8_t,4102> bytes{}; uint32_t length=0;
        const auto status = fisica_core_command(name,value,nullptr,0,bytes.data(),bytes.size(),&length);
        if (status) throw std::runtime_error("Internal session command");
        pending.assign(bytes.begin(),bytes.begin()+length);
        phase=next; ack_deadline=now+ack_timeout;
        emit(FISICA_TRANSMIT,0,0,operation(),pending);
    }
    void clear_pending() { phase=NONE; pending.clear(); }
    void release() {
        // A stop asked for before the close is done once nothing measures any more.
        if (stop_requested) { stop_requested=false; emit(FISICA_RESULT,0,0,FISICA_END); }
        if (owned) { transition(FISICA_RELEASING); send(RELEASE,"SET_DEVICE_STOP"); }
        else {
            closing=false; stop_requested=false;
            transition(FISICA_DISCONNECTED); emit(FISICA_RESULT,uncertain ? FISICA_TRANSPORT_LOST : FISICA_OK,0,FISICA_CLOSE);
        }
    }
    void begin() {
        transition(FISICA_STARTING);
        if (owned) send(BEGIN,"START_CONTINUOUS_MEASUREMENT");
        else send(MODE,mode==2 ? "SET_DEVICE_USB" : "SET_DEVICE_BLE");
    }
    void end() { transition(FISICA_STOPPING); send(END,"STOP_CONTINUOUS_MEASUREMENT"); }
    // Stop or close requests that arrived while a command was pending run once it is answered.
    void finish_command() {
        if (closing) { if (state==FISICA_MEASURING) end(); else release(); }
        else if (stop_requested) {
            if (state==FISICA_MEASURING) end();
            else { stop_requested=false; emit(FISICA_RESULT,0,0,FISICA_END); }
        }
    }
    void fault(uint32_t status, uint32_t device_error=0, const std::vector<uint8_t>& bytes={}) {
        const auto op=operation(); const auto old=phase;
        // GET_* queries (type 0x47) change nothing on the Scale, so a refused or unanswered one is
        // reported and the session carries on, as after a success; only state-changing commands fault.
        const bool query=old==COMMAND && pending.size()>2 && pending[2]==0x47;
        clear_pending();
        if (query) {
            emit(FISICA_RESULT,status,device_error,op,bytes);
            finish_command();
            return;
        }
        if (old==PROBE) {
            emit(FISICA_RESULT,status,device_error,op,bytes);
            if (closing) release();
            else if (desired) begin();
            else if (stop_requested) { stop_requested=false; emit(FISICA_RESULT,0,0,FISICA_END); }
            return; // An unverified firmware probe must not lock out the Scale.
        }
        desired=false; timer_active=false; measurement_deadline=0;
        transition(FISICA_FAULT);
        emit(FISICA_RESULT,status,device_error,op,bytes);
        if (stop_requested && op!=FISICA_END) emit(FISICA_RESULT,status,device_error,FISICA_END);
        if (closing && op!=FISICA_CLOSE) emit(FISICA_RESULT,status,device_error,FISICA_CLOSE);
        stop_requested=false; closing=false;
    }
    int32_t advance(uint64_t time) {
        if (time<now || time>uint64_t(INT64_MAX)-ack_timeout) return FISICA_INVALID_ARGUMENT;
        now=time;
        if (phase!=NONE && now>=ack_deadline) fault(FISICA_COMMAND_TIMEOUT);
        if (timer_active && now>=measurement_deadline && desired) {
            desired=false; stop_requested=true;
            if (state==FISICA_MEASURING && phase==NONE) end();
            if (state==FISICA_RECOVERING) {
                timer_active=false; measurement_deadline=0; stop_requested=false;
                emit(FISICA_RESULT,FISICA_TRANSPORT_LOST,0,FISICA_END);
            }
        }
        return FISICA_OK;
    }
    void response(const std::vector<uint8_t>& packet) {
        if (phase==NONE || packet[1]!=pending[1] || packet[2]!=pending[2]) {
            emit(FISICA_RESPONSE,0,0,0,packet); return;
        }
        if (!packet[3]) { fault(FISICA_DEVICE_REJECTED,(uint32_t(packet[4])<<8)|packet[5],packet); return; }
        const auto old=phase; const auto op=operation(); clear_pending();
        switch (old) {
        case PROBE:
            emit(FISICA_RESULT,0,0,op,packet);
            if (closing) release();
            else if (desired) begin();
            else if (stop_requested) { stop_requested=false; emit(FISICA_RESULT,0,0,FISICA_END); }
            break;
        case MODE:
            owned=true; uncertain=false;
            if (desired) send(BEGIN,"START_CONTINUOUS_MEASUREMENT");
            else {
                transition(FISICA_IDLE); emit(FISICA_RESULT,FISICA_CANCELLED,0,FISICA_BEGIN);
                if (stop_requested) { stop_requested=false; emit(FISICA_RESULT,0,0,FISICA_END); }
                if (closing) release();
            }
            break;
        case BEGIN:
            transition(FISICA_MEASURING);
            if (!timer_active && duration>=0) { timer_active=true; measurement_deadline=now+uint64_t(duration); }
            emit(FISICA_RESULT,0,0,op,packet);
            if (!desired || closing) end();
            break;
        case END:
            timer_active=false; measurement_deadline=0; stop_requested=false;
            transition(FISICA_IDLE); emit(FISICA_RESULT,0,0,op,packet);
            if (closing) release();
            break;
        case RELEASE:
            owned=false; uncertain=false; closing=false;
            transition(FISICA_DISCONNECTED); emit(FISICA_RESULT,0,0,op,packet);
            break;
        case COMMAND:
            emit(FISICA_RESULT,0,0,op,packet);
            finish_command();
            break;
        default: break;
        }
    }
};

namespace {
template<class F> int32_t protect(FisicaSession *s, F operation) noexcept {
    if (!s) return FISICA_INVALID_ARGUMENT;
    try { return operation(); }
    catch (...) {
        // An internal failure faults the session and says so: the host sees the state change and
        // the pending operation's result, and a later DISCONNECT is not refused as busy.
        const uint32_t op=s->phase!=NONE ? s->operation() : 0;
        // Results and state changes already produced stay; frames, responses and bytes to send go.
        s->events.erase(std::remove_if(s->events.begin(),s->events.end(),[](const Event& e) {
            return e.meta[0]!=FISICA_RESULT && e.meta[0]!=FISICA_STATE; }),s->events.end());
        s->clear_pending();
        s->state=FISICA_FAULT; s->desired=false; s->closing=false; s->stop_requested=false;
        s->timer_active=false; s->measurement_deadline=0;
        try {
            s->events.push_back({{FISICA_STATE,FISICA_FAULT,0,0,0,0,0,0},{}});
            if (op) s->events.push_back({{FISICA_RESULT,FISICA_FAULT,FISICA_INTERNAL_ERROR,0,op,0,0,0},{}});
        } catch (...) {}
        return FISICA_INTERNAL_ERROR;
    }
}
}
extern "C" {
int32_t fisica_core_session_create(FisicaSession **session) {
    if (!session) return FISICA_INVALID_ARGUMENT;
    *session=nullptr;
    try { *session=new FisicaSession; return FISICA_OK; } catch (...) { return FISICA_INTERNAL_ERROR; }
}
void fisica_core_session_destroy(FisicaSession *session) { delete session; }
int32_t fisica_core_session_tick(FisicaSession *s,uint64_t now) {
    return protect(s,[&]{ return s->advance(now); });
}
int32_t fisica_core_session_action(FisicaSession *s,uint32_t action,int64_t value,uint64_t now) {
    return protect(s,[&]() -> int32_t {
        if (action<FISICA_CONNECTED || action>FISICA_LOST) return FISICA_INVALID_ARGUMENT;
        if (action==FISICA_CONNECTED && value!=1 && value!=2) return FISICA_INVALID_ARGUMENT;
        if (action==FISICA_START && (value<-1 || (value>=0 && uint64_t(value)>uint64_t(INT64_MAX)-now)))
            return FISICA_INVALID_ARGUMENT;
        auto status=s->advance(now); if (status) return status;
        switch (action) {
        case FISICA_CONNECTED:
            if (s->state!=FISICA_DISCONNECTED && s->state!=FISICA_RECOVERING && s->state!=FISICA_FAULT)
                return FISICA_INVALID_STATE;
            s->input.clear(); s->clear_pending(); s->owned=false; s->closing=false;
            s->mode=uint32_t(value); s->transition(FISICA_IDLE); s->send(PROBE,"GET_FIRMWARE_VERSION");
            break;
        case FISICA_START:
            if (s->phase!=NONE) return FISICA_BUSY;
            if (s->state!=FISICA_IDLE) return FISICA_INVALID_STATE;
            s->desired=true; s->stop_requested=false; s->duration=value; s->timer_active=false; s->measurement_deadline=0;
            s->received.clear(); s->framed=false; s->begin();
            break;
        case FISICA_STOP:
        case FISICA_DISCONNECT:
            if (s->closing || (action==FISICA_STOP && s->stop_requested)) return FISICA_BUSY;
            s->desired=false;
            if (s->state==FISICA_FAULT) {
                if (action==FISICA_STOP) return FISICA_INVALID_STATE;
                s->transition(FISICA_DISCONNECTED); s->emit(FISICA_RESULT,FISICA_INVALID_STATE,0,FISICA_CLOSE);
                break; // Outcome is uncertain: do not pretend device mode was released.
            }
            s->closing=action==FISICA_DISCONNECT;
            s->stop_requested=s->stop_requested || action==FISICA_STOP;  // a STOP before a DISCONNECT still gets its result
            if (s->phase!=NONE) break; // Complete pending ACK before another command.
            if (s->state==FISICA_MEASURING) s->end();
            else if (s->closing) s->release();
            else { s->timer_active=false; s->measurement_deadline=0; s->stop_requested=false; s->emit(FISICA_RESULT,s->state==FISICA_RECOVERING ? FISICA_TRANSPORT_LOST : FISICA_OK,0,FISICA_END); }
            break;
        case FISICA_LOST: {
            if (s->state==FISICA_DISCONNECTED) break;  // nothing left to lose
            const auto op=s->operation(); const bool pending=s->phase!=NONE;
            const bool closing=s->closing || s->state==FISICA_RELEASING;
            s->uncertain = s->uncertain || s->owned || s->phase==MODE;
            if (s->state==FISICA_STARTING) s->desired=false;
            // Bytes not sent yet are void; results and frames already produced still count.
            s->clear_pending(); s->input.clear(); s->owned=false;
            s->events.erase(std::remove_if(s->events.begin(),s->events.end(),
                [](const Event& e) { return e.meta[0]==FISICA_TRANSMIT; }),s->events.end());
            // Lost while closing: the close ends here (the device's mode is uncertain); no reconnect.
            if (value) { s->desired=false; s->transition(FISICA_FAULT); }
            else s->transition(closing ? FISICA_DISCONNECTED : FISICA_RECOVERING);
            if (pending) s->emit(FISICA_RESULT,FISICA_TRANSPORT_LOST,0,op);
            if (s->stop_requested && op!=FISICA_END) s->emit(FISICA_RESULT,FISICA_TRANSPORT_LOST,0,FISICA_END);
            if (closing && op!=FISICA_CLOSE) s->emit(FISICA_RESULT,FISICA_TRANSPORT_LOST,0,FISICA_CLOSE);
            if (!value && !closing) s->emit(FISICA_RECONNECT);
            s->closing=false; s->stop_requested=false;
            break;
        }
        }
        return FISICA_OK;
    });
}
int32_t fisica_core_session_command(FisicaSession *s,const char *name,double value,
                                    const uint8_t *payload,uint32_t length,uint64_t now) {
    return protect(s,[&]() -> int32_t {
        std::array<uint8_t,4102> bytes{}; uint32_t size=0;
        auto status=fisica_core_command(name,value,payload,length,bytes.data(),bytes.size(),&size);
        if (status) return status;
        if (size<8 || bytes[0]!=0x24 || bytes[1]==0x22 || bytes[1]==0x51 || bytes[1]==0x43)
            return FISICA_INVALID_ARGUMENT;
        // Only commands whose answer the session can recognize (fisica_core.cpp response_packet).
        if (bytes[1]<0x10 || bytes[1]>0x60 || (bytes[2]!=0x47 && bytes[2]!=0x53)) return FISICA_INVALID_ARGUMENT;
        status=s->advance(now); if (status) return status;
        if (s->phase!=NONE) return FISICA_BUSY;
        if (s->state!=FISICA_IDLE && s->state!=FISICA_MEASURING) return FISICA_INVALID_STATE;
        s->phase=COMMAND; s->pending.assign(bytes.begin(),bytes.begin()+size); s->ack_deadline=now+ack_timeout;
        s->emit(FISICA_TRANSMIT,0,0,FISICA_REQUEST,s->pending);
        return FISICA_OK;
    });
}
int32_t fisica_core_session_feed(FisicaSession *s,const uint8_t *bytes,uint32_t length,uint64_t now) {
    return protect(s,[&]() -> int32_t {
        if ((!bytes && length) || length>FISICA_LIMIT_FEED_BYTES) return FISICA_INVALID_ARGUMENT;
        auto status=s->advance(now); if (status) return status;
        if (s->state==FISICA_DISCONNECTED || s->state==FISICA_RECOVERING) return FISICA_INVALID_STATE;
        for (uint32_t offset=0; offset<length;) {
            const auto count=std::min(uint32_t(4096),length-offset);
            s->input.insert(s->input.end(),bytes+offset,bytes+offset+count); offset+=count;
            while (!s->input.empty()) {
                std::array<uint8_t,4096> packet{}; uint32_t consumed=0,kind=0,size=0;
                const bool streaming=s->state==FISICA_MEASURING || s->state==FISICA_STOPPING;
                const uint8_t *expected=s->phase!=NONE && !streaming && s->pending.size()>2 ? s->pending.data()+1 : nullptr;
                status=fisica::next_packet(s->input.data(),s->input.size(),&consumed,&kind,packet.data(),packet.size(),&size,expected);
                if (status!=FISICA_OK && status!=FISICA_NO_RESULT) return status;
                if (!consumed) break;
                s->input.erase(s->input.begin(),s->input.begin()+consumed);
                std::vector<uint8_t> data(packet.begin(),packet.begin()+size);
                if (kind==FISICA_PACKET_RESPONSE) s->response(data);
                if (kind==FISICA_PACKET_MEASUREMENT && (s->state==FISICA_MEASURING || s->state==FISICA_STOPPING))
                    s->measurement(data);
            }
        }
        return FISICA_OK;
    });
}
int32_t fisica_core_session_poll(FisicaSession *s,uint32_t *event,uint32_t event_capacity,
                               uint8_t *payload,uint32_t capacity) {
    if (!s || !event) return FISICA_INVALID_ARGUMENT;
    if (event_capacity<8) return FISICA_BUFFER_TOO_SMALL;
    if (s->events.empty()) return FISICA_NO_RESULT;
    const auto &next=s->events.front(); std::copy(next.meta.begin(),next.meta.end(),event);
    if (next.bytes.size()>capacity) return FISICA_BUFFER_TOO_SMALL;
    if (!next.bytes.empty() && !payload) return FISICA_INVALID_ARGUMENT;
    if (!next.bytes.empty()) std::copy(next.bytes.begin(),next.bytes.end(),payload);
    s->events.pop_front(); return FISICA_OK;
}
int32_t fisica_core_session_license(FisicaSession *s,FisicaEntitlement *license,uint64_t now_s,uint64_t now_ms) {
    return protect(s,[&]() -> int32_t {
        if (now_s>fisica_max_wall_seconds) return FISICA_INVALID_ARGUMENT;
        auto status=s->advance(now_ms); if (status) return status;
        now_s=fisica_wall_seconds(now_s);
        const FisicaLicenseView view=fisica_license_view(license,now_s);
        s->full=view.mode==FISICA_MODE_FULL && view.until>now_s;
        s->features=s->full ? view.features : 0;
        s->full_until_ms=s->full ? fisica_monotonic_ms(s->now)+std::min(view.until-now_s,max_license_seconds)*1000 : 0;
        return FISICA_OK;
    });
}
int32_t fisica_core_session_analyze(FisicaSession *s,double *result,uint32_t capacity) {
    // Read-only: a failure here is reported and never changes the session.
    if (!s || !result) return FISICA_INVALID_ARGUMENT;
    if (capacity<5) return FISICA_BUFFER_TOO_SMALL;
    if (s->received.empty()) return FISICA_NO_RESULT;
    return fisica::analyze_packets(uint32_t(s->received.size()),[&](uint32_t i) { return s->received[i].data(); },result,capacity);
}
}
