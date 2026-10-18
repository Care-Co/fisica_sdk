using System.Text;
using System.Text.Json;
using Microsoft.Win32.SafeHandles;
namespace CareCo.Fisica;

/// <summary>The license as the shared core decides it now. Times are Unix seconds, 0 when not set; texts are empty when not set.</summary>
/// <param name="Mode">Full: an SDK key activated for this app or PC, within its key expiry and its trial or contract.
/// Basic otherwise: connection, measurement, settings, weight and foot size work; raw data and full-rate frames do not.</param>
/// <param name="Reason">Why the license is in basic mode; None in full mode. Message explains it.</param>
/// <param name="Features">Features open now; none in basic mode.</param>
/// <param name="Contract">State of the key's contract by its server-signed dates.</param>
/// <param name="Key">The SDK key (seat) ID.</param>
/// <param name="BindType">What the key is activated for (app or PC); None for a key that is not activated.</param>
/// <param name="Bind">The app ID or PC hash the key is activated for.</param>
/// <param name="TargetType">This app or PC as the core reads it; None when it cannot read one.</param>
/// <param name="Target">This app's ID or this PC's hash.</param>
/// <param name="FullUntil">End of full mode for the key's trial or contract (contract end plus grace).</param>
/// <param name="ActivationCode">FAC1 code to enter in the developer portal while the key is not activated, otherwise null.</param>
public sealed record EntitlementStatus(FisicaMode Mode, FisicaModeReason Reason, FisicaFeature Features,
    FisicaContract Contract, FisicaContractKind ContractKind, FisicaTrack Track,
    string Company, string ContractReference, string Key, IReadOnlyList<string> Grants,
    FisicaBindType BindType, string Bind, FisicaBindType TargetType, string Target,
    ulong VerifiedAt, ulong RefreshBy, ulong KeyUntil, ulong FullUntil, ulong ContractStart, ulong ContractEnd, ulong RevokedAt,
    FisicaEntitlementOutcome LastImport, FisicaKeyEnvironment KeyEnvironment, FisicaEntitlementWarning Warnings,
    string? ActivationCode) {
    public bool IsFull => Mode==FisicaMode.Full;
    /// <summary>True while the feature (or all of the given features) is open.</summary>
    public bool Has(FisicaFeature feature) => feature!=0 && (Features&feature)==feature;
    /// <summary>What basic mode means for this reason and what to do; null in full mode.</summary>
    public string? Message => FisicaEntitlement.Explain(Reason);
    /// <summary>A message for each raised warning bit, in bit order.</summary>
    public IReadOnlyList<string> WarningMessages => FisicaLicenseMessages.Warnings(Warnings);
}

/// <summary>Result of one exchange with the server. Error is set for a failed exchange, which changes nothing.</summary>
public sealed record EntitlementSync(bool Activated, bool Refreshed, string? Error);

/// <summary>What to show while pairing: an owner or admin enters UserCode at VerificationUri
/// (VerificationUriComplete already holds the code). The code is valid for ExpiresIn seconds.</summary>
public sealed record PairingCode(string UserCode, string VerificationUri, string VerificationUriComplete, int ExpiresIn);

/// <summary>How pairing ended. Approved: the key is loaded and stored.</summary>
public enum PairingResult { Approved, Denied, Expired, Cancelled }

internal sealed class EntitlementHandle : SafeHandleZeroOrMinusOneIsInvalid {
    internal EntitlementHandle() : base(true) { FisicaCore.Check(Native.fisica_core_entitlement_create(out var pointer)); SetHandle(pointer); }
    protected override bool ReleaseHandle() { Native.fisica_core_entitlement_destroy(handle); return true; }
}

