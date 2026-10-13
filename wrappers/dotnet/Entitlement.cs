using System.Text;
using Microsoft.Win32.SafeHandles;
namespace CareCo.Fisica;

/// <summary>Three independent facts. Nothing here enables or disables SDK features. Times are Unix seconds, 0 when not set.</summary>
/// <param name="BaseLicense">Granted with genuine hardware; it has no date expiry.</param>
/// <param name="Verification">How recently the server confirmed this information (30 days at most).</param>
/// <param name="Contract">State of the verified company's contract, by server-signed dates only.</param>
public sealed record EntitlementStatus(FisicaBaseLicense BaseLicense, FisicaVerification Verification, FisicaContract Contract,
    FisicaContractKind ContractKind, string Company, string ContractReference, IReadOnlyList<string> Grants,
    ulong VerifiedAt, ulong RefreshBy, ulong ContractStart, ulong ContractEnd, ulong RevokedAt,
    FisicaEntitlementOutcome LastImport, FisicaKeyEnvironment KeyEnvironment, FisicaEntitlementWarning Warnings);

internal sealed class EntitlementHandle : SafeHandleZeroOrMinusOneIsInvalid {
    internal EntitlementHandle() : base(true) { FisicaCore.Check(Native.fisica_core_entitlement_create(out var pointer)); SetHandle(pointer); }
    protected override bool ReleaseHandle() { Native.fisica_core_entitlement_destroy(handle); return true; }
}

/// <summary>One SDK instance's verified company/contract context. Import a server-signed token or file;
/// status and warnings never block connection, measurement or analysis.</summary>
public sealed class FisicaEntitlement : IDisposable {
    readonly EntitlementHandle handle=new(); readonly object gate=new(); readonly Func<ulong> clock; readonly FisicaCore owner;
    FisicaEntitlementWarning warned;
    /// <summary>Raised with newly appeared warnings so the app can show or log them.</summary>
    public event Action<FisicaEntitlementWarning,EntitlementStatus>? Warning;
    public FisicaEntitlement(FisicaCore core,Func<ulong>? clock=null) { owner=core; this.clock=clock ?? (() => (ulong)DateTimeOffset.UtcNow.ToUnixTimeSeconds()); }
    public void Dispose() { lock(gate) handle.Dispose(); }
    void Open() { if(handle.IsClosed) throw new ObjectDisposedException(nameof(FisicaEntitlement)); }

    /// <summary>Returns what happened to the token. A rejected or older token leaves verified information untouched.</summary>
    public FisicaEntitlementOutcome ImportToken(string token) { lock(gate) {
        Open(); var data=Encoding.UTF8.GetBytes(token);
        FisicaCore.Check(Native.fisica_core_entitlement_import(handle,data,(uint)data.Length,clock(),out uint outcome));
        Notify(); return (FisicaEntitlementOutcome)outcome;
    } }
    /// <summary>Offline setup: a file downloaded on another PC works on a PC that was never online.</summary>
    public FisicaEntitlementOutcome ImportFile(string path) => ImportToken(File.ReadAllText(path));
    string Text(FisicaEntitlementText field) {
        var data=new byte[4096];
        FisicaCore.Check(Native.fisica_core_entitlement_text(handle,(uint)field,data,(uint)data.Length,out uint length)); return Encoding.UTF8.GetString(data,0,(int)length);
    }
    public EntitlementStatus Status() { lock(gate) {
        Open(); var values=new ulong[12];
        FisicaCore.Check(Native.fisica_core_entitlement_status(handle,clock(),values,12));
        return new(FisicaBaseLicense.Granted,(FisicaVerification)values[1],(FisicaContract)values[2],(FisicaContractKind)values[3],
            Text(FisicaEntitlementText.Company),Text(FisicaEntitlementText.Contract),
            Text(FisicaEntitlementText.Grants).Split(',',StringSplitOptions.RemoveEmptyEntries),
            values[4],values[5],values[6],values[7],values[8],(FisicaEntitlementOutcome)values[9],(FisicaKeyEnvironment)values[10],(FisicaEntitlementWarning)values[11]);
    } }
    void Notify() {
        var status=Status(); var fresh=status.Warnings & ~warned; warned=status.Warnings;
        if (fresh!=0) Warning?.Invoke(fresh,status);
    }
    /// <summary>Record real feature use (connect, measure, analyze). Does nothing without a verified contract.</summary>
    public void MarkUsed() { lock(gate) { if(handle.IsClosed) return; Native.fisica_core_entitlement_mark_used(handle,clock()); Notify(); } }
    /// <summary>Next pending usage report: bearer token and JSON body for the host to send. reportId is 16 random
    /// bytes naming this report for retry de-duplication only. Null when nothing is pending.</summary>
    public (string Token,byte[] Body)? PendingReport(byte[] reportId) { lock(gate) {
        Open(); var token=new byte[4096]; var body=new byte[256];
        int status=Native.fisica_core_entitlement_report(handle,reportId,(uint)reportId.Length,token,(uint)token.Length,out uint tokenLength,body,(uint)body.Length,out uint bodyLength);
        if (status==3) return null; FisicaCore.Check(status);
        return (Encoding.ASCII.GetString(token,0,(int)tokenLength),body[..(int)bodyLength]);
    } }
    /// <summary>Call only after the server confirmed the report; the local record is then cleared.</summary>
    public void ReportDone() { lock(gate) { Open(); FisicaCore.Check(Native.fisica_core_entitlement_report_done(handle)); } }
    /// <summary>Opaque state for the host to store. Restoring verifies every stored token again.</summary>
    public byte[] ExportState() { lock(gate) {
        Open(); var data=new byte[262144];
        FisicaCore.Check(Native.fisica_core_entitlement_export(handle,data,(uint)data.Length,out uint length)); return data[..(int)length];
    } }
    public void RestoreState(byte[] state) { lock(gate) { Open(); FisicaCore.Check(Native.fisica_core_entitlement_restore(handle,state,(uint)state.Length)); Notify(); } }
}
