#if WINDOWS
using System.Runtime.InteropServices.WindowsRuntime;
using Windows.Devices.Bluetooth;
using Windows.Devices.Bluetooth.Advertisement;
using Windows.Devices.Bluetooth.GenericAttributeProfile;
using Windows.Devices.Radios;
namespace CareCo.Fisica;

/// <summary>Windows BLE link to a Scale (WinRT, Windows 10 2004 or later). OS I/O only: bytes in, bytes out.
/// Available when the app targets net8.0-windows10.0.19041.0 or later. Every failure is reported through
/// Error, Lost or SendFailed; nothing is thrown into the caller. Events arrive on thread-pool threads.</summary>
public sealed class BleTransport : IFisicaTransport {
    public event Action? Ready; public event Action<byte[]>? Bytes; public event Action? Lost;
    public event Action<Exception>? SendFailed; public event Action<Exception>? Error;
    /// <summary>An advertising device was seen: Bluetooth address, advertised name, RSSI in dBm.</summary>
    public event Action<ulong,string,short>? Device;
    /// <summary>Time allowed from Open until notifications are enabled.</summary>
    public TimeSpan ReadyTimeout { get; set; }=TimeSpan.FromSeconds(15);
    /// <summary>ATT MTU agreed with the Scale; 23 until a connection reports it.</summary>
    public int Mtu { get; private set; }=DefaultMtu;
    public const int DefaultMtu=23;

    readonly object gate=new();
    BluetoothLEAdvertisementWatcher? watcher;
    BluetoothLEDevice? device; GattSession? session; GattCharacteristic? write, notify;
    readonly List<GattDeviceService> services=new();
    ulong address; FisicaBleProfile profile=FisicaBleProfile.Scale;
    bool selected, ready; int generation;
    Task sending=Task.CompletedTask;

    static FisicaException Failure(string message) => new(1001,message);
    void Run(Func<Task> work) => _=Task.Run(async () => {
        try { await work().ConfigureAwait(false); }
        catch (Exception error) { Error?.Invoke(Failure("Bluetooth is unavailable: "+error.Message)); }
    });
    // Null when a powered Bluetooth LE radio is present, otherwise the reason.
    static async Task<string?> Unavailable() {
        var adapter=await BluetoothAdapter.GetDefaultAsync();
        if (adapter==null || !adapter.IsLowEnergySupported) return "Bluetooth LE is not available on this PC";
        var radio=await adapter.GetRadioAsync();
        return radio==null || radio.State!=RadioState.On ? "Bluetooth is switched off" : null;
    }

    /// <summary>Report advertising devices through Device. Filter by FisicaBleProfile.NameContains in the app.</summary>
    public void Scan() => Run(async () => {
        if (await Unavailable().ConfigureAwait(false) is {} problem) { Error?.Invoke(Failure(problem)); return; }
        lock(gate) {
            if (watcher==null) {
                watcher=new BluetoothLEAdvertisementWatcher { ScanningMode=BluetoothLEScanningMode.Active };
                watcher.Received+=(_,seen) => Device?.Invoke(seen.BluetoothAddress,seen.Advertisement.LocalName ?? "",seen.RawSignalStrengthInDBm);
                watcher.Stopped+=(_,stopped) => { if (stopped.Error!=BluetoothError.Success) Error?.Invoke(Failure($"BLE scan error {stopped.Error}")); };
            }
            if (watcher.Status!=BluetoothLEAdvertisementWatcherStatus.Started) watcher.Start();
        }
    });
    public void StopScan() {
        lock(gate) {
            try { if (watcher?.Status==BluetoothLEAdvertisementWatcherStatus.Started) watcher.Stop(); }
            catch (Exception) { /* Radio was switched off meanwhile. */ }
        }
    }
    /// <summary>Choose the Scale and its GATT profile for a later Open.</summary>
    public void Select(ulong bluetoothAddress,FisicaBleProfile? profile=null) {
        lock(gate) { address=bluetoothAddress; this.profile=profile ?? FisicaBleProfile.Scale; selected=true; }
    }

