// Public JNI marshaling only. Protocol/state/analysis remain in the linked core.
#include <jni.h>
#include "fisica_core.h"
#include <array>
#include <string>
#include <vector>
static bool checked(JNIEnv *env,int status) {
    if (!status) return true;
    jclass type=env->FindClass("com/carenco/fisica/FisicaException");
    if (!type) return false;
    jmethodID constructor=env->GetMethodID(type,"<init>","(ILjava/lang/String;)V");
    if (!constructor) return false;
    auto message=env->NewStringUTF(fisica_core_error_message(status));
    auto exception=static_cast<jthrowable>(env->NewObject(type,constructor,status,message));
    if (exception) env->Throw(exception);
    return false;
}
static FisicaSession *session(jlong handle) { return reinterpret_cast<FisicaSession*>(intptr_t(handle)); }
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
    std::array<uint32_t,8> event{}; std::array<uint8_t,4102> data{};
    int status=fisica_core_session_poll(session(h),event.data(),event.size(),data.data(),data.size());
    if (status==FISICA_NO_RESULT) return nullptr; if (!checked(e,status)) return nullptr;
    e->SetIntArrayRegion(meta,0,8,reinterpret_cast<const jint*>(event.data())); return output(e,data.data(),event[7]);
}
JNIEXPORT jdoubleArray JNICALL Java_com_carenco_fisica_Bridge_decode(JNIEnv *e,jobject,jbyteArray input) {
    auto data=bytes(e,input); std::array<double,1481> values{};
    if (!checked(e,fisica_core_decode_packet(data.data(),data.size(),values.data(),638,values.data()+638,841,values.data()+1479,2))) return nullptr;
    auto result=e->NewDoubleArray(values.size()); if (result) e->SetDoubleArrayRegion(result,0,values.size(),values.data()); return result;
}
JNIEXPORT jdoubleArray JNICALL Java_com_carenco_fisica_Bridge_analyzePackets(JNIEnv *e,jobject,jbyteArray input,jint count) {
    auto data=bytes(e,input); std::array<double,5> result{};
    if (count<=0 || uint64_t(count)*645!=data.size()) { checked(e,FISICA_INVALID_ARGUMENT); return nullptr; }
    const auto status=fisica_core_analyze_packets(data.data(),count,result.data(),result.size());
    if (status==FISICA_NO_RESULT) return nullptr; if (!checked(e,status)) return nullptr;
    auto output=e->NewDoubleArray(5); if (output) e->SetDoubleArrayRegion(output,0,5,result.data()); return output;
}
JNIEXPORT jbyteArray JNICALL Java_com_carenco_fisica_Bridge_responsePayload(JNIEnv *e,jobject,jbyteArray input) {
    auto data=bytes(e,input); std::array<uint8_t,4096> payload{}; uint32_t length=0;
    if (!checked(e,fisica_core_response_payload(data.data(),data.size(),payload.data(),payload.size(),&length))) return nullptr;
    return output(e,payload.data(),length);
}
static FisicaEntitlement *entitlement(jlong handle) { return reinterpret_cast<FisicaEntitlement*>(intptr_t(handle)); }
JNIEXPORT jlong JNICALL Java_com_carenco_fisica_Bridge_entitlementCreate(JNIEnv *e,jobject) {
    FisicaEntitlement *handle=nullptr; checked(e,fisica_core_entitlement_create(&handle)); return reinterpret_cast<intptr_t>(handle);
}
JNIEXPORT void JNICALL Java_com_carenco_fisica_Bridge_entitlementDestroy(JNIEnv *,jobject,jlong h) { fisica_core_entitlement_destroy(entitlement(h)); }
JNIEXPORT jint JNICALL Java_com_carenco_fisica_Bridge_entitlementImport(JNIEnv *e,jobject,jlong h,jbyteArray token,jlong now) {
    auto data=bytes(e,token); uint32_t outcome=0;
    checked(e,fisica_core_entitlement_import(entitlement(h),data.data(),data.size(),now,&outcome)); return outcome;
}
JNIEXPORT jlongArray JNICALL Java_com_carenco_fisica_Bridge_entitlementStatus(JNIEnv *e,jobject,jlong h,jlong now) {
    std::array<uint64_t,12> status{};
    if (!checked(e,fisica_core_entitlement_status(entitlement(h),now,status.data(),status.size()))) return nullptr;
    auto result=e->NewLongArray(12); if (result) e->SetLongArrayRegion(result,0,12,reinterpret_cast<const jlong*>(status.data())); return result;
}
JNIEXPORT jbyteArray JNICALL Java_com_carenco_fisica_Bridge_entitlementText(JNIEnv *e,jobject,jlong h,jint field) {
    std::array<uint8_t,4096> text{}; uint32_t length=0;
    if (!checked(e,fisica_core_entitlement_text(entitlement(h),field,text.data(),text.size(),&length))) return nullptr;
    return output(e,text.data(),length);
}
JNIEXPORT void JNICALL Java_com_carenco_fisica_Bridge_entitlementMarkUsed(JNIEnv *e,jobject,jlong h,jlong now) { checked(e,fisica_core_entitlement_mark_used(entitlement(h),now)); }
// Returns the bearer token; body receives the JSON to send. Null when nothing is pending.
JNIEXPORT jbyteArray JNICALL Java_com_carenco_fisica_Bridge_entitlementReport(JNIEnv *e,jobject,jlong h,jbyteArray id,jobjectArray body) {
    auto identifier=bytes(e,id); std::array<uint8_t,4096> token{}; std::array<uint8_t,256> json{}; uint32_t token_length=0,json_length=0;
    const auto status=fisica_core_entitlement_report(entitlement(h),identifier.data(),identifier.size(),token.data(),token.size(),&token_length,json.data(),json.size(),&json_length);
    if (status==FISICA_NO_RESULT) return nullptr; if (!checked(e,status)) return nullptr;
    e->SetObjectArrayElement(body,0,output(e,json.data(),json_length)); return output(e,token.data(),token_length);
}
JNIEXPORT void JNICALL Java_com_carenco_fisica_Bridge_entitlementReportDone(JNIEnv *e,jobject,jlong h) { checked(e,fisica_core_entitlement_report_done(entitlement(h))); }
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
    auto output=e->NewDoubleArray(5); if (output) e->SetDoubleArrayRegion(output,0,5,result.data()); return output;
}
}
