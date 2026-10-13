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
    /// <summary>Store state per company and contract under the user's local application data.</summary>
    public const string AutoStorage="*";
    static readonly HttpClient http=new() { Timeout=TimeSpan.FromSeconds(5) };
    readonly EntitlementHandle handle=new(); readonly object gate=new(); readonly Func<ulong> clock; readonly FisicaCore owner;
    readonly string? endpoint, storage; readonly bool online;
    readonly SemaphoreSlim exchange=new(1,1);  // One exchange at a time, so a report is never confirmed twice.
    long nextSync; int syncing;
    FisicaEntitlementWarning warned;
    /// <summary>Raised with newly appeared warnings so the app can show or log them.</summary>
    public event Action<FisicaEntitlementWarning,EntitlementStatus>? Warning;
    /// <summary>Last storage or network problem, or null. Never an entitlement state.</summary>
    public string? LastSyncError { get; private set; }
    /// <param name="endpoint">Server base URL for online confirmation and the minimal usage report. Without it no network is used.</param>
    /// <param name="storage">Directory for the state file, AutoStorage for the default location, or null for memory only.</param>
    /// <param name="online">False keeps a configured endpoint unused.</param>
    public FisicaEntitlement(FisicaCore core,Func<ulong>? clock=null,string? endpoint=null,string? storage=AutoStorage,bool online=true) {
        owner=core; this.clock=clock ?? (() => (ulong)DateTimeOffset.UtcNow.ToUnixTimeSeconds());
        this.endpoint=endpoint?.TrimEnd('/'); this.online=online;
        this.storage=storage==AutoStorage ? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),"Fisica","sdk") : storage;
    }
    public void Dispose() { lock(gate) handle.Dispose(); }
    void Open() { if(handle.IsClosed) throw new ObjectDisposedException(nameof(FisicaEntitlement)); }

    /// <summary>Returns what happened to the token. A rejected or older token leaves verified information untouched.</summary>
    public FisicaEntitlementOutcome ImportToken(string token) {
        uint outcome;
        lock(gate) {
            Open(); var data=Encoding.UTF8.GetBytes(token);
            FisicaCore.Check(Native.fisica_core_entitlement_import(handle,data,(uint)data.Length,clock(),out outcome));
            if (outcome==(uint)FisicaEntitlementOutcome.Accepted) {
                // Stored usage and a possibly newer confirmation win over an older file.
                try {
                    if (StateFile() is {} file && File.Exists(file)) {
                        var state=File.ReadAllBytes(file);
                        if (Native.fisica_core_entitlement_restore(handle,state,(uint)state.Length)==0)
                            Native.fisica_core_entitlement_import(handle,data,(uint)data.Length,clock(),out _);
                    }
                } catch (Exception error) when (error is IOException or UnauthorizedAccessException) { LastSyncError="storage: "+error.Message; }
                Save();
            }
            Notify();
        }
        if (outcome==(uint)FisicaEntitlementOutcome.Accepted) Sync();
        return (FisicaEntitlementOutcome)outcome;
    }
    string? StateFile() {
        if (storage==null) return null;
        string company=Text(FisicaEntitlementText.Company); if (company.Length==0) return null;
        // Named by company and contract, the only attribution unit; never an installation or device id.
        var name=new string((company+"-"+Text(FisicaEntitlementText.Contract)).Select(c => char.IsAsciiLetterOrDigit(c) || c=='_' || c=='-' ? c : '_').ToArray());
        return Path.Combine(storage,"entitlement-"+name+".state");
    }
    void Save() {
        try {
            if (StateFile() is not {} file) return;
            Directory.CreateDirectory(Path.GetDirectoryName(file)!);
            var data=new byte[262144];
            FisicaCore.Check(Native.fisica_core_entitlement_export(handle,data,(uint)data.Length,out uint length));
            File.WriteAllBytes(file+".tmp",data[..(int)length]); File.Move(file+".tmp",file,true);
        } catch (Exception error) when (error is IOException or UnauthorizedAccessException) { LastSyncError="storage: "+error.Message; }
    }
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
    public void MarkUsed() {
        lock(gate) { if(handle.IsClosed) return; Native.fisica_core_entitlement_mark_used(handle,clock()); Save(); Notify(); }
        Sync();
    }

    async Task<byte[]> Post(string path,string token,byte[] body) {
        using var request=new HttpRequestMessage(HttpMethod.Post,endpoint+path) { Content=new ByteArrayContent(body) };
        request.Headers.Authorization=new("Bearer",token); request.Content.Headers.ContentType=new("application/json");
        using var response=await http.SendAsync(request).ConfigureAwait(false);
        response.EnsureSuccessStatusCode();
        return await response.Content.ReadAsByteArrayAsync().ConfigureAwait(false);
    }
    /// <summary>Online confirmation, then the pending usage reports. Returns null or the problem. A failure
    /// changes no entitlement state: it is never an expiry or a revocation.</summary>
    public async Task<string?> SyncAsync() {
        if (!online || endpoint==null) return null;
        await exchange.WaitAsync().ConfigureAwait(false);
        try {
            string token; lock(gate) { if(handle.IsClosed) return null; token=Text(FisicaEntitlementText.Token); }
            if (token.Length==0) return null;  // Nothing verified: nothing to refresh, nothing attributable to report.
            using var refreshed=System.Text.Json.JsonDocument.Parse(await Post("/sdk/v1/entitlement/refresh",token,"{}"u8.ToArray()).ConfigureAwait(false));
            var data=Encoding.ASCII.GetBytes(refreshed.RootElement.GetProperty("token").GetString()!);
            lock(gate) { if(!handle.IsClosed) Native.fisica_core_entitlement_import(handle,data,(uint)data.Length,clock(),out _); }
            while (true) {
                (string Token,byte[] Body)? report; lock(gate) report=handle.IsClosed ? null : PendingReport(System.Security.Cryptography.RandomNumberGenerator.GetBytes(16));
                if (report is not {} pending) break;
                await Post("/sdk/v1/usage",pending.Token,pending.Body).ConfigureAwait(false);
                lock(gate) { if(!handle.IsClosed) ReportDone(); }  // Cleared only after the server confirmed.
            }
            LastSyncError=null;
        } catch (Exception error) when (error is HttpRequestException or TaskCanceledException or System.Text.Json.JsonException or KeyNotFoundException or InvalidOperationException or FisicaException) {
            LastSyncError="network: "+error.Message;
        } finally { exchange.Release(); }
        lock(gate) { if(!handle.IsClosed) { Save(); Notify(); } }
        return LastSyncError;
    }
    /// <summary>Background SyncAsync, at most hourly unless forced. Never blocks or throws into the caller.</summary>
    public void Sync(bool force=false) {
        if (!online || endpoint==null) return;
        long now=Environment.TickCount64;
        if (!force && now<Interlocked.Read(ref nextSync)) return;
        if (Interlocked.CompareExchange(ref syncing,1,0)!=0) return;
        Interlocked.Exchange(ref nextSync,now+3_600_000);
        _=Task.Run(async () => { try { await SyncAsync().ConfigureAwait(false); } catch (Exception) { /* Recorded in LastSyncError. */ } finally { Volatile.Write(ref syncing,0); } });
    }
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
