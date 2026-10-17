using System.Collections.Concurrent;
using System.Diagnostics;
using System.Text;
namespace CareCo.Fisica;

/// <summary>Byte link to one Scale. The app or SDK adapter owns the OS I/O; no protocol rules here.</summary>
public interface IFisicaTransport {
    /// <summary>The Scale link this transport carries, or null when it can carry either; then
    /// FisicaClient.ConnectAsync must be given the link. A different link is rejected before anything is sent.</summary>
    FisicaLink? Link => null;
    /// <summary>Reads and writes both work.</summary>
    event Action? Ready;
    event Action<byte[]>? Bytes;
    /// <summary>The link ended or could not be established.</summary>
    event Action? Lost;
    /// <summary>A write did not reach the Scale; the command outcome is unknown.</summary>
    event Action<Exception>? SendFailed;
    event Action<Exception>? Error;
    void Open();
    void Send(byte[] bytes);
    void Close();
}

/// <summary>Scripted link for development and tests without hardware: request bytes -> response chunks.
/// It contains no device behavior; unknown requests are recorded and get no reply.</summary>
public sealed class MockTransport : IFisicaTransport {
    public event Action? Ready; public event Action<byte[]>? Bytes; public event Action? Lost;
    public event Action<Exception>? SendFailed; public event Action<Exception>? Error;
    /// <summary>Set to model a transport that carries only one link kind.</summary>
    public FisicaLink? Link { get; set; }
    readonly Dictionary<string,byte[][]> exchanges=new();
    public List<byte[]> Sent { get; }=new();
    public bool IsOpen { get; private set; }
    /// <summary>Set false to model a link that never becomes ready.</summary>
    public bool BecomesReady { get; set; }=true;
    public void Reply(byte[] request,params byte[][] chunks) => exchanges[Convert.ToHexString(request)]=chunks;
    public void Open() { IsOpen=true; if (BecomesReady) Ready?.Invoke(); }
    public void Send(byte[] bytes) {
        if (!IsOpen) { SendFailed?.Invoke(new FisicaException(1001,"Mock transport is closed")); return; }
        lock(Sent) Sent.Add((byte[])bytes.Clone());
        if (exchanges.TryGetValue(Convert.ToHexString(bytes),out var chunks)) foreach(var chunk in chunks) Bytes?.Invoke(chunk);
    }
    public void Feed(byte[] bytes) => Bytes?.Invoke(bytes);
    public void Drop() { IsOpen=false; Lost?.Invoke(); }
    public void Close() => IsOpen=false;
    internal void Report(Exception error) => Error?.Invoke(error);
}

/// <summary>A command the Scale rejected or did not confirm: SDK status in Code, the Scale's 16-bit code in DeviceCode.</summary>
public sealed class FisicaCommandException(SessionEvent sessionEvent)
    : Exception($"Command rejected or unconfirmed; device code {sessionEvent.DeviceError}") {
    public SessionEvent Event { get; }=sessionEvent;
    public FisicaStatus Code => (FisicaStatus)Event.Status;
    public uint DeviceCode => Event.DeviceError;
}

/// <summary>Result of the shared analysis. Centimetres use the nominal 1 cm cell; not clinically calibrated.
/// Weight and foot size come in every mode from every packet the core received; in basic mode Frames holds about
/// one frame a second.</summary>
public sealed record SessionReport(double Weight,double LeftWidth,double LeftLength,double RightWidth,double RightLength,IReadOnlyList<Measurement> Frames) {
    public int FrameCount => Frames.Count;
    /// <summary>The client's last ReadBatteryAsync reading in percent, or null. Packets carry no battery field.</summary>
    public int? Battery { get; init; }
    public bool BatteryValid => Battery is not null;
}

