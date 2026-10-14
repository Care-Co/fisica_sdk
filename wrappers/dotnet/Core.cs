using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;
namespace CareCo.Fisica;
public sealed class FisicaException(int code, string message) : Exception(message) { public int Code { get; } = code; }
/// <summary>One decoded measurement. Packet is the 645 bytes as received, Raw the 29x22 sensor grid and
/// Display the processed 29x29 grid used for analysis. Relative sensor units, not calibrated kPa.</summary>
public sealed record Measurement(byte[] Packet, double[] Raw, double[] Display, double[] Metrics) { public double Weight => Metrics[0]; public bool BatteryValid => false; }
public sealed record SessionEvent(uint[] Metadata, byte[] Payload) {
    public uint Kind => Metadata[0]; public uint State => Metadata[1]; public uint Status => Metadata[2];
    public uint DeviceError => Metadata[3]; public uint Operation => Metadata[4];
}
public sealed class FisicaCore {
    static bool configured;
    static readonly object gate = new();
    public string Version => Marshal.PtrToStringUTF8(Native.fisica_core_version())!;
    public string PolicyJson => Marshal.PtrToStringUTF8(Native.fisica_core_policy_json())!;
    // One trusted core binary per process; device state is isolated per session.
    public FisicaCore(string? libraryPath=null, Action<string>? onDeprecation=null) {
        lock(gate) { if (!configured) {
            NativeLibrary.SetDllImportResolver(typeof(Native).Assembly, (name, assembly, path) =>
                name=="fisica_core" ? NativeLibrary.Load(libraryPath ?? (OperatingSystem.IsWindows()?"fisica_core.dll":"libfisica_core.dylib"),assembly,path) : IntPtr.Zero);
            configured=true;
        } }
        uint abi=Native.fisica_core_abi_version(); if ((abi>>16)!=1 || (abi&65535)<3) throw new FisicaException(12,"Require C ABI 1.3+");
        Check(Native.fisica_core_check_wrapper("dotnet",FisicaRelease.SdkVersion,FisicaRelease.WrapperVersion,out uint warnings));
        onDeprecation ??= message => System.Diagnostics.Trace.TraceWarning(message);
        if ((warnings&1)!=0) onDeprecation($"SDK {FisicaRelease.SdkVersion} is deprecated");
        if ((warnings&2)!=0) onDeprecation($"C# wrapper {FisicaRelease.WrapperVersion} is deprecated");
    }
    internal static void Check(int status) { if (status!=0) throw new FisicaException(status,Marshal.PtrToStringUTF8(Native.fisica_core_error_message(status))!); }
    public byte[] Command(string name,double value=0,byte[]? payload=null) {
        if (name.Contains('\0')) throw new ArgumentException("NUL in command"); payload ??= []; var data=new byte[4102];
        Check(Native.fisica_core_command(name,value,payload,(uint)payload.Length,data,(uint)data.Length,out uint length)); return data[..(int)length];
    }
    public Measurement Decode(byte[] packet) {
        var raw=new double[638]; var display=new double[841]; var metrics=new double[2];
        Check(Native.fisica_core_decode_packet(packet,(uint)packet.Length,raw,638,display,841,metrics,2)); return new((byte[])packet.Clone(),raw,display,metrics);
    }
    /// <summary>Payload bytes of a fixed or variable-length command response.</summary>
    public byte[] ResponsePayload(byte[] packet) {
        var data=new byte[4096];
        Check(Native.fisica_core_response_payload(packet,(uint)packet.Length,data,(uint)data.Length,out uint length)); return data[..(int)length];
    }
    /// <summary>Weight and foot dimensions from measurement packets as received: [weight_kg, left_width_cm,
    /// left_length_cm, right_width_cm, right_length_cm], or null without a stable measurement.
    /// The analysis itself runs in the shared core.</summary>
    public double[]? AnalyzePackets(IReadOnlyList<byte[]> packets) {
        if (packets.Count==0 || packets.Any(p => p.Length!=645)) throw new ArgumentException("Expected 645-byte measurement packets");
        var joined=new byte[packets.Count*645]; for (int i=0;i<packets.Count;i++) packets[i].CopyTo(joined,i*645);
        var output=new double[5]; int status=Native.fisica_core_analyze_packets(joined,(uint)packets.Count,output,5);
        if (status==3) return null; Check(status); return output;
    }
    public double[]? Analyze(double[] matrices,double[] weights,uint rows,uint cols) {
        if ((ulong)weights.Length*rows*cols!=(ulong)matrices.Length) throw new ArgumentException("Matrix length mismatch");
        var output=new double[5]; int status=Native.fisica_core_analyze(matrices,weights,(uint)weights.Length,rows,cols,output,5);
        if (status==3) return null; Check(status); return output;
    }
}
internal sealed class SessionHandle : SafeHandleZeroOrMinusOneIsInvalid {
    internal SessionHandle() : base(true) { FisicaCore.Check(Native.fisica_core_session_create(out var pointer)); SetHandle(pointer); }
    protected override bool ReleaseHandle() { Native.fisica_core_session_destroy(handle); return true; }
}
public sealed class FisicaSession(FisicaCore core) : IDisposable {
    readonly SessionHandle handle=new(); readonly object gate=new(); readonly FisicaCore owner=core;
    void Valid(long now) { if(handle.IsClosed) throw new ObjectDisposedException(nameof(FisicaSession)); if(now<0) throw new ArgumentOutOfRangeException(nameof(now)); }
    public void Dispose() { lock(gate) handle.Dispose(); }
    public void Action(uint action,long value,long nowMs) { lock(gate) { Valid(nowMs); FisicaCore.Check(Native.fisica_core_session_action(handle,action,value,(ulong)nowMs)); } }
    public void Tick(long nowMs) { lock(gate) { Valid(nowMs); FisicaCore.Check(Native.fisica_core_session_tick(handle,(ulong)nowMs)); } }
    public void Feed(byte[] bytes,long nowMs) { lock(gate) { Valid(nowMs); FisicaCore.Check(Native.fisica_core_session_feed(handle,bytes,(uint)bytes.Length,(ulong)nowMs)); } }
    public void Command(string name,long nowMs,double value=0,byte[]? payload=null) { lock(gate) {
        Valid(nowMs); if(name.Contains('\0')) throw new ArgumentException("NUL in command"); payload??=[];
        FisicaCore.Check(Native.fisica_core_session_command(handle,name,value,payload,(uint)payload.Length,(ulong)nowMs));
    } }
    public SessionEvent? Poll() { lock(gate) { Valid(0); var meta=new uint[8]; var bytes=new byte[4102];
        int status=Native.fisica_core_session_poll(handle,meta,8,bytes,(uint)bytes.Length); if(status==3) return null;
        FisicaCore.Check(status); return new(meta,bytes[..(int)meta[7]]);
    } }
    public List<SessionEvent> Drain() { lock(gate) { var events=new List<SessionEvent>(); while(Poll() is {} next) events.Add(next); return events; } }
}
internal static class Native {
    const string Library="fisica_core";
    [DllImport(Library,CallingConvention=CallingConvention.Cdecl)] internal static extern IntPtr fisica_core_version();
    [DllImport(Library,CallingConvention=CallingConvention.Cdecl)] internal static extern uint fisica_core_abi_version();
    [DllImport(Library,CallingConvention=CallingConvention.Cdecl)] internal static extern IntPtr fisica_core_policy_json();
    [DllImport(Library,CallingConvention=CallingConvention.Cdecl)] internal static extern IntPtr fisica_core_error_message(int status);
    [DllImport(Library,CallingConvention=CallingConvention.Cdecl)] internal static extern int fisica_core_check_wrapper([MarshalAs(UnmanagedType.LPUTF8Str)] string name,[MarshalAs(UnmanagedType.LPUTF8Str)] string sdk,[MarshalAs(UnmanagedType.LPUTF8Str)] string version,out uint warnings);
    [DllImport(Library,CallingConvention=CallingConvention.Cdecl)] internal static extern int fisica_core_command([MarshalAs(UnmanagedType.LPUTF8Str)] string name,double value,byte[] payload,uint length,[Out] byte[] output,uint capacity,out uint size);
    [DllImport(Library,CallingConvention=CallingConvention.Cdecl)] internal static extern int fisica_core_decode_packet(byte[] data,uint length,[Out] double[] raw,uint rawCapacity,[Out] double[] display,uint displayCapacity,[Out] double[] metrics,uint metricsCapacity);
    [DllImport(Library,CallingConvention=CallingConvention.Cdecl)] internal static extern int fisica_core_analyze(double[] matrices,double[] weights,uint count,uint rows,uint cols,[Out] double[] result,uint capacity);
    [DllImport(Library,CallingConvention=CallingConvention.Cdecl)] internal static extern int fisica_core_analyze_packets(byte[] packets,uint count,[Out] double[] result,uint capacity);
    [DllImport(Library,CallingConvention=CallingConvention.Cdecl)] internal static extern int fisica_core_response_payload(byte[] packet,uint length,[Out] byte[] output,uint capacity,out uint size);
    [DllImport(Library,CallingConvention=CallingConvention.Cdecl)] internal static extern int fisica_core_entitlement_create(out IntPtr handle);
    [DllImport(Library,CallingConvention=CallingConvention.Cdecl)] internal static extern void fisica_core_entitlement_destroy(IntPtr handle);
    [DllImport(Library,CallingConvention=CallingConvention.Cdecl,EntryPoint="fisica_core_entitlement_import")] internal static extern int fisica_core_entitlement_import(EntitlementHandle handle,byte[] token,uint length,ulong now,out uint outcome);
    [DllImport(Library,CallingConvention=CallingConvention.Cdecl)] internal static extern int fisica_core_entitlement_status(EntitlementHandle handle,ulong now,[Out] ulong[] status,uint capacity);
    [DllImport(Library,CallingConvention=CallingConvention.Cdecl)] internal static extern int fisica_core_entitlement_text(EntitlementHandle handle,uint field,[Out] byte[] output,uint capacity,out uint length);
    [DllImport(Library,CallingConvention=CallingConvention.Cdecl)] internal static extern int fisica_core_entitlement_export(EntitlementHandle handle,[Out] byte[] output,uint capacity,out uint length);
    [DllImport(Library,CallingConvention=CallingConvention.Cdecl)] internal static extern int fisica_core_entitlement_restore(EntitlementHandle handle,byte[] state,uint length);
    [DllImport(Library,CallingConvention=CallingConvention.Cdecl)] internal static extern int fisica_core_session_create(out IntPtr handle);
    [DllImport(Library,CallingConvention=CallingConvention.Cdecl)] internal static extern void fisica_core_session_destroy(IntPtr handle);
    [DllImport(Library,CallingConvention=CallingConvention.Cdecl)] internal static extern int fisica_core_session_action(SessionHandle handle,uint action,long value,ulong now);
    [DllImport(Library,CallingConvention=CallingConvention.Cdecl)] internal static extern int fisica_core_session_tick(SessionHandle handle,ulong now);
    [DllImport(Library,CallingConvention=CallingConvention.Cdecl)] internal static extern int fisica_core_session_feed(SessionHandle handle,byte[] data,uint length,ulong now);
    [DllImport(Library,CallingConvention=CallingConvention.Cdecl)] internal static extern int fisica_core_session_command(SessionHandle handle,[MarshalAs(UnmanagedType.LPUTF8Str)] string name,double value,byte[] payload,uint length,ulong now);
    [DllImport(Library,CallingConvention=CallingConvention.Cdecl)] internal static extern int fisica_core_session_poll(SessionHandle handle,[Out] uint[] meta,uint metaCapacity,[Out] byte[] output,uint capacity);
}
