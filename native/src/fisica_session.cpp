#include "fisica_core.h"
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

    void emit(uint32_t kind, uint32_t status=0, uint32_t error=0,
              uint32_t operation=0, const std::vector<uint8_t>& bytes={}) {
        if (events.size() >= 1024) throw std::overflow_error("Drain session events");
        uint32_t header = bytes.size() >= 3 ? bytes[1] : 0;
        uint32_t type = bytes.size() >= 3 ? bytes[2] : 0;
        events.push_back({{kind,state,status,error,operation,header,type,uint32_t(bytes.size())},bytes});
    }
    void transition(uint32_t next) { if (state!=next) { state=next; emit(FISICA_STATE); } }
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
        if (owned) { transition(FISICA_RELEASING); send(RELEASE,"SET_DEVICE_STOP"); }
        else { transition(FISICA_DISCONNECTED); emit(FISICA_RESULT,uncertain ? FISICA_TRANSPORT_LOST : FISICA_OK,0,FISICA_CLOSE); }
    }
    void begin() {
        transition(FISICA_STARTING);
        if (owned) send(BEGIN,"START_CONTINUOUS_MEASUREMENT");
        else send(MODE,mode==2 ? "SET_DEVICE_USB" : "SET_DEVICE_BLE");
    }
    void end() { transition(FISICA_STOPPING); send(END,"STOP_CONTINUOUS_MEASUREMENT"); }
    void fault(uint32_t status, uint32_t device_error=0, const std::vector<uint8_t>& bytes={}) {
        const auto op=operation(); const auto old=phase;
        clear_pending();
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
            if (closing) { if (state==FISICA_MEASURING) end(); else release(); }
            else if (stop_requested) {
                if (state==FISICA_MEASURING) end();
                else { stop_requested=false; emit(FISICA_RESULT,0,0,FISICA_END); }
            }
            break;
        default: break;
        }
    }
};

namespace {
template<class F> int32_t protect(FisicaSession *s, F operation) noexcept {
    if (!s) return FISICA_INVALID_ARGUMENT;
    try { return operation(); }
    catch (...) { s->events.clear(); s->state=FISICA_FAULT; s->desired=false; s->clear_pending(); return FISICA_INTERNAL_ERROR; }
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
            s->desired=true; s->stop_requested=false; s->duration=value; s->timer_active=false; s->measurement_deadline=0; s->begin();
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
            s->stop_requested=action==FISICA_STOP;
            if (s->phase!=NONE) break; // Complete pending ACK before another command.
            if (s->state==FISICA_MEASURING) s->end();
            else if (s->closing) s->release();
            else { s->timer_active=false; s->measurement_deadline=0; s->stop_requested=false; s->emit(FISICA_RESULT,s->state==FISICA_RECOVERING ? FISICA_TRANSPORT_LOST : FISICA_OK,0,FISICA_END); }
            break;
        case FISICA_LOST: {
            const auto op=s->operation(); const bool pending=s->phase!=NONE;
            s->uncertain = s->uncertain || s->owned || s->phase==MODE;
            if (s->state==FISICA_STARTING) s->desired=false;
            s->clear_pending(); s->input.clear(); s->events.clear(); s->owned=false;
            if (value) { s->desired=false; s->transition(FISICA_FAULT); }
            else s->transition(FISICA_RECOVERING);
            if (pending) s->emit(FISICA_RESULT,FISICA_TRANSPORT_LOST,0,op);
            if (s->stop_requested && op!=FISICA_END) s->emit(FISICA_RESULT,FISICA_TRANSPORT_LOST,0,FISICA_END);
            if (s->closing && op!=FISICA_CLOSE) s->emit(FISICA_RESULT,FISICA_TRANSPORT_LOST,0,FISICA_CLOSE);
            if (!value) s->emit(FISICA_RECONNECT);
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
        if ((!bytes && length) || length>65536) return FISICA_INVALID_ARGUMENT;
        auto status=s->advance(now); if (status) return status;
        if (s->state==FISICA_DISCONNECTED || s->state==FISICA_RECOVERING) return FISICA_INVALID_STATE;
        for (uint32_t offset=0; offset<length;) {
            const auto count=std::min(uint32_t(4096),length-offset);
            s->input.insert(s->input.end(),bytes+offset,bytes+offset+count); offset+=count;
            while (!s->input.empty()) {
                std::array<uint8_t,4096> packet{}; uint32_t consumed=0,kind=0,size=0;
                status=fisica_core_next_packet(s->input.data(),s->input.size(),&consumed,&kind,packet.data(),packet.size(),&size);
                if (status!=FISICA_OK && status!=FISICA_NO_RESULT) return status;
                if (!consumed) break;
                s->input.erase(s->input.begin(),s->input.begin()+consumed);
                std::vector<uint8_t> data(packet.begin(),packet.begin()+size);
                if (kind==FISICA_PACKET_RESPONSE) s->response(data);
                if (kind==FISICA_PACKET_MEASUREMENT && (s->state==FISICA_MEASURING || s->state==FISICA_STOPPING))
                    s->emit(FISICA_MEASUREMENT,0,0,0,data);
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
}