    /// <summary>Connect to the selected Scale. Ready follows once notifications are enabled.</summary>
    public void Open() => Run(async () => {
        int attempt; ulong target; FisicaBleProfile gatt;
        lock(gate) {
            if (!selected) attempt=-1; else attempt=++generation;
            target=address; gatt=profile;
        }
        if (attempt<0) { Error?.Invoke(Failure("Select a Scale and profile before opening")); Lost?.Invoke(); return; }
        StopScan(); Release();  // A reconnect starts from the default MTU with nothing queued.
        BluetoothLEDevice? connected=null; GattSession? gattSession=null; var found=new List<GattDeviceService>(); bool adopted=false;
        using var deadline=new CancellationTokenSource(ReadyTimeout);
        try {
            if (await Unavailable().ConfigureAwait(false) is {} problem) throw Failure(problem);
            connected=await BluetoothLEDevice.FromBluetoothAddressAsync(target).AsTask(deadline.Token).ConfigureAwait(false);
            if (connected==null) throw Failure("Scale not found");
            var discovered=await connected.GetGattServicesAsync(BluetoothCacheMode.Uncached).AsTask(deadline.Token).ConfigureAwait(false);
            if (discovered.Status!=GattCommunicationStatus.Success) throw Failure($"Service discovery error {discovered.Status}");
            found.AddRange(discovered.Services);
            Guid writeId=Guid.Parse(gatt.Write), notifyId=Guid.Parse(gatt.Notify); GattCharacteristic? output=null, input=null;
            foreach (var service in found) {
                var characteristics=await service.GetCharacteristicsAsync(BluetoothCacheMode.Uncached).AsTask(deadline.Token).ConfigureAwait(false);
                if (characteristics.Status!=GattCommunicationStatus.Success) continue;
                foreach (var characteristic in characteristics.Characteristics) {
                    if (characteristic.Uuid==writeId) output=characteristic;
                    if (characteristic.Uuid==notifyId) input=characteristic;
                }
            }
            // Every service was searched: report it instead of waiting forever.
            if (output==null || input==null) throw Failure("Scale characteristic UUIDs not found");
            var mode=input.CharacteristicProperties.HasFlag(GattCharacteristicProperties.Notify)
                ? GattClientCharacteristicConfigurationDescriptorValue.Notify : GattClientCharacteristicConfigurationDescriptorValue.Indicate;
            var enabled=await input.WriteClientCharacteristicConfigurationDescriptorAsync(mode).AsTask(deadline.Token).ConfigureAwait(false);
            if (enabled!=GattCommunicationStatus.Success) throw Failure($"Enable notifications failed: {enabled}");
            gattSession=await GattSession.FromDeviceIdAsync(connected.BluetoothDeviceId).AsTask(deadline.Token).ConfigureAwait(false);
            lock(gate) {
                if (attempt!=generation) return;  // Close or a newer Open took over.
                device=connected; session=gattSession; write=output; notify=input; services.AddRange(found);
                // Writes are split by the MTU Windows actually negotiated; unknown means the default.
                Mtu=Math.Max(DefaultMtu,(int)(gattSession?.MaxPduSize ?? DefaultMtu)); ready=true; adopted=true;
            }
            input.ValueChanged+=OnValue; connected.ConnectionStatusChanged+=OnStatus;
            if (gattSession!=null) { gattSession.MaintainConnection=true; gattSession.MaxPduSizeChanged+=OnMtu; }
            Ready?.Invoke();
        } catch (Exception error) {
            Error?.Invoke(error is OperationCanceledException ? Failure("BLE connection was not ready in time")
                : error as FisicaException ?? Failure("BLE connect failed: "+error.Message));
            bool current; lock(gate) current=attempt==generation;
            if (current) Lost?.Invoke();
        } finally {
            if (!adopted) { foreach (var service in found) service.Dispose(); gattSession?.Dispose(); connected?.Dispose(); }
        }
    });

    void OnValue(GattCharacteristic sender,GattValueChangedEventArgs change) => Bytes?.Invoke(change.CharacteristicValue.ToArray());
    void OnMtu(GattSession sender,object change) { lock(gate) { if (ReferenceEquals(sender,session)) Mtu=Math.Max(DefaultMtu,(int)sender.MaxPduSize); } }
    void OnStatus(BluetoothLEDevice sender,object change) {
        if (sender.ConnectionStatus!=BluetoothConnectionStatus.Disconnected) return;
        bool current; lock(gate) current=ready && ReferenceEquals(sender,device);
        if (current) { Release(); Lost?.Invoke(); }
    }

    public void Send(byte[] bytes) {
        GattCharacteristic? output; int chunk, attempt;
        lock(gate) { output=ready ? write : null; chunk=Mtu-3; attempt=generation; }
        if (output==null) { SendFailed?.Invoke(Failure("BLE is not ready")); return; }
        var option=output.CharacteristicProperties.HasFlag(GattCharacteristicProperties.Write) ? GattWriteOption.WriteWithResponse : GattWriteOption.WriteWithoutResponse;
        lock(gate) {
            // One write at a time, in order; a completed GATT write is not a command ACK.
            sending=sending.ContinueWith(async _ => {
                try {
                    for (int offset=0; offset<bytes.Length; offset+=chunk) {
                        if (attempt!=Volatile.Read(ref generation)) return;
                        var result=await output.WriteValueWithResultAsync(bytes.AsBuffer(offset,Math.Min(chunk,bytes.Length-offset)),option).AsTask().ConfigureAwait(false);
                        if (result.Status!=GattCommunicationStatus.Success) throw Failure($"BLE write error {result.Status}");
                    }
                } catch (Exception error) {
                    if (attempt==Volatile.Read(ref generation)) SendFailed?.Invoke(error as FisicaException ?? Failure("BLE write failed: "+error.Message));
                }
            },TaskScheduler.Default).Unwrap();
        }
    }

    public void Close() { lock(gate) generation++; StopScan(); Release(); }
    void Release() {
        BluetoothLEDevice? oldDevice; GattSession? oldSession; GattCharacteristic? oldNotify; GattDeviceService[] oldServices;
        lock(gate) {
            oldDevice=device; oldSession=session; oldNotify=notify; oldServices=services.ToArray();
            device=null; session=null; write=null; notify=null; services.Clear(); ready=false; Mtu=DefaultMtu; sending=Task.CompletedTask;
        }
        try {
            if (oldNotify!=null) oldNotify.ValueChanged-=OnValue;
            if (oldDevice!=null) oldDevice.ConnectionStatusChanged-=OnStatus;
            if (oldSession!=null) { oldSession.MaxPduSizeChanged-=OnMtu; oldSession.Dispose(); }
            foreach (var service in oldServices) service.Dispose();
            oldDevice?.Dispose();
        } catch (Exception) { /* Device already gone: nothing left to release. */ }
    }
}
#endif