/// <summary>Connect, measure, stop and report over any transport. Command order, ACK waits, timeouts, recovery
/// decisions and license limits live in the shared core; this class only runs the clock, moves bytes and turns core
/// events into tasks and events.
/// Work runs one piece at a time on a thread-pool thread, never on the thread that calls a method or delivers
/// transport input, so a blocking transport write never holds up a UI thread. Events are raised on that worker
/// thread, or posted to the SynchronizationContext passed as callbacks (for example the UI thread's). A handler that
/// throws is reported through Error; the client carries on. Tasks complete asynchronously; await resumes on the
/// awaiting context as usual.
/// Without an Entitlement holding an SDK key activated for this app or PC the client runs in basic mode: connection,
/// measurement, settings, weight and foot size work, frames arrive at most once a second without Raw and Packet.
/// The client's timer does not keep it alive: a client dropped without CloseAsync can still be collected.</summary>
public sealed class FisicaClient : IAsyncDisposable {
    public FisicaCore Core { get; } public IFisicaTransport Transport { get; }
    /// <summary>The license applied to the session before every start, every 30 seconds and whenever it changes.</summary>
    public FisicaEntitlement? Entitlement { get; }
    public event Action<Measurement>? Frame;
    public event Action<SessionEvent>? Event;
    /// <summary>The link dropped. Reopen the same Scale with ConnectAsync; the core resumes a measurement it still wants.</summary>
    public event Action? LinkLost;
    /// <summary>Transport and core errors, and exceptions thrown by Frame, Event or LinkLost handlers.</summary>
    public event Action<Exception>? Error;
    public string? FirmwareVersion { get; private set; }
    /// <summary>Battery level in percent from the last ReadBatteryAsync, or null. Measurement packets carry no battery field.</summary>
    public int? Battery { get; private set; }
    public FisicaSessionState State { get; private set; }=FisicaSessionState.Disconnected;

    readonly FisicaSession session; readonly Stopwatch clock=Stopwatch.StartNew();
    readonly SynchronizationContext? callbacks;
    readonly ConcurrentQueue<Action> work=new(); int pumping;
    readonly Dictionary<FisicaSessionOperation,TaskCompletionSource<SessionEvent>> waiters=new();
    readonly Queue<Measurement> frames=new(); int dropped, maxFrames=(int)FisicaLimit.AnalysisFrames;
    Timer? ticker; CancellationTokenSource? readyDeadline; bool linkReady, closed, wired;
    FisicaLink link;
    const long LicenseInterval=30_000;
    long licenseApplied;
    // The core's analysis of the last measurement, kept when the session is released so Report() works after closing.
    volatile double[]? closedAnalysis;

    /// <param name="core">The shared core.</param>
    /// <param name="transport">The byte link to the Scale.</param>
    /// <param name="entitlement">The license; null is basic mode.</param>
    /// <param name="callbacks">Where Frame, Event, LinkLost and Error are raised, for example
    /// SynchronizationContext.Current on the UI thread; null raises them on the client's worker thread.</param>
    public FisicaClient(FisicaCore core,IFisicaTransport transport,FisicaEntitlement? entitlement=null,SynchronizationContext? callbacks=null) {
        Core=core; Transport=transport; Entitlement=entitlement; this.callbacks=callbacks; session=new FisicaSession(core);
        if (entitlement!=null) entitlement.Changed+=OnLicenseChanged;
    }

    /// <summary>Frames of the current measurement. StartAsync clears them; a reconnect keeps them.
    /// Only the latest MaxFrames are kept.</summary>
    public IReadOnlyList<Measurement> Frames { get { lock(frames) return frames.ToArray(); } }
    /// <summary>Oldest frames dropped beyond MaxFrames since the last StartAsync or ClearFrames.</summary>
    public int DroppedFrames { get { lock(frames) return dropped; } }
    /// <summary>How many recent frames to keep, 1..FisicaLimit.AnalysisFrames (the most one analysis accepts).
    /// Also bounds memory in long sessions.</summary>
    public int MaxFrames {
        get { lock(frames) return maxFrames; }
        set {
            ArgumentOutOfRangeException.ThrowIfLessThan(value,1); ArgumentOutOfRangeException.ThrowIfGreaterThan(value,(int)FisicaLimit.AnalysisFrames);
            lock(frames) { maxFrames=value; Trim(); }
        }
    }
    public void ClearFrames() { lock(frames) { frames.Clear(); dropped=0; } }
    void Trim() { while (frames.Count>maxFrames) { frames.Dequeue(); dropped++; } }

