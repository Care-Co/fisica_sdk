using System.Collections.Concurrent;
using System.Diagnostics;
using System.Text;
namespace CareCo.Fisica;

/// <summary>Byte link to one Scale. The app or SDK adapter owns the OS I/O; no protocol rules here.</summary>
public interface IFisicaTransport {
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

/// <summary>Result of the shared analysis over the collected frames. Centimetres use the nominal 1 cm cell; not clinically calibrated.</summary>
public sealed record SessionReport(double Weight,double LeftWidth,double LeftLength,double RightWidth,double RightLength,IReadOnlyList<Measurement> Frames) {
    public int FrameCount => Frames.Count; public bool BatteryValid => false;
}

/// <summary>Link kind passed to the core; it selects the Scale's measurement mode command.</summary>
public enum FisicaLink { Ble=1, Usb=2 }

/// <summary>Connect, measure, stop and report over any transport. Command order, ACK waits, timeouts and recovery
/// decisions live in the shared core; this class only runs the clock, moves bytes and turns core events into
/// tasks and events. Work is serialized internally; events are raised on the thread that delivered the input.</summary>
public sealed class FisicaClient : IAsyncDisposable {
    public FisicaCore Core { get; } public IFisicaTransport Transport { get; } public FisicaEntitlement? Entitlement { get; }
    public event Action<Measurement>? Frame;
    public event Action<SessionEvent>? Event;
    /// <summary>The link dropped. Reopen the same Scale with ConnectAsync; the core resumes a measurement it still wants.</summary>
    public event Action? LinkLost;
    public event Action<Exception>? Error;
    public string? FirmwareVersion { get; private set; }
    public FisicaSessionState State { get; private set; }=FisicaSessionState.Disconnected;

    readonly FisicaSession session; readonly Stopwatch clock=Stopwatch.StartNew();
    readonly ConcurrentQueue<Action> work=new(); int pumping;
    readonly Dictionary<FisicaSessionOperation,TaskCompletionSource<SessionEvent>> waiters=new();
    readonly List<Measurement> frames=new();
    Timer? ticker; CancellationTokenSource? readyDeadline; bool linkReady, closed, wired;
    FisicaLink link;

    public FisicaClient(FisicaCore core,IFisicaTransport transport,FisicaEntitlement? entitlement=null) {
        Core=core; Transport=transport; Entitlement=entitlement; session=new FisicaSession(core);
    }

    /// <summary>Frames collected since the last ClearFrames.</summary>
    public IReadOnlyList<Measurement> Frames { get { lock(frames) return frames.ToArray(); } }
    public void ClearFrames() { lock(frames) frames.Clear(); }

    // One piece of work at a time, in arrival order, without re-entering the core.
    void Post(Action action) {
        work.Enqueue(action);
        while (!work.IsEmpty && Interlocked.CompareExchange(ref pumping,1,0)==0) {
            try { while (work.TryDequeue(out var next)) { try { next(); } catch (Exception error) { Error?.Invoke(error); } } }
            finally { Volatile.Write(ref pumping,0); }
        }
    }
    static TaskCompletionSource<SessionEvent> Waiter() => new(TaskCreationOptions.RunContinuationsAsynchronously);
    void Finish(FisicaSessionOperation operation,SessionEvent? result,Exception? error) {
        if (!waiters.Remove(operation,out var waiter)) return;
        // Release everything before the caller hears that close finished, confirmed or not.
        if (operation==FisicaSessionOperation.Close) Teardown();
        if (error!=null) waiter.TrySetException(error); else waiter.TrySetResult(result!);
    }

    /// <summary>Open the transport and probe firmware. Completes with the probe result; an unanswered probe is
    /// reported in its Status and does not prevent use.</summary>
    public Task<SessionEvent> ConnectAsync(FisicaLink link=FisicaLink.Usb,TimeSpan? timeout=null) {
        var waiter=Waiter();
        Post(() => {
            if (closed || linkReady || waiters.ContainsKey(FisicaSessionOperation.Probe)) { waiter.TrySetException(new FisicaException((int)FisicaStatus.Busy,"Already connected or connecting")); return; }
            waiters[FisicaSessionOperation.Probe]=waiter; this.link=link;
            if (!wired) {
                wired=true;
                Transport.Ready+=OnReady; Transport.Bytes+=OnBytes; Transport.Lost+=OnLost; Transport.SendFailed+=OnSendFailed; Transport.Error+=OnTransportError;
            }
            var deadline=readyDeadline=new CancellationTokenSource();
            _=Task.Delay(timeout ?? TimeSpan.FromSeconds(15),deadline.Token).ContinueWith(_ => Post(() => {
                if (linkReady || closed || deadline!=readyDeadline) return;
                Transport.Close();
                Finish(FisicaSessionOperation.Probe,null,new FisicaException(1001,"Transport was not ready in time"));
            }),TaskContinuationOptions.OnlyOnRanToCompletion);
            Entitlement?.MarkUsed();
            Transport.Open();
        });
        return waiter.Task;
    }
    void OnReady() => Post(() => {
        if (closed || linkReady) return;
        linkReady=true; readyDeadline?.Cancel();
        ticker ??= new Timer(_ => Post(() => Step(() => session.Tick(clock.ElapsedMilliseconds))),null,20,20);
        Step(() => session.Action((uint)FisicaSessionAction.Connected,(long)link,clock.ElapsedMilliseconds));
    });
    void OnBytes(byte[] bytes) => Post(() => {
        if (!linkReady) return;
        // The core accepts at most 65536 bytes per call.
        for (int offset=0;offset<bytes.Length;offset+=65536) { var part=bytes[offset..Math.Min(offset+65536,bytes.Length)]; Step(() => session.Feed(part,clock.ElapsedMilliseconds)); }
    });
    void OnLost() => Post(() => LinkEnded(false));
    void OnSendFailed(Exception error) => Post(() => { Error?.Invoke(error); LinkEnded(true); });
    void OnTransportError(Exception error) => Post(() => Error?.Invoke(error));
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