/// <summary>One SDK instance's license: its SDK key, the key's activation for this app or PC, and renewal.
/// The shared core verifies the key, reads this app's ID or this PC's hash itself and decides full or basic mode;
/// it also limits what a session hands out. This class stores the key, activates and renews it online and reports
/// the status. Nothing records or reports whether, when or how often the SDK is used.</summary>
public sealed class FisicaEntitlement : IDisposable {
    /// <summary>Store state per company and contract in <see cref="DefaultStorage"/>.</summary>
    public const string AutoStorage="*";
    /// <summary>The per-user directory every Fisica SDK on the PC uses, so they find the same seat: Windows
    /// %LOCALAPPDATA%\Fisica\sdk, macOS ~/Library/Application Support/Fisica/sdk, Linux $XDG_STATE_HOME/fisica-sdk
    /// or ~/.local/state/fisica-sdk.</summary>
    public static string DefaultStorage => OperatingSystem.IsLinux()
        ? Path.Combine(Environment.GetEnvironmentVariable("XDG_STATE_HOME") is { Length: >0 } state ? state
            : Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),".local","state"),"fisica-sdk")
        : Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),"Fisica","sdk");
    // Messages shared by every wrapper (wrappers/license-messages.json, generated into Codes.cs).
    internal static string? Explain(FisicaModeReason reason) => FisicaLicenseMessages.Reason(reason);
    const string Activate="/sdk/v1/entitlement/activate", Refresh="/sdk/v1/entitlement/refresh",
        ReleasePath="/sdk/v1/entitlement/release", Pairing="/sdk/v1/pairing", PairingToken="/sdk/v1/pairing/token";
    static readonly HttpClient http=new() { Timeout=TimeSpan.FromSeconds(5) };
    readonly EntitlementHandle handle=new(); readonly object gate=new(); readonly Func<ulong> clock; readonly FisicaCore owner;
    readonly string? endpoint, storage; readonly bool online;
    readonly SemaphoreSlim exchange=new(1,1);  // One exchange with the server at a time.
    long nextSync; int syncing;
    FisicaEntitlementWarning warned; (FisicaMode,FisicaModeReason,FisicaFeature,string,ulong)? noticed;
    /// <summary>Raised with newly appeared warnings (one or more bits) so the app can show or log them;
    /// FisicaLicenseMessages.Warnings(bits) gives a message for each.</summary>
    public event Action<FisicaEntitlementWarning,EntitlementStatus>? Warning;
    /// <summary>Raised when the mode, reason, open features or key changed (import, activation, renewal, release,
    /// restore, or the end of full mode noticed by CheckStatus). A FisicaClient applies the new license by itself.
    /// Exceptions from handlers are ignored.</summary>
    public event Action<EntitlementStatus>? Changed;
    /// <summary>Last storage or network problem, or null. Never a license state.</summary>
    public string? LastSyncError { get; private set; }
    /// <param name="clock">Wall-clock Unix seconds; the system clock by default.</param>
    /// <param name="endpoint">Developer portal base URL for activation and renewal. Without it no network is used.</param>
    /// <param name="storage">Directory for the stored key, AutoStorage for the default location, or null for memory only.
    /// The key stored there for this app or PC (paired or imported earlier) is loaded at once, so an app needs no key
    /// of its own after pairing. SDK use is not tracked: only the signed key and the latest time seen are stored.</param>
    /// <param name="online">False keeps a configured endpoint unused.</param>
    public FisicaEntitlement(FisicaCore core,Func<ulong>? clock=null,string? endpoint=null,string? storage=AutoStorage,bool online=true) {
        owner=core; this.clock=clock ?? (() => (ulong)DateTimeOffset.UtcNow.ToUnixTimeSeconds());
        this.endpoint=endpoint?.TrimEnd('/'); this.online=online;
        this.storage=storage==AutoStorage ? DefaultStorage : storage;
        if (LoadStored()) { Notify(); Sync(); }
    }
    /// <summary>The developer portal, for pairing when no endpoint is given.</summary>
    public const string DefaultEndpoint="https://developer.fisicahealthcare.com";

    // The key stored for this app or PC, if any. The core names a state file from the company, the contract and the
    // target, so a file it names again after restoring it belongs here. With several, a full-mode key wins, then the
    // most recently verified one.
    bool LoadStored() {
        if (storage==null) return false;
        string[] files;
        try { files=Directory.Exists(storage) ? Directory.GetFiles(storage,"entitlement-*.state") : []; }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException) { return false; }
        (bool Full,ulong Verified)? best=null; byte[]? chosen=null;
        foreach (var file in files.Order(StringComparer.Ordinal)) {
            try {
                var state=File.ReadAllBytes(file);
                using var probe=new EntitlementHandle();
                if (Native.fisica_core_entitlement_restore(probe,state,(uint)state.Length)!=0) continue;
                var name=new byte[256];
                if (Native.fisica_core_entitlement_text(probe,(uint)FisicaEntitlementText.StateName,name,(uint)name.Length,out uint length)!=0
                    || Encoding.UTF8.GetString(name,0,(int)length)!=Path.GetFileName(file)) continue;
                var values=new ulong[(int)FisicaLimit.StatusFields];
                if (Native.fisica_core_entitlement_status(probe,clock(),values,(uint)values.Length)!=0) continue;
                var rank=((FisicaMode)values[0]==FisicaMode.Full,values[8]);
                if (best==null || rank.CompareTo(best.Value)>0) { best=rank; chosen=state; }
            } catch (Exception error) when (error is IOException or UnauthorizedAccessException) { }
        }
        lock(gate) return chosen!=null && Native.fisica_core_entitlement_restore(handle,chosen,(uint)chosen.Length)==0;
    }
    public void Dispose() { lock(gate) handle.Dispose(); }
    void Open() { if(handle.IsClosed) throw new ObjectDisposedException(nameof(FisicaEntitlement)); }
    // The context and wall-clock seconds for a core call that needs a license, serialized with every other use.
    internal T Use<T>(Func<IntPtr,ulong,T> call) { lock(gate) { Open(); return call(handle.DangerousGetHandle(),clock()); } }

    /// <summary>Import an SDK key or key file content (an activation or renewal file from the developer portal).
    /// Returns what happened to it; a rejected or older key, or one activated for another app or PC, leaves the current
    /// key untouched. A key activated or renewed earlier and stored here wins over an older original key file.
    /// Online, a new key is activated for this app or PC in the background.</summary>
    public FisicaEntitlementOutcome ImportToken(string token) {
        uint outcome;
        lock(gate) {
            Open(); var data=Encoding.UTF8.GetBytes(token); ulong now=clock();
            FisicaCore.Check(Native.fisica_core_entitlement_import(handle,data,(uint)data.Length,now,out outcome));
            if (outcome==(uint)FisicaEntitlementOutcome.Accepted) {
                Interlocked.Exchange(ref nextSync,0);  // A new key may need activation now.
                try {
                    if (StateFile() is {} file && File.Exists(file)) {
                        var state=File.ReadAllBytes(file);
                        if (Native.fisica_core_entitlement_restore(handle,state,(uint)state.Length)==0)
                            Native.fisica_core_entitlement_import(handle,data,(uint)data.Length,now,out _);
                    }
                } catch (Exception error) when (error is IOException or UnauthorizedAccessException) { LastSyncError="storage: "+error.Message; }
                Save();
            }
        }
        Notify();
        if (outcome==(uint)FisicaEntitlementOutcome.Accepted) Sync();
        return (FisicaEntitlementOutcome)outcome;
    }
    /// <summary>Offline setup: a key file from the developer portal works on a PC that is never online.</summary>
    public FisicaEntitlementOutcome ImportFile(string path) => ImportToken(File.ReadAllText(path));

    string? StateFile() {
        if (storage==null) return null;
        // One file per seat on this device, named by the core from the company, the contract and this
        // app's or PC's target, so every wrapper on a PC finds the same file. Never a usage record.
        string name=Text(FisicaEntitlementText.StateName);
        return name.Length==0 ? null : Path.Combine(storage,name);
    }
    void Save() {
        try {
            if (StateFile() is not {} file) return;
            Directory.CreateDirectory(Path.GetDirectoryName(file)!);
            var data=new byte[262144];
            FisicaCore.Check(Native.fisica_core_entitlement_export(handle,data,(uint)data.Length,out uint length));
            // A temporary file of its own: other processes and wrappers on this PC may save the same seat.
            var temporary=$"{file}.{Guid.NewGuid():N}.tmp";
            try { File.WriteAllBytes(temporary,data[..(int)length]); File.Move(temporary,file,true); }
            catch { try { File.Delete(temporary); } catch (Exception) { } throw; }
        } catch (Exception error) when (error is IOException or UnauthorizedAccessException) { LastSyncError="storage: "+error.Message; }
    }
    void Forget(string? file) {
        try { if (file!=null) File.Delete(file); }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException) { LastSyncError="storage: "+error.Message; }
    }
    string Text(FisicaEntitlementText field) => OptionalText(field) ?? "";
    // Null when the core has nothing to give (FISICA_INVALID_STATE: no target, or no unactivated key for a code).
    string? OptionalText(FisicaEntitlementText field) {
        var data=new byte[(int)FisicaLimit.TokenBytes];
        int status=Native.fisica_core_entitlement_text(handle,(uint)field,data,(uint)data.Length,out uint length);
        if (status==(int)FisicaStatus.InvalidState) return null;
        FisicaCore.Check(status); return Encoding.UTF8.GetString(data,0,(int)length);
    }
    ulong[] Values() {
        var values=new ulong[(int)FisicaLimit.StatusFields];
        FisicaCore.Check(Native.fisica_core_entitlement_status(handle,clock(),values,(uint)values.Length)); return values;
    }
    public EntitlementStatus Status() { lock(gate) { Open(); return Current(); } }
    EntitlementStatus Current() {
        var v=Values(); var reason=(FisicaModeReason)v[1];
        return new((FisicaMode)v[0],reason,(FisicaFeature)v[2],(FisicaContract)v[3],(FisicaContractKind)v[4],(FisicaTrack)v[5],
            Text(FisicaEntitlementText.Company),Text(FisicaEntitlementText.Contract),Text(FisicaEntitlementText.Key),
            Text(FisicaEntitlementText.Grants).Split(',',StringSplitOptions.RemoveEmptyEntries),
            (FisicaBindType)v[6],Text(FisicaEntitlementText.Bind),(FisicaBindType)v[7],Text(FisicaEntitlementText.Target),
            v[8],v[9],v[10],v[11],v[12],v[13],v[14],(FisicaEntitlementOutcome)v[15],(FisicaKeyEnvironment)v[16],(FisicaEntitlementWarning)v[17],
            reason==FisicaModeReason.NotActivated ? OptionalText(FisicaEntitlementText.ActivationCode) : null);
    }
    /// <summary>FAC1 code for activating the loaded key in the developer portal, for a device that is never online;
    /// null without an unactivated key.</summary>
    public string? ActivationCode() { lock(gate) { Open(); return OptionalText(FisicaEntitlementText.ActivationCode); } }
    /// <summary>Release the key activated here: the core forgets it, the stored key is deleted, and the FRC1 code to
    /// enter in the developer portal is returned. Online, the server is also told in the background, which frees the
    /// seat at once (ReleaseAsync waits for that). An activation or renewal still on its way does not bring the key back.
    /// FisicaException (InvalidState) without a key activated here.</summary>
    public string Release() {
        var (code,token)=ReleaseHere();
        if (token!=null) _=Task.Run(() => TellReleased(token));
        return code;
    }
    /// <summary>Release, and online tell the server with the key itself so the seat is free at once. Returns the
    /// FRC1 code and whether the server confirmed; without that confirmation enter the code in the developer portal.</summary>
    public async Task<(string Code,bool ReleasedOnline)> ReleaseAsync() {
        var (code,token)=ReleaseHere();
        return (code,token!=null && await TellReleased(token).ConfigureAwait(false));
    }
    (string Code,string? Token) ReleaseHere() {
        string code, token;
        // Under the same lock as importing a server answer, which SyncAsync does only while its key is still loaded.
        lock(gate) {
            Open(); var file=StateFile(); token=Text(FisicaEntitlementText.Token); var data=new byte[1024];
            FisicaCore.Check(Native.fisica_core_entitlement_release(handle,data,(uint)data.Length,out uint length));
            code=Encoding.ASCII.GetString(data,0,(int)length);
            Forget(file);
        }
        Notify();
        return (code,online && endpoint!=null && token.Length>0 ? token : null);
    }
    async Task<bool> TellReleased(string token) {
        try { await Post(ReleasePath,token,"{}"u8.ToArray()).ConfigureAwait(false); return true; }
        catch (Exception failure) { LastSyncError="network: "+failure.Message; return false; }
    }
    void Notify() {
        EntitlementStatus status; FisicaEntitlementWarning fresh; bool changed;
        lock(gate) {
            if (handle.IsClosed) return;
            status=Current(); fresh=status.Warnings & ~warned; warned=status.Warnings;
            var state=(status.Mode,status.Reason,status.Features,status.Key,status.VerifiedAt);
            changed=noticed!=state; noticed=state;
        }
        // Handlers run outside the lock; a failing handler never breaks licensing.
        if (fresh!=0) try { Warning?.Invoke(fresh,status); } catch (Exception) { }
        if (changed) try { Changed?.Invoke(status); } catch (Exception) { }
    }
    /// <summary>Re-evaluate the license for the current time and activate or renew in the background when due.
    /// Called from feature paths so a long-running app notices an ending key or contract. It records nothing about
    /// the use, never blocks and never throws.</summary>
    public void CheckStatus() {
        try { Notify(); Sync(); } catch (Exception error) { LastSyncError="check: "+error.Message; }
    }

    async Task<(int Status,byte[] Data)> Send(string path,string? token,byte[] body) {
        using var request=new HttpRequestMessage(HttpMethod.Post,endpoint+path) { Content=new ByteArrayContent(body) };
        if (token!=null) request.Headers.Authorization=new("Bearer",token);
        request.Content.Headers.ContentType=new("application/json");
        using var response=await http.SendAsync(request).ConfigureAwait(false);
        return ((int)response.StatusCode,await response.Content.ReadAsByteArrayAsync().ConfigureAwait(false));
    }
    async Task<byte[]> Post(string path,string? token,byte[] body) {
        var (status,data)=await Send(path,token,body).ConfigureAwait(false);
        // A refused activation (key in use elsewhere, released, trial already used) is reported with the server's answer.
        if (status is < 200 or > 299) { var text=Encoding.UTF8.GetString(data); throw new HttpRequestException($"{status} {text[..Math.Min(200,text.Length)]}"); }
        return data;
    }

    /// <summary>Get a key without one: the developer portal approves a code shown here (device authorization).
    /// Only this app's ID or this PC's hash is sent. onCode receives the code to show; an owner or admin of the
    /// organization enters it at VerificationUri and approves it with a new or unused key. The key, already activated
    /// for this app or PC, then arrives, is loaded and stored, and later instances load it by themselves.
    /// Waits until the code is approved, denied or expires, or cancel is set. Throws InvalidOperationException without
    /// an endpoint or a readable app ID or PC, and HttpRequestException when pairing cannot start.</summary>
    public async Task<PairingResult> PairAsync(Action<PairingCode> onCode,CancellationToken cancel=default) {
        if (endpoint==null) throw new InvalidOperationException("Pairing needs the developer portal endpoint");
        // onCode runs where PairAsync was called (a UI thread, for example), not on a network thread.
        var context=SynchronizationContext.Current;
        string request;
        lock(gate) { Open(); request=OptionalText(FisicaEntitlementText.ActivationRequest) ?? throw new InvalidOperationException(Explain(FisicaModeReason.NoTarget)); }
        using var device=JsonDocument.Parse(await Post(Pairing,null,Encoding.UTF8.GetBytes(request)).ConfigureAwait(false));
        var root=device.RootElement;
        var code=new PairingCode(root.GetProperty("user_code").GetString()!,root.GetProperty("verification_uri").GetString()!,
            root.GetProperty("verification_uri_complete").GetString()!,root.GetProperty("expires_in").GetInt32());
        if (context!=null) context.Post(_ => onCode(code),null); else onCode(code);
        var body=JsonSerializer.SerializeToUtf8Bytes(new Dictionary<string,string> { ["device_code"]=root.GetProperty("device_code").GetString()! });
        double interval=root.TryGetProperty("interval",out var given) ? Math.Max(0,given.GetDouble()) : 5;
        // A key approved just before the code expires can still be received for a short while.
        var deadline=DateTimeOffset.UtcNow.AddSeconds(code.ExpiresIn+30);
        while (true) {
            var wait=TimeSpan.FromSeconds(Math.Max(0,Math.Min(interval,(deadline-DateTimeOffset.UtcNow).TotalSeconds)));
            try { await Task.Delay(wait,cancel).ConfigureAwait(false); } catch (OperationCanceledException) { return PairingResult.Cancelled; }
            (int Status,byte[] Data) answer;
            try { answer=await Send(PairingToken,null,body).ConfigureAwait(false); }
            catch (Exception failure) when (failure is HttpRequestException or TaskCanceledException) {
                LastSyncError="network: "+failure.Message; answer=(0,[]);  // keep polling until the code expires
            }
            if (answer.Status==200) {
                using var reply=JsonDocument.Parse(answer.Data);
                var outcome=ImportToken(reply.RootElement.GetProperty("token").GetString()!);
                if (outcome!=FisicaEntitlementOutcome.Accepted) throw new InvalidDataException($"The paired key was not accepted here ({outcome})");
                return PairingResult.Approved;
            }
            if (answer.Status==400) {
                string? error=null;
                try { using var reply=JsonDocument.Parse(answer.Data); error=reply.RootElement.GetProperty("error").GetString(); }
                catch (Exception failure) when (failure is JsonException or KeyNotFoundException or InvalidOperationException) { }
                if (error=="access_denied") return PairingResult.Denied;
                if (error=="expired_token") return PairingResult.Expired;
                if (error=="slow_down") interval+=5;
            }
            if (DateTimeOffset.UtcNow>=deadline) return PairingResult.Expired;
        }
    }
    // What the server should do now, as the core decides it: activate, renew, or nothing.
    (string? Path,string Token,byte[] Body) Due(bool force) {
        lock(gate) {
            if (handle.IsClosed) return (null,"",[]);
            var token=Text(FisicaEntitlementText.Token);
            if (token.Length==0) return (null,"",[]);
            FisicaCore.Check(EntitlementNative.fisica_core_entitlement_next_exchange(handle,clock(),out uint next));
            if ((FisicaExchange)next==FisicaExchange.Activate && OptionalText(FisicaEntitlementText.ActivationRequest) is {} request)
                return (Activate,token,Encoding.UTF8.GetBytes(request));  // the core's request for this target, verbatim
            return ((FisicaExchange)next==FisicaExchange.Refresh || force ? Refresh : null,token,"{}"u8.ToArray());
        }
    }
    /// <summary>Activate or renew with the server now when due (force: renew now). A failure changes no license state:
    /// it is recorded in LastSyncError and in the result's Error, never thrown. When the server reports the key released,
    /// the stored key is deleted. An answer for a key released or replaced while it was on its way changes nothing.</summary>
    public async Task<EntitlementSync> SyncAsync(bool force=false) {
        if (!online || endpoint==null) return new(false,false,null);
        await exchange.WaitAsync().ConfigureAwait(false);
        bool activated=false, refreshed=false; string? error=null;
        try {
            var (path,token,body)=Due(force);
            if (path==null) return new(false,false,null);
            var (status,answer)=await Send(path,token,body).ConfigureAwait(false);
            if (status is < 200 or > 299) {
                // A refused activation (key in use elsewhere, released, trial already used) does not change
                // within a minute: ask again in an hour. Other failures retry after a minute (Sync).
                if (status is >= 400 and < 500 and not 429) Interlocked.Exchange(ref nextSync,Environment.TickCount64+3_600_000);
                var text=Encoding.UTF8.GetString(answer);
                throw new HttpRequestException($"{status} {text[..Math.Min(200,text.Length)]}");
            }
            using var reply=JsonDocument.Parse(answer);
            var data=Encoding.UTF8.GetBytes(reply.RootElement.GetProperty("token").GetString() ?? throw new InvalidDataException("No key in the response"));
            lock(gate) {
                if (handle.IsClosed) return new(false,false,null);
                // The answer belongs to the key it was asked for. Released or replaced (Release, ImportToken, RestoreState)
                // while it was on its way, it is dropped: a released key never comes back and a newer key stays.
                if (Text(FisicaEntitlementText.Token)!=token) return new(false,false,null);
                FisicaCore.Check(Native.fisica_core_entitlement_import(handle,data,(uint)data.Length,clock(),out uint outcome));
                bool save=outcome==(uint)FisicaEntitlementOutcome.Accepted;
                if (path==Activate) activated=save; else refreshed=save;
                // The server reports this key released: the device lets go of it.
                if ((FisicaModeReason)Values()[1]==FisicaModeReason.Revoked) Forget(StateFile());
                else if (save) Save();
            }
            LastSyncError=null;
            Interlocked.Exchange(ref nextSync,Environment.TickCount64+3_600_000);
        } catch (Exception failure) {
            // A communication failure is not an expiry, a release or a revocation.
            error=LastSyncError="network: "+failure.Message;
        } finally { exchange.Release(); }
        try { Notify(); } catch (Exception) { /* Closed while finishing. */ }
        return new(activated,refreshed,error);
    }
    /// <summary>Background SyncAsync when due, at most hourly after a success unless forced. Never blocks or throws.</summary>
    public void Sync(bool force=false) {
        if (!online || endpoint==null) return;
        long now=Environment.TickCount64;
        if (!force && now<Interlocked.Read(ref nextSync)) return;
        if (Interlocked.CompareExchange(ref syncing,1,0)!=0) return;
        Interlocked.Exchange(ref nextSync,now+60_000);  // Until this attempt succeeds.
        _=Task.Run(async () => {
            try { await SyncAsync(force).ConfigureAwait(false); } catch (Exception) { /* Recorded in LastSyncError. */ } finally { Volatile.Write(ref syncing,0); }
            // A key imported during this exchange asked for its own (ImportToken makes one due at once).
            if (Environment.TickCount64>=Interlocked.Read(ref nextSync)) Sync();
        });
    }
    /// <summary>Opaque state for the host to store: the verified key and the latest time seen. Restoring verifies the key again.</summary>
    public byte[] ExportState() { lock(gate) {
        Open(); var data=new byte[262144];
        FisicaCore.Check(Native.fisica_core_entitlement_export(handle,data,(uint)data.Length,out uint length)); return data[..(int)length];
    } }
    public void RestoreState(byte[] state) { lock(gate) { Open(); FisicaCore.Check(Native.fisica_core_entitlement_restore(handle,state,(uint)state.Length)); } Notify(); }
}

/// <summary>Entitlement functions bound here; the rest are in Native (Core.cs).</summary>
internal static class EntitlementNative {
    [System.Runtime.InteropServices.DllImport("fisica_core",CallingConvention=System.Runtime.InteropServices.CallingConvention.Cdecl)]
    internal static extern int fisica_core_entitlement_next_exchange(EntitlementHandle handle,ulong now,out uint exchange);
}
