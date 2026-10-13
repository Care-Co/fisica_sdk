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