    /// <summary>Run one core call, then deliver everything it produced.</summary>
    void Step(Action call) {
        if (closed) return;
        try { call(); foreach (var next in session.Drain()) Deliver(next); } catch (Exception error) { Error?.Invoke(error); }
    }
    void Deliver(SessionEvent e) {
        State=(FisicaSessionState)e.State;
        switch ((FisicaSessionEvent)e.Kind) {
        case FisicaSessionEvent.Transmit: Transport.Send(e.Payload); break;
        case FisicaSessionEvent.Measurement:
            var frame=Core.Decode(e.Payload); lock(frames) frames.Add(frame); Frame?.Invoke(frame); break;
        case FisicaSessionEvent.Result:
            var operation=(FisicaSessionOperation)e.Operation; bool probe=operation==FisicaSessionOperation.Probe;
            if (probe && e.Status==0) { var version=Encoding.UTF8.GetString(Core.ResponsePayload(e.Payload)).Trim('\0',' ','\r','\n'); FirmwareVersion=version.Length==0 ? null : version; }
            // A failed firmware probe is informational; the core lets use continue.
            if (e.Status==0 || probe) Finish(operation,e,null); else Finish(operation,null,new FisicaCommandException(e));
            break;
        case FisicaSessionEvent.Reconnect: LinkLost?.Invoke(); break;
        }
        Event?.Invoke(e);
    }

    Task<SessionEvent> Request(FisicaSessionAction action,long value,FisicaSessionOperation waitFor) {
        var waiter=Waiter();
        Post(() => {
            if (closed || waiters.ContainsKey(waitFor)) { waiter.TrySetException(new FisicaException((int)FisicaStatus.Busy,"Another operation is pending")); return; }
            waiters[waitFor]=waiter;
            try { session.Action((uint)action,value,clock.ElapsedMilliseconds); foreach (var next in session.Drain()) Deliver(next); }
            catch (Exception error) { Finish(waitFor,null,error); }
        });
        return waiter.Task;
    }
    /// <summary>Start measuring; null duration measures until StopAsync. Completes when the Scale confirmed the start.</summary>
    public Task<SessionEvent> StartAsync(TimeSpan? duration=null) {
        Entitlement?.MarkUsed();
        return Request(FisicaSessionAction.Start,duration is {} d ? (long)d.TotalMilliseconds : -1,FisicaSessionOperation.Begin);
    }
    public Task<SessionEvent> StopAsync() => Request(FisicaSessionAction.Stop,0,FisicaSessionOperation.End);

    /// <summary>Stop if needed, release the Scale's measurement mode, then close the transport. Resources are
    /// released even when the Scale does not confirm; the task then carries that error.</summary>
    public Task CloseAsync() {
        var done=new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        Post(() => {
            if (closed) { done.TrySetResult(); return; }
            if (!linkReady) { Teardown(); done.TrySetResult(); return; }
            Request(FisicaSessionAction.Disconnect,0,FisicaSessionOperation.Close).ContinueWith(task => {
                if (task.Exception?.InnerException is {} error) done.TrySetException(error); else done.TrySetResult();
            });
        });
        return done.Task;
    }
    void Teardown() {
        if (closed) return;
        closed=true; linkReady=false; ticker?.Dispose(); readyDeadline?.Cancel();
        if (wired) { Transport.Ready-=OnReady; Transport.Bytes-=OnBytes; Transport.Lost-=OnLost; Transport.SendFailed-=OnSendFailed; Transport.Error-=OnTransportError; }
        Transport.Close(); session.Dispose();
        foreach (var waiter in waiters.Values) waiter.TrySetException(new FisicaException((int)FisicaStatus.Cancelled,"Client closed"));
        waiters.Clear();
    }
    public async ValueTask DisposeAsync() { try { await CloseAsync(); } catch (Exception) { /* Resources are already released. */ } }

    /// <summary>Analyze the frames collected so far; null without a stable measurement.</summary>
    public SessionReport? Report() {
        var snapshot=Frames; if (snapshot.Count==0) return null;
        Entitlement?.MarkUsed();
        var result=Core.AnalyzePackets(snapshot.Select(f => f.Packet).ToArray());
        return result==null ? null : new(result[0],result[1],result[2],result[3],result[4],snapshot);
    }
}
