// Public JNI marshaling only. Protocol/state/analysis and licensing remain in the linked core.
#include <jni.h>
#include "fisica_core.h"
#include <array>
#include <string>
#include <vector>
// FEATURE_NOT_LICENSED is a license decision, not a measurement or communication error: it
// gets its own exception type so apps can tell basic mode from a failure.
static bool checked(JNIEnv *env,int status) {
    if (!status) return true;
    const bool license=status==FISICA_FEATURE_NOT_LICENSED;
    jclass type=env->FindClass(license ? "com/carenco/fisica/FisicaLicenseException" : "com/carenco/fisica/FisicaException");
    if (!type) return false;
    jmethodID constructor=env->GetMethodID(type,"<init>",license ? "(Ljava/lang/String;)V" : "(ILjava/lang/String;)V");
    if (!constructor) return false;
    auto message=env->NewStringUTF(fisica_core_error_message(status));
    auto exception=static_cast<jthrowable>(license ? env->NewObject(type,constructor,message) : env->NewObject(type,constructor,status,message));
    if (exception) env->Throw(exception);
    return false;
}
static FisicaSession *session(jlong handle) { return reinterpret_cast<FisicaSession*>(intptr_t(handle)); }
static FisicaEntitlement *entitlement(jlong handle) { return reinterpret_cast<FisicaEntitlement*>(intptr_t(handle)); }
static std::vector<uint8_t> bytes(JNIEnv *env,jbyteArray input) {
    const auto length=env->GetArrayLength(input); std::vector<uint8_t> result(length);
    if (length) env->GetByteArrayRegion(input,0,length,reinterpret_cast<jbyte*>(result.data())); return result;
}
static std::string text(JNIEnv *env,jstring input) {
    const char *value=env->GetStringUTFChars(input,nullptr);
    if (!value) return {}; std::string result(value); env->ReleaseStringUTFChars(input,value); return result;
}
static jbyteArray output(JNIEnv *env,const uint8_t *data,uint32_t size) {
    auto array=env->NewByteArray(size); if (array && size) env->SetByteArrayRegion(array,0,size,reinterpret_cast<const jbyte*>(data)); return array;
}
static jdoubleArray doubles(JNIEnv *env,const double *data,uint32_t size) {
    auto array=env->NewDoubleArray(size); if (array && size) env->SetDoubleArrayRegion(array,0,size,data); return array;
}
extern "C" {
JNIEXPORT jstring JNICALL Java_com_carenco_fisica_Bridge_version(JNIEnv *e,jobject) { return e->NewStringUTF(fisica_core_version()); }
JNIEXPORT jint JNICALL Java_com_carenco_fisica_Bridge_abi(JNIEnv *,jobject) { return fisica_core_abi_version(); }
JNIEXPORT jstring JNICALL Java_com_carenco_fisica_Bridge_policy(JNIEnv *e,jobject) { return e->NewStringUTF(fisica_core_policy_json()); }
JNIEXPORT jint JNICALL Java_com_carenco_fisica_Bridge_checkWrapper(JNIEnv *e,jobject,jstring name,jstring sdk,jstring version) {
    uint32_t notices=0; auto n=text(e,name),s=text(e,sdk),v=text(e,version);
    checked(e,fisica_core_check_wrapper(n.c_str(),s.c_str(),v.c_str(),&notices)); return notices;
}
JNIEXPORT jlong JNICALL Java_com_carenco_fisica_Bridge_create(JNIEnv *e,jobject) {
    FisicaSession *handle=nullptr; checked(e,fisica_core_session_create(&handle)); return reinterpret_cast<intptr_t>(handle);
}
JNIEXPORT void JNICALL Java_com_carenco_fisica_Bridge_destroy(JNIEnv *,jobject,jlong h) { fisica_core_session_destroy(session(h)); }
JNIEXPORT void JNICALL Java_com_carenco_fisica_Bridge_action(JNIEnv *e,jobject,jlong h,jint a,jlong v,jlong now) { checked(e,fisica_core_session_action(session(h),a,v,now)); }
JNIEXPORT void JNICALL Java_com_carenco_fisica_Bridge_tick(JNIEnv *e,jobject,jlong h,jlong now) { checked(e,fisica_core_session_tick(session(h),now)); }
JNIEXPORT void JNICALL Java_com_carenco_fisica_Bridge_feed(JNIEnv *e,jobject,jlong h,jbyteArray input,jlong now) {
    auto data=bytes(e,input); checked(e,fisica_core_session_feed(session(h),data.data(),data.size(),now));
}
JNIEXPORT jbyteArray JNICALL Java_com_carenco_fisica_Bridge_command(JNIEnv *e,jobject,jlong h,jstring name,jdouble value,jbyteArray payload,jlong now) {
    auto n=text(e,name); auto input=bytes(e,payload); std::array<uint8_t,4102> data{}; uint32_t length=0;
    int status=h ? fisica_core_session_command(session(h),n.c_str(),value,input.data(),input.size(),now) :
        fisica_core_command(n.c_str(),value,input.data(),input.size(),data.data(),data.size(),&length);
    if (!checked(e,status)) return nullptr; return output(e,data.data(),length);
}
JNIEXPORT jbyteArray JNICALL Java_com_carenco_fisica_Bridge_poll(JNIEnv *e,jobject,jlong h,jintArray meta) {
    if (e->GetArrayLength(meta)!=8) { checked(e,FISICA_INVALID_ARGUMENT); return nullptr; }
    // EVENT_BYTES holds every event, including a full-mode MEASUREMENT (grids and packet). A stack
    // buffer, not zero-filled: nothing is allocated per poll except the Java array of the payload.
    std::array<uint32_t,8> event{}; std::array<uint8_t,FISICA_LIMIT_EVENT_BYTES> data;
    int status=fisica_core_session_poll(session(h),event.data(),event.size(),data.data(),data.size());
    if (status==FISICA_NO_RESULT) return nullptr; if (!checked(e,status)) return nullptr;
    e->SetIntArrayRegion(meta,0,8,reinterpret_cast<const jint*>(event.data())); return output(e,data.data(),event[7]);
}
// The session's license decision (license 0: basic mode). now_s: wall clock, now_ms: the session clock.
JNIEXPORT void JNICALL Java_com_carenco_fisica_Bridge_sessionLicense(JNIEnv *e,jobject,jlong h,jlong license,jlong nowS,jlong nowMs) {
    checked(e,fisica_core_session_license(session(h),entitlement(license),nowS,nowMs));
}
JNIEXPORT jdoubleArray JNICALL Java_com_carenco_fisica_Bridge_sessionAnalyze(JNIEnv *e,jobject,jlong h) {
    std::array<double,5> result{}; const auto status=fisica_core_session_analyze(session(h),result.data(),result.size());
    if (status==FISICA_NO_RESULT) return nullptr; if (!checked(e,status)) return nullptr;
    return doubles(e,result.data(),result.size());
}
// [raw 638, display 841, metrics 2]; needs a license that opens RAW_DATA (license 0: none).
JNIEXPORT jdoubleArray JNICALL Java_com_carenco_fisica_Bridge_decode(JNIEnv *e,jobject,jlong license,jlong nowS,jbyteArray input) {
    auto data=bytes(e,input); std::array<double,1481> values{};
    if (!checked(e,fisica_core_decode_packet(entitlement(license),nowS,data.data(),data.size(),values.data(),638,
        values.data()+638,841,values.data()+1479,2))) return nullptr;
    return doubles(e,values.data(),values.size());
}
JNIEXPORT jdoubleArray JNICALL Java_com_carenco_fisica_Bridge_analyzePackets(JNIEnv *e,jobject,jbyteArray input,jint count) {
    auto data=bytes(e,input); std::array<double,5> result{};
    if (count<=0 || uint64_t(count)*645!=data.size()) { checked(e,FISICA_INVALID_ARGUMENT); return nullptr; }
    const auto status=fisica_core_analyze_packets(data.data(),count,result.data(),result.size());
    if (status==FISICA_NO_RESULT) return nullptr; if (!checked(e,status)) return nullptr;
    return doubles(e,result.data(),result.size());
}
JNIEXPORT jbyteArray JNICALL Java_com_carenco_fisica_Bridge_responsePayload(JNIEnv *e,jobject,jbyteArray input) {
    auto data=bytes(e,input); std::array<uint8_t,4096> payload{}; uint32_t length=0;
    if (!checked(e,fisica_core_response_payload(data.data(),data.size(),payload.data(),payload.size(),&length))) return nullptr;
    return output(e,payload.data(),length);
}
JNIEXPORT jlong JNICALL Java_com_carenco_fisica_Bridge_entitlementCreate(JNIEnv *e,jobject) {
    FisicaEntitlement *handle=nullptr; checked(e,fisica_core_entitlement_create(&handle)); return reinterpret_cast<intptr_t>(handle);
}
JNIEXPORT void JNICALL Java_com_carenco_fisica_Bridge_entitlementDestroy(JNIEnv *,jobject,jlong h) { fisica_core_entitlement_destroy(entitlement(h)); }
JNIEXPORT jint JNICALL Java_com_carenco_fisica_Bridge_entitlementImport(JNIEnv *e,jobject,jlong h,jbyteArray token,jlong now) {
    auto data=bytes(e,token); uint32_t outcome=0;
    checked(e,fisica_core_entitlement_import(entitlement(h),data.data(),data.size(),now,&outcome)); return outcome;
}
JNIEXPORT jlongArray JNICALL Java_com_carenco_fisica_Bridge_entitlementStatus(JNIEnv *e,jobject,jlong h,jlong now) {
    std::array<uint64_t,FISICA_LIMIT_STATUS_FIELDS> status{};
    if (!checked(e,fisica_core_entitlement_status(entitlement(h),now,status.data(),status.size()))) return nullptr;
    auto result=e->NewLongArray(status.size());
    if (result) e->SetLongArrayRegion(result,0,status.size(),reinterpret_cast<const jlong*>(status.data())); return result;
}
// null when the core has no such text now (INVALID_STATE: no target, or no unactivated key for a code).
JNIEXPORT jbyteArray JNICALL Java_com_carenco_fisica_Bridge_entitlementText(JNIEnv *e,jobject,jlong h,jint field) {
    std::vector<uint8_t> value(FISICA_LIMIT_TOKEN_BYTES); uint32_t length=0;
    const auto status=fisica_core_entitlement_text(entitlement(h),field,value.data(),value.size(),&length);
    if (status==FISICA_INVALID_STATE) return nullptr; if (!checked(e,status)) return nullptr;
    return output(e,value.data(),length);
}
JNIEXPORT jint JNICALL Java_com_carenco_fisica_Bridge_entitlementNextExchange(JNIEnv *e,jobject,jlong h,jlong now) {
    uint32_t exchange=0;
    if (!checked(e,fisica_core_entitlement_next_exchange(entitlement(h),now,&exchange))) return 0;
    return jint(exchange);
}
JNIEXPORT jbyteArray JNICALL Java_com_carenco_fisica_Bridge_entitlementRelease(JNIEnv *e,jobject,jlong h) {
    std::array<uint8_t,1024> code{}; uint32_t length=0;
    if (!checked(e,fisica_core_entitlement_release(entitlement(h),code.data(),code.size(),&length))) return nullptr;
    return output(e,code.data(),length);
}
JNIEXPORT jbyteArray JNICALL Java_com_carenco_fisica_Bridge_entitlementExport(JNIEnv *e,jobject,jlong h) {
    std::vector<uint8_t> state(262144); uint32_t length=0;
    if (!checked(e,fisica_core_entitlement_export(entitlement(h),state.data(),state.size(),&length))) return nullptr;
    return output(e,state.data(),length);
}
JNIEXPORT void JNICALL Java_com_carenco_fisica_Bridge_entitlementRestore(JNIEnv *e,jobject,jlong h,jbyteArray state) {
    auto data=bytes(e,state); checked(e,fisica_core_entitlement_restore(entitlement(h),data.data(),data.size()));
}
JNIEXPORT jdoubleArray JNICALL Java_com_carenco_fisica_Bridge_analyze(JNIEnv *e,jobject,jdoubleArray input,jdoubleArray weights,jint rows,jint cols) {
    const auto count=e->GetArrayLength(weights),length=e->GetArrayLength(input);
    if (rows<=0 || cols<=0 || uint64_t(count)*uint32_t(rows)*uint32_t(cols)!=uint64_t(length)) { checked(e,FISICA_INVALID_ARGUMENT); return nullptr; }
    std::vector<double> matrices(length),values(count);
    e->GetDoubleArrayRegion(input,0,length,matrices.data()); e->GetDoubleArrayRegion(weights,0,count,values.data());
    std::array<double,5> result{}; const auto status=fisica_core_analyze(matrices.data(),values.data(),count,rows,cols,result.data(),result.size());
    if (status==FISICA_NO_RESULT) return nullptr; if (!checked(e,status)) return nullptr;
    return doubles(e,result.data(),result.size());
}
}