    // One piece of work at a time, in arrival order, on a thread-pool thread, without re-entering the core.
    void Post(Action action) {
        work.Enqueue(action);
        if (Interlocked.CompareExchange(ref pumping,1,0)==0) ThreadPool.UnsafeQueueUserWorkItem(static client => client.Pump(),this,false);
    }
    void Pump() {
        do {
            while (work.TryDequeue(out var next)) { try { next(); } catch (Exception error) { Report(error); } }
            Volatile.Write(ref pumping,0);
        } while (!work.IsEmpty && Interlocked.CompareExchange(ref pumping,1,0)==0);
    }
    // User handlers: one that throws is reported through Error and never stops the work at hand.
    void Raise<T>(Action<T>? handler,T value) {
        if (handler==null) return;
        if (callbacks==null) Invoke(handler,value); else callbacks.Post(_ => Invoke(handler,value),null);
    }
    void Invoke<T>(Action<T> handler,T value) { try { handler(value); } catch (Exception error) { Report(error); } }
    void Report(Exception error) {
        var handler=Error; if (handler==null) return;
        if (callbacks==null) Quietly(handler,error); else callbacks.Post(_ => Quietly(handler,error),null);
    }
    // An Error handler that throws has nowhere left to report to.
    static void Quietly(Action<Exception> handler,Exception error) { try { handler(error); } catch (Exception) { } }

    static TaskCompletionSource<SessionEvent> Waiter() => new(TaskCreationOptions.RunContinuationsAsynchronously);
    void Finish(FisicaSessionOperation operation,SessionEvent? result,Exception? error) {
        if (!waiters.Remove(operation,out var waiter)) return;
        // Release everything before the caller hears that close finished, confirmed or not.
        if (operation==FisicaSessionOperation.Close) Teardown();
        if (error!=null) waiter.TrySetException(error); else waiter.TrySetResult(result!);
    }

    /// <summary>Open the transport and probe firmware. Completes with the probe result; an unanswered probe is
    /// reported in its Status and does not prevent use. The link defaults to the transport's own Link; a transport
    /// that does not declare one (Link null) must be given it. A missing link, or one other than the transport's,
    /// fails with InvalidArgument before anything is sent.</summary>
    public Task<SessionEvent> ConnectAsync(FisicaLink? link=null,TimeSpan? timeout=null) {
        var waiter=Waiter();
        Post(() => {
            if (closed || linkReady || waiters.ContainsKey(FisicaSessionOperation.Probe)) { waiter.TrySetException(new FisicaException((int)FisicaStatus.Busy,"Already connected or connecting")); return; }
            var carried=Transport.Link;
            string? problem=(link ?? carried) is not {} chosen ? "This transport does not declare its link: pass FisicaLink.Ble or FisicaLink.Usb"
                : !Enum.IsDefined(chosen) ? "Use FisicaLink.Ble or FisicaLink.Usb"
                : carried is {} known && known!=chosen ? $"This transport carries {known}, not {chosen}" : null;
            if (problem!=null) { waiter.TrySetException(new FisicaException((int)FisicaStatus.InvalidArgument,problem)); return; }
            waiters[FisicaSessionOperation.Probe]=waiter; this.link=(link ?? carried)!.Value;
            if (!wired) {
                wired=true;
                Transport.Ready+=OnReady; Transport.Bytes+=OnBytes; Transport.Lost+=OnLost; Transport.SendFailed+=OnSendFailed; Transport.Error+=OnTransportError;
            }
            var deadline=readyDeadline=new CancellationTokenSource();
            _=Task.Delay(timeout ?? TimeSpan.FromSeconds(15),deadline.Token).ContinueWith(_ => Post(() => {
                if (linkReady || closed || deadline!=readyDeadline) return;
                CloseTransport();
                Finish(FisicaSessionOperation.Probe,null,new FisicaException(1001,"Transport was not ready in time"));
            }),CancellationToken.None,TaskContinuationOptions.OnlyOnRanToCompletion,TaskScheduler.Default);
            Entitlement?.CheckStatus();
            try { Transport.Open(); }
            catch (Exception error) { deadline.Cancel(); Finish(FisicaSessionOperation.Probe,null,error); }
        });
        return waiter.Task;
    }
    void OnReady() => Post(() => {
        if (closed || linkReady) return;
        linkReady=true; readyDeadline?.Cancel();
        ticker ??= Tick(this);
        Step(() => session.Action((uint)FisicaSessionAction.Connected,(long)link,clock.ElapsedMilliseconds));
    });
    void OnBytes(byte[] bytes) => Post(() => {
        if (!linkReady) return;
        const int limit=(int)FisicaLimit.FeedBytes;
        for (int offset=0;offset<bytes.Length;offset+=limit) { var part=bytes[offset..Math.Min(offset+limit,bytes.Length)]; Step(() => session.Feed(part,clock.ElapsedMilliseconds)); }
    });
    void OnLost() => Post(() => LinkEnded(false));
    void OnSendFailed(Exception error) => Post(() => { Report(error); LinkEnded(true); });
    void OnTransportError(Exception error) => Post(() => Report(error));
    void LinkEnded(bool sendFailure) {
        if (closed) return;
        if (!linkReady) {
            // Never became ready: nothing to recover, report the failed connection.
            readyDeadline?.Cancel();
            Finish(FisicaSessionOperation.Probe,null,new FisicaException(1001,"Transport could not connect")); return;
        }
        linkReady=false;
        Step(() => session.Action((uint)FisicaSessionAction.Lost,sendFailure ? 1 : 0,clock.ElapsedMilliseconds));
    }

