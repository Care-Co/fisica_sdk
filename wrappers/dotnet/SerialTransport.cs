using System.IO.Ports;
namespace CareCo.Fisica;

/// <summary>USB serial link to a Scale (CP210x virtual COM port, 115200 8N1 as in the Python SDK).
/// DTR and RTS are asserted: the Scale's ESP32 is wired for auto-reset, and with DTR off it reboots
/// (DTR and RTS both off) or is held in reset (only RTS on).
/// OS I/O only: bytes in, bytes out. Choose the port in the app, for example from PortNames().</summary>
public sealed class SerialTransport(string portName,int baudRate=115200) : IFisicaTransport {
    public event Action? Ready; public event Action<byte[]>? Bytes; public event Action? Lost;
    public event Action<Exception>? SendFailed; public event Action<Exception>? Error;
    public FisicaLink? Link => FisicaLink.Usb;
    readonly object gate=new(); ISerialPort? port; volatile bool open;
    public string PortName { get; }=portName;
    public static string[] PortNames() => SerialPort.GetPortNames();
    /// <summary>The port to open; the system serial port unless a test stands in for it.</summary>
    internal Func<ISerialPort>? Ports { get; init; }

    internal SerialPort CreatePort() =>
        new(PortName,baudRate,Parity.None,8,StopBits.One) { Handshake=Handshake.None, DtrEnable=true, RtsEnable=true, ReadTimeout=200, WriteTimeout=1000 };
    /// <summary>Open the port and raise Ready. Already open (a reconnect after the session gave up on the link,
    /// for example after a failed write), the link still works: Ready is raised again on the same port.</summary>
    public void Open() {
        lock(gate) {
            if (!open) {
                // Never reuse a port left from an earlier link: the OS may refuse to open the same COM port twice.
                if (port is {} stale) { port=null; Shut(stale); }
                ISerialPort? created=null;
                try {
                    created=Ports?.Invoke() ?? new SystemPort(CreatePort()); created.Open();
                } catch (Exception error) when (error is IOException or UnauthorizedAccessException or ArgumentException or InvalidOperationException) {
                    if (created!=null) Shut(created);
                    Error?.Invoke(new FisicaException(1001,$"Cannot open serial port {PortName}: {error.Message}")); Lost?.Invoke(); return;
                }
                port=created; open=true;
                new Thread(Read) { IsBackground=true, Name="fisica-serial" }.Start(created);
            }
        }
        Ready?.Invoke();
    }
    void Read(object? state) {
        var active=(ISerialPort)state!; var buffer=new byte[4096];
        while (open && ReferenceEquals(active,port)) {
            try {
                int count=active.Read(buffer,0,buffer.Length);
                if (count>0) Bytes?.Invoke(buffer[..count]);
            } catch (TimeoutException) { /* Idle link. */ }
            catch (Exception error) when (error is IOException or InvalidOperationException or UnauthorizedAccessException or OperationCanceledException) {
                // Cable removed or port closed underneath us: release the port and report the loss once, unless Close() asked for it.
                bool failed;
                lock(gate) { failed=open && ReferenceEquals(active,port); if (failed) { open=false; port=null; } }
                if (failed) { Shut(active); Error?.Invoke(new FisicaException(1001,$"Serial read failed: {error.Message}")); Lost?.Invoke(); }
                return;
            }
        }
    }
    public void Send(byte[] bytes) {
        try {
            ISerialPort? active; lock(gate) active=port;
            if (!open || active==null) throw new InvalidOperationException("Serial port is not open");
            active.Write(bytes,0,bytes.Length);
        } catch (Exception error) when (error is IOException or InvalidOperationException or TimeoutException or UnauthorizedAccessException) {
            SendFailed?.Invoke(new FisicaException(1001,$"Serial write failed: {error.Message}"));
        }
    }
    public void Close() {
        ISerialPort? active;
        lock(gate) { open=false; active=port; port=null; }
        if (active!=null) Shut(active);
    }
    static void Shut(ISerialPort active) {
        try { active.Close(); } catch (Exception error) when (error is IOException or UnauthorizedAccessException or InvalidOperationException) { /* Device already gone. */ }
        try { active.Dispose(); } catch (Exception error) when (error is IOException or UnauthorizedAccessException or InvalidOperationException) { }
    }
}

/// <summary>What SerialTransport needs of a port: the system serial port, or a stand-in in tests.</summary>
internal interface ISerialPort : IDisposable {
    void Open();
    int Read(byte[] buffer,int offset,int count);
    void Write(byte[] buffer,int offset,int count);
    void Close();
}

sealed class SystemPort(SerialPort port) : ISerialPort {
    public void Open() => port.Open();
    public int Read(byte[] buffer,int offset,int count) => port.Read(buffer,offset,count);
    public void Write(byte[] buffer,int offset,int count) => port.Write(buffer,offset,count);
    public void Close() => port.Close();
    public void Dispose() => port.Dispose();
}
