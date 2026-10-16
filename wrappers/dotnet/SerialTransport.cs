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
    readonly object gate=new(); SerialPort? port; Thread? reader; volatile bool open;
    public string PortName { get; }=portName;
    public static string[] PortNames() => SerialPort.GetPortNames();

    internal SerialPort CreatePort() =>
        new(PortName,baudRate,Parity.None,8,StopBits.One) { Handshake=Handshake.None, DtrEnable=true, RtsEnable=true, ReadTimeout=200, WriteTimeout=1000 };
    public void Open() {
        lock(gate) {
            if (open) return;
            try {
                port=CreatePort(); port.Open(); open=true;
            } catch (Exception error) when (error is IOException or UnauthorizedAccessException or ArgumentException or InvalidOperationException) {
                port?.Dispose(); port=null;
                Error?.Invoke(new FisicaException(1001,$"Cannot open serial port {PortName}: {error.Message}")); Lost?.Invoke(); return;
            }
            reader=new Thread(Read) { IsBackground=true, Name="fisica-serial" }; reader.Start(port);
        }
        Ready?.Invoke();
    }
    void Read(object? state) {
        var active=(SerialPort)state!; var buffer=new byte[4096];
        while (open && ReferenceEquals(active,port)) {
            try {
                int count=active.Read(buffer,0,buffer.Length);
                if (count>0) Bytes?.Invoke(buffer[..count]);
            } catch (TimeoutException) { /* Idle link. */ }
            catch (Exception error) when (error is IOException or InvalidOperationException or UnauthorizedAccessException or OperationCanceledException) {
                // Cable removed or port closed underneath us: report the loss once, unless Close() asked for it.
                if (open && ReferenceEquals(active,port)) { open=false; Error?.Invoke(new FisicaException(1001,$"Serial read failed: {error.Message}")); Lost?.Invoke(); }
                return;
            }
        }
    }
    public void Send(byte[] bytes) {
        try {
            SerialPort? active; lock(gate) active=port;
            if (!open || active==null) throw new InvalidOperationException("Serial port is not open");
            active.Write(bytes,0,bytes.Length);
        } catch (Exception error) when (error is IOException or InvalidOperationException or TimeoutException or UnauthorizedAccessException) {
            SendFailed?.Invoke(new FisicaException(1001,$"Serial write failed: {error.Message}"));
        }
    }
    public void Close() {
        SerialPort? active;
        lock(gate) { open=false; active=port; port=null; }
        try { active?.Close(); } catch (IOException) { /* Device already gone. */ }
        active?.Dispose();
    }
}