    /// <summary>Ticks every 20 ms. The timer holds the client weakly: a client dropped without CloseAsync is
    /// collected (its session's native memory with it), and the timer then disposes itself.</summary>
    static Timer Tick(FisicaClient client) {
        var target=new WeakReference<FisicaClient>(client); Timer? timer=null;
        timer=new Timer(_ => { if (target.TryGetTarget(out var alive)) alive.Post(alive.Pulse); else timer?.Dispose(); },null,20,20);
        return timer;
    }
    void Pulse() {
        Step(() => session.Tick(clock.ElapsedMilliseconds));
        // Picks up renewals and activation, and notices the end of full mode.
        if (!closed && clock.ElapsedMilliseconds-licenseApplied>=LicenseInterval) { Entitlement?.CheckStatus(); ApplyLicense(); }
    }
    void OnLicenseChanged(EntitlementStatus status) => Post(ApplyLicense);
    /// <summary>Copy the current license into the session; the core then limits what it hands out.</summary>
    void ApplyLicense() {
        if (closed) return;
        long now=clock.ElapsedMilliseconds; licenseApplied=now;
        try { session.License(Entitlement,now); }
        catch (Exception error) when (error is ObjectDisposedException or FisicaException) {
            // A license that cannot be read is basic mode; measuring goes on.
            session.License(null,now); if (error is FisicaException) Report(error);
        }
    }

    /// <summary>Run one core call, then deliver everything it produced.</summary>
    void Step(Action call) {
        if (closed) return;
        try { call(); } catch (Exception error) { Report(error); }
        DeliverQueued();
    }
    // Every queued event is delivered, even when a handler or the transport failed on an earlier one, so results
    // (and the tasks waiting for them) are never lost.
    void DeliverQueued() {
        if (closed) return;
        List<SessionEvent> events;
        try { events=session.Drain(); } catch (Exception error) { Report(error); return; }
        foreach (var next in events) { try { Deliver(next); } catch (Exception error) { Report(error); } }
    }
    void Deliver(SessionEvent e) {
        State=(FisicaSessionState)e.State;
        switch ((FisicaSessionEvent)e.Kind) {
        case FisicaSessionEvent.Transmit:
            // A transport that throws instead of raising SendFailed is handled the same way, after the events at hand.
            try { Transport.Send(e.Payload); } catch (Exception error) { OnSendFailed(error); }
            break;
        case FisicaSessionEvent.Measurement:
            var frame=Measurement.FromPayload(e.Payload); lock(frames) { frames.Enqueue(frame); Trim(); } Raise(Frame,frame); break;
        case FisicaSessionEvent.Result:
            var operation=(FisicaSessionOperation)e.Operation; bool probe=operation==FisicaSessionOperation.Probe;
            if (probe && e.Status==0) {
                try { var version=Encoding.UTF8.GetString(Core.ResponsePayload(e.Payload)).Trim('\0',' ','\r','\n'); FirmwareVersion=version.Length==0 ? null : version; }
                catch (FisicaException error) { Report(error); }
            }
            // A failed firmware probe is informational; the core lets use continue.
            if (e.Status==0 || probe) Finish(operation,e,null); else Finish(operation,null,new FisicaCommandException(e));
            break;
        case FisicaSessionEvent.Reconnect: if (LinkLost is {} lost) Raise(handler => handler(),lost); break;
        }
        Raise(Event,e);
    }

    // Registers the waiter, runs the core call and delivers what it produced; a call the core refuses fails the task.
    Task<SessionEvent> Request(FisicaSessionOperation waitFor,Action call) {
        var waiter=Waiter();
        Post(() => {
            if (closed || waiters.ContainsKey(waitFor)) { waiter.TrySetException(new FisicaException((int)FisicaStatus.Busy,"Another operation is pending")); return; }
            waiters[waitFor]=waiter;
            try { call(); } catch (Exception error) { Finish(waitFor,null,error); }
            DeliverQueued();
        });
        return waiter.Task;
    }
    /// <summary>Start measuring; null duration measures until StopAsync. Completes when the Scale confirmed the start.
    /// The current license is applied first. Frames of the previous measurement are cleared once the core accepts the start.</summary>
    public Task<SessionEvent> StartAsync(TimeSpan? duration=null) {
        Entitlement?.CheckStatus();
        long value=duration is {} d ? (long)d.TotalMilliseconds : -1;
        return Request(FisicaSessionOperation.Begin,() => {
            ApplyLicense(); session.Action((uint)FisicaSessionAction.Start,value,clock.ElapsedMilliseconds); ClearFrames();
        });
    }
    public Task<SessionEvent> StopAsync() =>
        Request(FisicaSessionOperation.End,() => session.Action((uint)FisicaSessionAction.Stop,0,clock.ElapsedMilliseconds));

    /// <summary>Ask the Scale for its battery level (GET_BATTERY_LEVEL), idle or during a measurement, without
    /// interrupting it. A refused or unanswered query fails only this task; the session carries on.</summary>
    public async Task<int> ReadBatteryAsync() {
        var result=await Request(FisicaSessionOperation.Request,() => session.Command("GET_BATTERY_LEVEL",clock.ElapsedMilliseconds));
        var data=Core.ResponsePayload(result.Payload);
        var percent=data.Length==2 ? (data[0]<<8)|data[1] : -1;
        if (percent is < 0 or > 100) throw new FisicaCommandException(result);
        Battery=percent; return percent;
    }

    /// <summary>Stop if needed, release the Scale's measurement mode, then close the transport. Resources are
    /// released even when the Scale does not confirm; the task then carries that error.</summary>
    public Task CloseAsync() {
        var done=new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        Post(() => {
            if (closed) { done.TrySetResult(); return; }
            if (!linkReady) { Teardown(); done.TrySetResult(); return; }
            Request(FisicaSessionOperation.Close,() => session.Action((uint)FisicaSessionAction.Disconnect,0,clock.ElapsedMilliseconds)).ContinueWith(task => {
                if (task.Exception?.InnerException is {} error) done.TrySetException(error); else done.TrySetResult();
            },TaskScheduler.Default);
        });
        return done.Task;
    }
    void CloseTransport() { try { Transport.Close(); } catch (Exception error) { Report(error); } }
    void Teardown() {
        if (closed) return;
        closed=true; linkReady=false; ticker?.Dispose(); readyDeadline?.Cancel();
        if (wired) { Transport.Ready-=OnReady; Transport.Bytes-=OnBytes; Transport.Lost-=OnLost; Transport.SendFailed-=OnSendFailed; Transport.Error-=OnTransportError; }
        if (Entitlement!=null) Entitlement.Changed-=OnLicenseChanged;
        // Report() still works after closing: keep the core's analysis of the last measurement.
        try { closedAnalysis=session.Analyze(); } catch (FisicaException) { }
        CloseTransport(); session.Dispose();
        foreach (var waiter in waiters.Values) waiter.TrySetException(new FisicaException((int)FisicaStatus.Cancelled,"Client closed"));
        waiters.Clear();
    }
    public async ValueTask DisposeAsync() { try { await CloseAsync(); } catch (Exception) { /* Resources are already released. */ } }

    /// <summary>Weight and foot size of the current measurement, analyzed in the shared core from every packet it
    /// received since the start in every mode (Frames may hold only some of them); null without a stable
    /// measurement. After CloseAsync it returns the last measurement's analysis.</summary>
    public SessionReport? Report() {
        Entitlement?.CheckStatus();
        double[]? result;
        try { result=session.Analyze(); } catch (ObjectDisposedException) { result=closedAnalysis; }
        return result==null ? null : new(result[0],result[1],result[2],result[3],result[4],Frames) { Battery=Battery };
    }
}
